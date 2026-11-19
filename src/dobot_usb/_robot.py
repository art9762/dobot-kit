"""Synchronous commands for one Magician on an explicit USB port."""

from __future__ import annotations

import math
import struct
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from types import TracebackType

from .errors import DobotError, ExecutionTimeout, ProtocolError, RobotAlarmError
from .execution import CommandExecutor
from .transport import SerialTransport
from .validation import number

_POSITION = 10
_ALARMS = 20
_HOME = 31
_SUCTION = 62
_GRIPPER = 63
_SPEED = 83
_MOVE = 84
_LINEAR_XYZ = 2

_Coordinates = tuple[float, float, float, float]


class Dobot:
    """Connect to a classic DOBOT Magician by serial port, e.g. Dobot("COM3")."""

    def __init__(self, port: str) -> None:
        self._actions = threading.Lock()
        self._executor = CommandExecutor(SerialTransport(port, allow_writes=True))
        self._interrupted_session: str | None = None
        try:
            self.position()
        except BaseException:
            self.close()
            raise

    def position(self) -> _Coordinates:
        """Read and return (x, y, z, angle) in millimetres and degrees."""
        data = self._executor.read(_POSITION)
        if len(data) != 32:
            raise ProtocolError("Expected 32 bytes for the robot position")
        x, y, z, angle, j1, j2, j3, j4 = struct.unpack("<8f", data)
        if not all(math.isfinite(value) for value in (x, y, z, angle, j1, j2, j3, j4)):
            raise ProtocolError("The robot reported an invalid position")
        return x, y, z, angle

    @contextmanager
    def _action(self) -> Iterator[None]:
        # Hold the sequence across reads and waits so a concurrent stop interrupts
        # the whole action. Stop itself can run without waiting for this lock.
        with self._executor.sequence(), self._actions:
            session = self._executor.session_id
            if session == self._interrupted_session:
                raise DobotError(
                    "Check the robot and call stop() before continuing.",
                    stage="not_sent",
                )
            self._executor.prepare_queue()
            try:
                yield
            except DobotError as exc:
                if exc.stage == "accepted":
                    self._interrupted_session = session
                raise
            except KeyboardInterrupt:
                self._interrupted_session = session
                raise

    def _check_alarms(self) -> None:
        data = self._executor.read(_ALARMS)
        if len(data) != 16:
            raise ProtocolError("Expected 16 bytes for the robot alarm state")
        alarms = tuple(
            i * 8 + bit for i, byte in enumerate(data) for bit in range(8) if byte >> bit & 1
        )
        if alarms:
            raise RobotAlarmError(
                f"Robot has active alarms: {alarms}", alarms=alarms, stage="not_sent"
            )

    def move(self, x: float, y: float, z: float, angle: float | None = None) -> None:
        """Move straight to an absolute position. Omit angle to keep it as it is."""
        x, y, z = number(x, "x"), number(y, "y"), number(z, "z")
        if angle is not None:
            angle = number(angle, "angle")
        with self._action():
            self._check_alarms()
            target = (x, y, z, self.position()[3] if angle is None else angle)
            payload = struct.pack("<B4f", _LINEAR_XYZ, *target)
            handle = self._executor.submit(_MOVE, payload)
            self._confirm_position(target, handle.queue_index)

    def _confirm_position(self, target: _Coordinates, queue_index: int | None) -> None:
        def near(a: _Coordinates, b: _Coordinates) -> bool:
            return math.dist(a[:3], b[:3]) <= 0.3 and abs(a[3] - b[3]) <= 0.65

        deadline = time.monotonic() + 5
        previous = None
        try:
            while time.monotonic() < deadline:
                sample = self.position()
                if time.monotonic() >= deadline:
                    break
                self._check_alarms()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                if near(sample, target):
                    if previous is not None and near(sample, previous):
                        return
                    previous = sample
                else:
                    previous = None
                time.sleep(min(0.04, remaining))
            raise ExecutionTimeout(
                "The robot did not report reaching the target. Check the arm before continuing."
            )
        except DobotError as exc:
            exc.stage = "accepted"
            exc.command = _MOVE
            exc.queue_index = queue_index
            raise

    def home(self) -> None:
        """Run the controller's homing routine and wait for its queue."""
        with self._action():
            self._check_alarms()
            self._executor.submit(_HOME, struct.pack("<I", 0), timeout=60)

    def speed(self, percent: float) -> None:
        """Set the speed and acceleration ratios for point-to-point motion (1–100)."""
        percent = number(percent, "percent", minimum=1)
        if percent > 100:
            raise ValueError("percent must be at most 100")
        with self._action():
            self._executor.submit(_SPEED, struct.pack("<2f", percent, percent))

    def suction_on(self) -> None:
        """Turn on the suction cup."""
        with self._action():
            self._executor.submit(_SUCTION, b"\x01\x01")

    def suction_off(self) -> None:
        """Turn off the suction cup."""
        with self._action():
            self._executor.submit(_SUCTION, b"\x00\x00")

    def gripper_open(self) -> None:
        """Open the pneumatic gripper."""
        with self._action():
            self._executor.submit(_GRIPPER, b"\x01\x00")

    def gripper_close(self) -> None:
        """Close the pneumatic gripper."""
        with self._action():
            self._executor.submit(_GRIPPER, b"\x01\x01")

    def gripper_off(self) -> None:
        """Disable the gripper output."""
        with self._action():
            self._executor.submit(_GRIPPER, b"\x00\x00")

    def stop(self) -> None:
        """Force-stop motion and clear pending commands. Tool outputs stay as they are."""
        self._executor.stop()

    def close(self) -> None:
        """Disconnect from the USB port."""
        self._executor.close()

    def __enter__(self) -> Dobot:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()
