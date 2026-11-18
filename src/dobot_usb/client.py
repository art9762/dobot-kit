"""Small synchronous query API for the classic Magician."""

import math
import struct
from types import TracebackType
from typing import cast

from .backend import CommandBackend
from .commands import COMMANDS, CommandSpec
from .conveyor import Conveyor
from .errors import ConnectionError, ProtocolError, RobotAlarmError, UnsupportedCommand
from .execution import CommandExecutor, CommandHandle
from .models import DeviceInfo, Point, Pose, Status, ToolState
from .motion import Motion
from .pen import Pen
from .peripherals import IO, Motors, Network, Rail, Sensors, Settings, Teaching
from .queue import Queue
from .tools import Laser, Tool
from .transfer import GripperHead, SuctionHead
from .transport import BOOTSTRAP_QUERIES, SerialTransport, Transport
from .validation import options


class Advanced:
    """Named, validated protocol operations and legacy raw bootstrap queries."""

    def __init__(self, backend: CommandBackend, queue: Queue) -> None:
        self._backend = backend
        self._queue = queue

    def read(self, command: int) -> bytes:
        """Return unparsed query bytes for an explicitly supported command ID."""
        if type(command) is not int or command not in BOOTSTRAP_QUERIES:
            raise UnsupportedCommand(f"Query command {command!r} is not enabled in this release")
        return self._backend.executor.read(command)

    def get(self, name: str, **params: object) -> dict[str, object]:
        """Read a named command, validating addressed queries and reply fields."""
        return self._backend.read(name, **params)

    def set(
        self,
        name: str,
        /,
        *,
        wait: bool = True,
        timeout: float = 30.0,
        queued: bool | None = None,
        **params: object,
    ) -> CommandHandle | None:
        """Write a named command. Queue lifecycle operations invalidate old receipts."""
        options(wait, timeout)
        lifecycle = {
            "queue_start": lambda: self._queue.start(timeout=timeout),
            "queue_stop": lambda: self._queue.pause(timeout=timeout),
            "queue_force_stop": lambda: self._queue.stop(clear=False, timeout=timeout),
            "queue_clear": lambda: self._queue.clear(timeout=timeout),
        }
        spec = self.describe(name)
        if name in lifecycle:
            spec.encode_set(**params)
            if queued is not None and (type(queued) is not bool or queued):
                raise ValueError("Queue lifecycle commands cannot be queued")
            lifecycle[name]()
            return None
        return self._backend.write(name, wait=wait, timeout=timeout, queued=queued, **params)

    def describe(self, name: str) -> CommandSpec:
        try:
            return COMMANDS[name]
        except (KeyError, TypeError) as exc:
            raise UnsupportedCommand(f"Unknown command: {name!r}", stage="not_sent") from exc

    @property
    def commands(self) -> tuple[str, ...]:
        return tuple(COMMANDS)


class Dobot:
    """A classic Magician connected through a protocol transport.

    Construction with a custom Transport does not perform a handshake. Use connect() for
    a serial connection. Neither path sends motion or changes controller state.
    """

    def __init__(self, transport: Transport) -> None:
        self._transport = transport
        self._executor = CommandExecutor(transport)
        self._backend = CommandBackend(self._executor, self._ready)
        self.suction = SuctionHead(self._backend, self)
        self.gripper = GripperHead(self._backend, self)
        self.laser = Laser(self._backend, is_session_active=lambda: not self._executor.closed)
        self.pen = Pen(self)
        self.tool = Tool(self._backend)
        self.io = IO(self._backend)
        self.motors = Motors(self._backend)
        self.conveyor = Conveyor(self._backend)
        self.sensors = Sensors(self._backend)
        self.rail = Rail(self._backend)
        self.teaching = Teaching(self._backend)
        self.settings = Settings(self._backend)
        self.network = Network(self._backend)
        self.queue = Queue(self._backend, on_invalidate=self.laser.invalidate)
        self.advanced = Advanced(self._backend, self.queue)
        self.motion = Motion(
            self._backend,
            self.pose,
            self._executor.synchronize,
            self._ready,
            sequence=self._executor.sequence,
        )

    @classmethod
    def connect(cls, port: str, *, timeout: float = 1.0, read_only: bool = False) -> "Dobot":
        """Open an explicit port and verify version/pose/alarms using queries only.

        The port must be released by DobotStudio, DobotLink or the existing local panel.
        The handshake checks reply shapes, not cryptographic identity or tool presence.
        """
        if type(read_only) is not bool:
            raise ValueError("read_only must be bool")
        bot = cls(SerialTransport(port, timeout=timeout, allow_writes=not read_only))
        try:
            bot.status()
        except BaseException:
            bot.close()
            raise
        return bot

    def _ready(self) -> None:
        alarms = self.alarms()
        if alarms:
            raise RobotAlarmError(
                "Controller alarms active before motion", alarms=alarms, stage="not_sent"
            )

    def move_to(
        self,
        x: float | Point | Pose,
        y: float | None = None,
        z: float | None = None,
        r: float | None = None,
        *,
        path: str = "linear",
        wait: bool = True,
        timeout: float = 30.0,
    ) -> CommandHandle:
        """Move to XYZ or a saved Point/Pose, including its tool rotation."""
        return self.motion.move_to(x, y, z, r, path=path, wait=wait, timeout=timeout)

    def move_by(
        self,
        *,
        x: float = 0,
        y: float = 0,
        z: float = 0,
        r: float = 0,
        path: str = "linear",
        wait: bool = True,
        timeout: float = 30.0,
    ) -> CommandHandle:
        return self.motion.move_by(x=x, y=y, z=z, r=r, path=path, wait=wait, timeout=timeout)

    def move_joints(
        self,
        j1: float,
        j2: float,
        j3: float,
        j4: float,
        *,
        relative: bool = False,
        wait: bool = True,
        timeout: float = 30.0,
    ) -> CommandHandle:
        return self.motion.move_joints(
            j1, j2, j3, j4, relative=relative, wait=wait, timeout=timeout
        )

    def home(self, *, wait: bool = True, timeout: float = 60.0) -> CommandHandle:
        return self.motion.home(wait=wait, timeout=timeout)

    def speed(
        self,
        *,
        mm_s: float,
        mm_s2: float,
        deg_s: float | None = None,
        deg_s2: float | None = None,
    ) -> CommandHandle:
        return self.motion.speed(mm_s=mm_s, mm_s2=mm_s2, deg_s=deg_s, deg_s2=deg_s2)

    def stop(self, *, clear: bool = True, timeout: float = 30.0) -> None:
        self.queue.stop(clear=clear, timeout=timeout)

    def pose(self) -> Pose:
        data = self.advanced.read(10)
        if len(data) != 32:
            raise ProtocolError(f"Pose must contain 32 bytes, got {len(data)}")
        x, y, z, r, j1, j2, j3, j4 = struct.unpack("<8f", data)
        if not all(math.isfinite(v) for v in (x, y, z, r, j1, j2, j3, j4)):
            raise ProtocolError("Pose contains a non-finite value")
        return Pose(x, y, z, r, (j1, j2, j3, j4))

    def alarms(self) -> tuple[int, ...]:
        data = self.advanced.read(20)
        if len(data) != 16:
            raise ProtocolError(f"Alarm state must contain 16 bytes, got {len(data)}")
        return tuple(
            i * 8 + bit for i, byte in enumerate(data) for bit in range(8) if byte >> bit & 1
        )

    def device_info(self) -> DeviceInfo:
        data = self.advanced.read(2)
        # V1.1.5 specifies 3 bytes. Magician 3.7.0 and 4.0.3 also use 14-byte replies.
        # Preserve all extended fields without guessing their meaning.
        if len(data) not in (3, 14):
            raise ProtocolError(f"Unsupported firmware version reply length: {len(data)}")
        return DeviceInfo(
            firmware=".".join(str(v) for v in data[:3]),
            firmware_raw=data,
            serial_number=cast(str | None, self.advanced.get("device_sn")["serial_number"]),
            name=cast(str | None, self.advanced.get("device_name")["name"]),
        )

    def status(self) -> Status:
        """Read identity, pose and alarms in sequence; does not probe attached tools."""
        return Status(self.device_info(), self.pose(), self.alarms())

    def tool_state(self, tool: str) -> ToolState:
        """Read controller outputs, not physical tool presence or grasp success."""
        commands = {"laser": 61, "suction": 62, "gripper": 63}
        if tool not in commands:
            raise ValueError("tool must be 'laser', 'suction' or 'gripper'")
        data = self.advanced.read(commands[tool])
        if len(data) != 2 or any(v not in (0, 1) for v in data):
            raise ProtocolError("Tool state must contain two boolean bytes")
        return ToolState(bool(data[0]), bool(data[1]))

    def queue_index(self) -> int:
        """Read controller execution index; this alone does not prove task completion."""
        data = self.advanced.read(246)
        if len(data) != 8:
            raise ProtocolError(f"Queue index must contain 8 bytes, got {len(data)}")
        return int(struct.unpack("<Q", data)[0])

    def close(self) -> None:
        """Release the connection; does not stop motion owned by another program."""
        self.laser.invalidate()
        self._executor.close()

    def __enter__(self) -> "Dobot":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()


__all__ = ["Advanced", "Dobot", "ConnectionError"]
