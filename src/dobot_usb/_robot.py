"""Synchronous commands for one Magician on an explicit USB port."""

from __future__ import annotations

import math
import struct
import threading
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from types import TracebackType

from .errors import DobotError, ExecutionTimeout, ProtocolError, RobotAlarmError
from .execution import CommandExecutor
from .geometry import circle_path, coordinates, planar_path, positive
from .transport import SerialTransport
from .validation import number

_POSITION = 10
_ALARMS = 20
_HOME = 31
_SUCTION = 62
_GRIPPER = 63
_SPEED = 83
_MOVE = 84
_COLOR_SENSOR = 137
_LINEAR_XYZ = 2

_Coordinates = tuple[float, float, float, float]


class Dobot:
    """Connect to a classic DOBOT Magician by serial port, e.g. Dobot("COM3")."""

    def __init__(self, port: str) -> None:
        self._actions = threading.Lock()
        self._executor = CommandExecutor(SerialTransport(port, allow_writes=True))
        self._interrupted_session: str | None = None
        self._origin: _Coordinates | None = None
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
            target = (x, y, z, self.position()[3] if angle is None else angle)
            self._move_to(target)

    def _move_to(self, target: _Coordinates) -> None:
        """Execute inside one _action(), including a multi-segment path."""
        self._check_alarms()
        payload = struct.pack("<B4f", _LINEAR_XYZ, *target)
        handle = self._executor.submit(_MOVE, payload)
        self._confirm_position(target, handle.queue_index)

    @property
    def origin(self) -> _Coordinates | None:
        """Saved calibration in this Python object, or None. Does not read USB."""
        return self._origin

    def calibrate(self, position: Sequence[float] | None = None) -> _Coordinates:
        """Remember a pose without motion, homing or changing controller coordinates.

        Three coordinates preserve the current angle. Calibration is not persistent.
        """
        values = None if position is None else coordinates(position)
        with self._executor.sequence(), self._actions:
            if values is None:
                target = self.position()
            else:
                angle = values[3] if len(values) == 4 else self.position()[3]
                target = (values[0], values[1], values[2], angle)
            self._origin = target
            return target

    def _require_origin(self) -> _Coordinates:
        if self._origin is None:
            raise ValueError("Call calibrate() before moving relative to the saved origin")
        return self._origin

    def move_relative(self, dx: float = 0, dy: float = 0, dz: float = 0, dangle: float = 0) -> None:
        """Move to origin + offsets (mm/degrees), NOT current pose + offsets."""
        offsets = tuple(
            number(v, n)
            for v, n in zip((dx, dy, dz, dangle), ("dx", "dy", "dz", "dangle"), strict=True)
        )
        origin = self._require_origin()
        target = tuple(number(a + b, "target") for a, b in zip(origin, offsets, strict=True))
        with self._action():
            self._move_to((target[0], target[1], target[2], target[3]))

    def _move_axis(self, axis: int, offset: float) -> None:
        offset = number(offset, "offset")
        value = number(self._require_origin()[axis] + offset, "target")
        self._set_axis(axis, value)

    def _set_axis(self, axis: int, value: float) -> None:
        value = number(value, "position")
        with self._action():
            target = list(self.position())
            target[axis] = value
            self._move_to((target[0], target[1], target[2], target[3]))

    def set_x(self, x: float) -> None:
        """Move to absolute X in mm; keep current Y, Z and angle. No calibration needed."""
        self._set_axis(0, x)

    def set_y(self, y: float) -> None:
        """Move to absolute Y in mm; keep current X, Z and angle. No calibration needed."""
        self._set_axis(1, y)

    def set_z(self, z: float) -> None:
        """Move to absolute Z in mm; keep current X, Y and angle. No calibration needed."""
        self._set_axis(2, z)

    def set_angle(self, angle: float) -> None:
        """Move to an absolute angle in degrees; keep current XYZ. No calibration needed."""
        self._set_axis(3, angle)

    def move_x(self, offset: float) -> None:
        """Set X to origin X + offset mm; keep other current coordinates."""
        self._move_axis(0, offset)

    def move_y(self, offset: float) -> None:
        """Set Y to origin Y + offset mm; keep other current coordinates."""
        self._move_axis(1, offset)

    def move_z(self, offset: float) -> None:
        """Set Z to origin Z + offset mm; keep other current coordinates."""
        self._move_axis(2, offset)

    def move_angle(self, offset: float) -> None:
        """Set angle to origin angle + offset degrees; keep current XYZ."""
        self._move_axis(3, offset)

    def _draw(self, points: Sequence[Sequence[float]]) -> None:
        # Validate the ENTIRE path before preparing the queue or moving.
        path = [coordinates(point, "path point") for point in points]
        with self._action():
            angle = self.position()[3] if any(len(point) == 3 for point in path) else 0.0
            for point in path:
                self._move_to(
                    (point[0], point[1], point[2], point[3] if len(point) == 4 else angle)
                )

    def draw_line(self, start: Sequence[float], end: Sequence[float]) -> None:
        """Move straight to start, then end. No pen lift or tool switching."""
        self._draw([start, end])

    def draw_rectangle(
        self, start: Sequence[float], width: float, height: float, *, plane: str = "xy"
    ) -> None:
        """Closed rectangle from its minimum-axis corner, dimensions in mm."""
        width, height = positive(width, "width"), positive(height, "height")
        self._draw(
            planar_path(start, [(0, 0), (width, 0), (width, height), (0, height), (0, 0)], plane)
        )

    def draw_square(self, start: Sequence[float], side: float, *, plane: str = "xy") -> None:
        """Closed square; start and plane have the same meaning as for rectangle."""
        self.draw_rectangle(start, side, side, plane=plane)

    def draw_triangle(self, start: Sequence[float], side: float, *, plane: str = "xy") -> None:
        """Equilateral triangle, first side along the first plane axis, in mm."""
        side = positive(side, "side")
        self._draw(
            planar_path(
                start, [(0, 0), (side, 0), (side / 2, side * math.sqrt(3) / 2), (0, 0)], plane
            )
        )

    def draw_circle(
        self, start: Sequence[float], radius: float, *, segments: int = 72, plane: str = "xy"
    ) -> None:
        """Closed polygonal circle in mm, starting ON the circumference.

        Centre is start minus radius along the first plane axis. No pen lift.
        """
        self._draw(circle_path(start, radius, segments, plane))

    def color_sensor(self, enabled: bool = True, *, port: str, version: int) -> None:
        """Configure the classic Dobot color sensor, without touching the motion queue.

        port: GP1/GP2/GP4/GP5. version: sensor generation 1 or 2 (wire flags 0/1).
        Both must be chosen for the physically connected sensor. Returns on ACK,
        not proof of sensing, freshness or compatibility. No automatic fallback.
        """
        if type(enabled) is not bool:
            raise ValueError("enabled must be bool")
        ports = {"GP1": 0, "GP2": 1, "GP4": 2, "GP5": 3}
        if not isinstance(port, str) or port not in ports:
            raise ValueError("port must be 'GP1', 'GP2', 'GP4' or 'GP5'")
        if type(version) is not int or version not in (1, 2):
            raise ValueError("version must be sensor generation 1 or 2")
        payload = bytes((int(enabled), ports[port], version - 1))
        with self._executor.sequence(), self._actions:
            self._executor.submit(_COLOR_SENSOR, payload, queued=False, wait=False)

    def read_color(self) -> tuple[int, int, int]:
        """Read the controller's (R, G, B) uint8 report, without moving the arm.

        A valid report alone cannot establish sensor presence or fresh acquisition.
        Configure the physically connected sensor with color_sensor() first.
        """
        data = self._executor.read(_COLOR_SENSOR)
        if len(data) != 3:
            raise ProtocolError("Expected exactly three bytes for the color sensor RGB report")
        return data[0], data[1], data[2]

    def watch_color(
        self,
        handler: Callable[[tuple[int, int, int]], bool | None],
        *,
        interval: float = 0.1,
        stop_event: threading.Event | None = None,
        changes_only: bool = True,
    ) -> None:
        """Poll synchronously and call handler(rgb), initially and on RGB changes.

        Return False from handler, set stop_event or press Ctrl+C to leave.
        No background thread, implicit sensor configuration, retries or tool actions.
        Callback/USB exceptions propagate. stop() stops motion, not this polling loop.
        """
        if not callable(handler):
            raise ValueError("handler must be callable")
        interval = number(interval, "interval", minimum=0.01)
        if interval > 60:
            raise ValueError("interval must be at most 60 seconds")
        if type(changes_only) is not bool:
            raise ValueError("changes_only must be bool")
        if stop_event is not None and not isinstance(stop_event, threading.Event):
            raise ValueError("stop_event must be threading.Event or None")
        event = stop_event if stop_event is not None else threading.Event()
        previous = None
        while not event.is_set():
            rgb = self.read_color()
            if event.is_set():
                return
            if not changes_only or rgb != previous:
                if handler(rgb) is False:
                    return
                previous = rgb
            if event.wait(interval):
                return

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

    def suck(self, enabled: bool) -> None:
        """Turn the suction cup on (True) or off (False)."""
        if type(enabled) is not bool:
            raise ValueError("enabled must be bool")
        with self._action():
            self._executor.submit(_SUCTION, bytes((enabled, enabled)))

    def suction_on(self) -> None:
        """Turn on the suction cup."""
        self.suck(True)

    def suction_off(self) -> None:
        """Turn off the suction cup."""
        self.suck(False)

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

    def clear(self) -> None:
        """Reset controller alarm flags after correcting the cause and verify the result.

        Call stop() first if a motion was interrupted. Raises RobotAlarmError
        with the remaining codes if the controller still reports active flags.
        """
        with self._action():
            receipt = self._executor.submit(_ALARMS, queued=False, wait=False)
            try:
                self._check_alarms()
            except DobotError as exc:
                # The reset was acknowledged before this verification read failed.
                exc.stage = "accepted"
                exc.command = _ALARMS
                exc.session_id = receipt.session_id
                raise

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
