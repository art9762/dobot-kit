"""Classic Magician motion, in millimetres, degrees and seconds."""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from contextlib import AbstractContextManager, nullcontext
from enum import IntEnum
from typing import TYPE_CHECKING

from .errors import ExecutionTimeout
from .models import Point, Pose
from .validation import number, options

if TYPE_CHECKING:
    from .execution import CommandHandle
    from .interfaces import Backend


class PTPMode(IntEnum):
    """Wire enum from protocol V1.1.5, Program 17 (including mode 9)."""

    JUMP_XYZ = 0
    MOVJ_XYZ = 1
    MOVL_XYZ = 2
    JUMP_ANGLE = 3
    MOVJ_ANGLE = 4
    MOVL_ANGLE = 5
    MOVJ_INC = 6
    MOVL_INC = 7
    MOVJ_XYZ_INC = 8
    JUMP_MOVL_XYZ = 9


def validated_point(point: Point, name: str = "point") -> Point:
    """Copy and validate all coordinates without IO."""
    if not isinstance(point, Point):
        raise ValueError(f"{name} must be Point")
    return Point(*(number(getattr(point, axis), f"{name}.{axis}") for axis in "xyzr"))


def _path(path: str, *, relative: bool = False) -> PTPMode:
    modes = (
        {"linear": PTPMode.MOVL_INC, "joint": PTPMode.MOVJ_XYZ_INC}
        if relative
        else {"linear": PTPMode.MOVL_XYZ, "joint": PTPMode.MOVJ_XYZ, "jump": PTPMode.JUMP_XYZ}
    )
    if not isinstance(path, str) or path not in modes:
        raise ValueError(f"path must be one of {tuple(modes)}")
    return modes[path]


class Motion:
    """Motion group sharing one backend and queue owner with the facade.

    Numeric checks do not establish reachability, collision clearance or physical completion.
    """

    def __init__(
        self,
        backend: Backend,
        pose: Callable[[], Pose],
        synchronize: Callable[[float], None],
        ready: Callable[[], None],
        *,
        sequence: Callable[[], AbstractContextManager[None]] = nullcontext,
    ) -> None:
        self._backend = backend
        self._pose = pose
        self._synchronize = synchronize
        self._ready = ready
        self._sequence = sequence
        self._jump_configured = False

    def sequence(self) -> AbstractContextManager[None]:
        """Keep a multi-step action bound to one uninterrupted queue generation."""
        return self._sequence()

    def wait_for_pose(
        self,
        target: Point,
        *,
        timeout: float = 5.0,
        linear_tolerance_mm: float = 0.3,
        angular_tolerance_deg: float = 0.65,
    ) -> Pose:
        """Return the second of two matching, alarm-free controller pose samples.

        Both samples must match the target and one another in Euclidean XYZ
        distance and absolute reported R difference. Samples are separated by
        40 ms. This observation sends only queries and has no queue semantics.
        Each in-flight query remains subject to its transport timeout.
        """
        options(True, timeout)
        target = validated_point(target, "target")
        linear = number(linear_tolerance_mm, "linear_tolerance_mm", minimum=0)
        angular = number(angular_tolerance_deg, "angular_tolerance_deg", minimum=0)

        def matches(sample: Pose, other: Point | Pose) -> bool:
            distance = math.dist((sample.x, sample.y, sample.z), (other.x, other.y, other.z))
            return distance <= linear and abs(sample.r - other.r) <= angular

        deadline = time.monotonic() + timeout
        previous: Pose | None = None
        while time.monotonic() < deadline:
            sample = self._pose()
            if time.monotonic() >= deadline:
                break
            self._ready()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            if matches(sample, target):
                if previous is not None and matches(sample, previous):
                    return sample
                previous = sample
            else:
                previous = None
            time.sleep(min(0.04, remaining))
        raise ExecutionTimeout(f"Timed out observing two matching pose samples for {target!r}")

    def ptp(
        self,
        mode: PTPMode | int,
        x: float,
        y: float,
        z: float,
        r: float,
        *,
        wait: bool = True,
        timeout: float = 30,
    ) -> CommandHandle:
        """Explicit wire-mode access; coordinate meaning depends on mode."""
        options(wait, timeout)
        if isinstance(mode, bool) or not isinstance(mode, int):
            raise ValueError("mode must be an integer PTPMode (0..9)")
        try:
            mode = PTPMode(mode)
        except ValueError as exc:
            raise ValueError("mode must be an integer PTPMode (0..9)") from exc
        point = validated_point(Point(x, y, z, r))
        if mode in (PTPMode.JUMP_XYZ, PTPMode.JUMP_ANGLE, PTPMode.JUMP_MOVL_XYZ):
            if not self._jump_configured:
                raise ValueError("configure jump(height=..., z_limit=...) before JUMP")
        with self.sequence():
            self._ready()
            return self._backend.write(
                "ptp", mode=int(mode), **vars(point), wait=wait, timeout=timeout
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
        timeout: float = 30,
    ) -> CommandHandle:
        options(wait, timeout)
        mode = _path(path)
        if isinstance(x, (Point, Pose)):
            if y is not None or z is not None or r is not None:
                raise TypeError("pass a Point/Pose by itself, or give x, y, z and optional r")
            saved = x
            x, y, z, r = saved.x, saved.y, saved.z, number(saved.r, "target.r")
        if y is None or z is None:
            raise TypeError("move_to needs x, y and z, or a Point/Pose")
        point = validated_point(Point(x, y, z, 0 if r is None else r))
        if mode == PTPMode.JUMP_XYZ and not self._jump_configured:
            raise ValueError("configure jump(height=..., z_limit=...) before JUMP")
        with self.sequence():
            if r is None:
                if not wait:
                    raise ValueError(
                        "wait=False requires explicit r; current pose may lag queued targets"
                    )
                self._synchronize(timeout)
                r = number(self._pose().r, "pose.r")
            else:
                r = point.r
            return self.ptp(mode, point.x, point.y, point.z, r, wait=wait, timeout=timeout)

    def move_by(
        self,
        *,
        x: float = 0,
        y: float = 0,
        z: float = 0,
        r: float = 0,
        path: str = "linear",
        wait: bool = True,
        timeout: float = 30,
    ) -> CommandHandle:
        return self.ptp(_path(path, relative=True), x, y, z, r, wait=wait, timeout=timeout)

    def move_joints(
        self,
        j1: float,
        j2: float,
        j3: float,
        j4: float,
        *,
        relative: bool = False,
        wait: bool = True,
        timeout: float = 30,
    ) -> CommandHandle:
        if type(relative) is not bool:
            raise ValueError("relative must be bool")
        mode = PTPMode.MOVJ_INC if relative else PTPMode.MOVJ_ANGLE
        return self.ptp(mode, j1, j2, j3, j4, wait=wait, timeout=timeout)

    def home(self, *, wait: bool = True, timeout: float = 60) -> CommandHandle:
        options(wait, timeout)
        self._ready()
        return self._backend.write("home", reserved=0, wait=wait, timeout=timeout)

    def speed(
        self,
        *,
        mm_s: float,
        mm_s2: float,
        deg_s: float | None = None,
        deg_s2: float | None = None,
    ) -> CommandHandle:
        """Set nominal PTP Cartesian rates; existing percentage ratios still apply."""
        velocity = number(mm_s, "mm_s", minimum=0)
        acceleration = number(mm_s2, "mm_s2", minimum=0)
        rotation = None if deg_s is None else number(deg_s, "deg_s", minimum=0)
        rotation_acc = None if deg_s2 is None else number(deg_s2, "deg_s2", minimum=0)
        if rotation is None or rotation_acc is None:
            current = self._backend.read("ptp_coordinate_params")
            if rotation is None:
                rotation = number(current["r_velocity"], "r_velocity", minimum=0)
            if rotation_acc is None:
                rotation_acc = number(current["r_acceleration"], "r_acceleration", minimum=0)
        self._ready()
        return self._backend.write(
            "ptp_coordinate_params",
            xyz_velocity=velocity,
            r_velocity=rotation,
            xyz_acceleration=acceleration,
            r_acceleration=rotation_acc,
        )

    def jump(self, *, height: float, z_limit: float) -> CommandHandle:
        """Explicit JUMP lift distance and absolute maximum Z, both in mm."""
        height = number(height, "height", minimum=0)
        z_limit = number(z_limit, "z_limit")
        self._ready()
        # Revoke on any uncertain update; no old configuration is silently trusted.
        self._jump_configured = False
        handle = self._backend.write("ptp_jump_params", jump_height=height, z_limit=z_limit)
        self._jump_configured = True
        return handle

    def arc(
        self, via: Point, to: Point, *, wait: bool = True, timeout: float = 30
    ) -> CommandHandle:
        options(wait, timeout)
        via = validated_point(via, "via")
        to = validated_point(to, "to")
        self._ready()
        return self._backend.write(
            "arc", cir_point=vars(via), to_point=vars(to), wait=wait, timeout=timeout
        )

    def _line(
        self,
        mode: int,
        x: float,
        y: float,
        z: float,
        velocity: float,
        wait: bool,
        timeout: float,
    ) -> CommandHandle:
        options(wait, timeout)
        point = validated_point(Point(x, y, z, 0))
        velocity = number(velocity, "velocity", minimum=0)
        if velocity != 0:
            raise ValueError("CP velocity is reserved in V1.1.5; configure cp_params instead")
        self._ready()
        return self._backend.write(
            "cp",
            mode=mode,
            x=point.x,
            y=point.y,
            z=point.z,
            power=velocity,
            wait=wait,
            timeout=timeout,
        )

    def line_to(
        self,
        x: float,
        y: float,
        z: float,
        *,
        velocity: float = 0,
        wait: bool = True,
        timeout: float = 30,
    ) -> CommandHandle:
        """Absolute CP point, preserving R; configure CP rates through cp_params."""
        return self._line(1, x, y, z, velocity, wait, timeout)

    def line_by(
        self,
        *,
        x: float = 0,
        y: float = 0,
        z: float = 0,
        velocity: float = 0,
        wait: bool = True,
        timeout: float = 30,
    ) -> CommandHandle:
        """Relative CP point; no read/absolute conversion or automatic CP setup."""
        return self._line(0, x, y, z, velocity, wait, timeout)

    def jog(self, axis: str, direction: int, *, duration: float, timeout: float = 30) -> None:
        """Jog for 0 < duration <= 5 seconds, then attempt an immediate stop.

        This host timer is not a controller watchdog. A broken link can prevent stopping.
        Timeout applies separately to synchronization and each request, not overall travel.
        """
        options(True, timeout)
        duration = number(duration, "duration", minimum=0)
        if duration == 0 or duration > 5:
            raise ValueError("duration must be greater than 0 and at most 5 seconds")
        axes = {
            "x": (False, 1),
            "y": (False, 3),
            "z": (False, 5),
            "r": (False, 7),
            "j1": (True, 1),
            "j2": (True, 3),
            "j3": (True, 5),
            "j4": (True, 7),
        }
        if not isinstance(axis, str) or axis not in axes:
            raise ValueError("axis must be x/y/z/r or j1/j2/j3/j4")
        if type(direction) is not int or direction not in (-1, 1):
            raise ValueError("direction must be +1 or -1")
        is_joint, cmd = axes[axis]
        self._synchronize(timeout)
        self._ready()
        try:
            self._backend.write(
                "jog",
                is_joint=is_joint,
                cmd=cmd + (direction == -1),
                queued=False,
                wait=True,
                timeout=timeout,
            )
            time.sleep(duration)
        finally:
            # Also attempted after an uncertain start. Stop bypasses alarm checks.
            self.jog_stop(is_joint=is_joint, timeout=timeout)

    def jog_stop(self, *, is_joint: bool = False, timeout: float = 30) -> CommandHandle:
        """Explicit immediate JOG idle request, even if alarms are present."""
        options(True, timeout)
        if type(is_joint) is not bool:
            raise ValueError("is_joint must be bool")
        return self._backend.write(
            "jog", is_joint=is_joint, cmd=0, queued=False, wait=True, timeout=timeout
        )
