"""A calibrated conveyor adapter over controller motor steps per second."""

from .execution import CommandHandle
from .interfaces import Backend
from .validation import number, options


class Conveyor:
    def __init__(self, backend: Backend) -> None:
        self._backend = backend
        self._index: int | None = None
        self._steps_per_mm: float | None = None

    def configure(self, *, index: int, steps_per_mm: float) -> None:
        """Store measured calibration locally; never turn a motor on."""
        if type(index) is not int or index not in (0, 1):
            raise ValueError("index must be 0 or 1")
        calibration = number(steps_per_mm, "steps_per_mm", minimum=0)
        if calibration == 0:
            raise ValueError("steps_per_mm must be positive")
        self._index = index
        self._steps_per_mm = calibration

    def run(self, mm_s: float, *, wait: bool = True, timeout: float = 30.0) -> CommandHandle:
        options(wait, timeout)
        velocity = number(mm_s, "mm_s")
        if self._index is None or self._steps_per_mm is None:
            raise ValueError("configure(index=..., steps_per_mm=...) before conveyor motion")
        raw_speed = velocity * self._steps_per_mm
        if not -(2**31) <= raw_speed <= 2**31 - 1:
            raise ValueError("calibrated speed does not fit int32 steps/s")
        return self._backend.write(
            "external_motor",
            index=self._index,
            enabled=True,
            speed=round(raw_speed),
            wait=wait,
            timeout=timeout,
        )

    def stop(self, *, wait: bool = True, timeout: float = 30.0) -> CommandHandle:
        options(wait, timeout)
        if self._index is None:
            raise ValueError("configure conveyor motor index before stopping")
        return self._backend.write(
            "external_motor",
            index=self._index,
            enabled=False,
            speed=0,
            wait=wait,
            timeout=timeout,
        )
