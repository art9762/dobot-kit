"""Small immutable point routes; no serial IO during construction or preview."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .models import Point, Pose
from .motion import validated_point
from .validation import options

if TYPE_CHECKING:
    from .execution import CommandHandle
    from .motion import Motion


@dataclass(frozen=True)
class Bounds:
    """Inclusive coordinate bounds, not reachability or collision constraints."""

    minimum: Point
    maximum: Point

    def __post_init__(self) -> None:
        object.__setattr__(self, "minimum", validated_point(self.minimum, "minimum"))
        object.__setattr__(self, "maximum", validated_point(self.maximum, "maximum"))
        if any(getattr(self.minimum, axis) > getattr(self.maximum, axis) for axis in "xyzr"):
            raise ValueError("minimum must not exceed maximum on any axis")

    def contains(self, point: Point) -> bool:
        point = validated_point(point)
        return all(
            getattr(self.minimum, axis) <= getattr(point, axis) <= getattr(self.maximum, axis)
            for axis in "xyzr"
        )


@dataclass(frozen=True)
class Preview:
    points: tuple[Point, ...]
    bounds: Bounds | None


def _waypoint(point: Point | Pose) -> Point:
    if isinstance(point, Pose):
        point = Point(point.x, point.y, point.z, point.r)
    return validated_point(point)


@dataclass(frozen=True, init=False)
class Path:
    """Validated absolute linear PTP route, including the first approach point.

    No automatic pen lift/lower, homing, tool actions or return-to-start are inserted.
    """

    points: tuple[Point, ...]

    def __init__(self, points: Iterable[Point | Pose] = ()) -> None:
        object.__setattr__(self, "points", tuple(_waypoint(point) for point in points))

    def then(self, point: Point | Pose) -> Path:
        """Return a new path with one explicit XYZ/R point appended."""
        return Path((*self.points, point))

    def preview(self, bounds: Bounds | None = None) -> Preview:
        """Offline extent and optional bounds validation of every target."""
        if bounds is not None and not isinstance(bounds, Bounds):
            raise ValueError("bounds must be Bounds")
        points = tuple(validated_point(point) for point in self.points)
        for index, point in enumerate(points):
            if bounds is not None and not bounds.contains(point):
                raise ValueError(f"point {index} is outside bounds")
        extent = None
        if points:
            extent = Bounds(
                Point(*(min(getattr(p, axis) for p in points) for axis in "xyzr")),
                Point(*(max(getattr(p, axis) for p in points) for axis in "xyzr")),
            )
        return Preview(points, extent)

    def run(
        self,
        motion: Motion,
        *,
        bounds: Bounds | None = None,
        wait: bool = True,
        timeout: float = 30,
    ) -> tuple[CommandHandle, ...]:
        """Validate the entire route before IO, then submit each linear PTP.

        Timeout is per point. wait=False queues explicit XYZ/R targets; completion remains
        the handles' responsibility. An error stops submission, with no replay or rollback.
        """
        options(wait, timeout)
        if not self.points:
            self.preview(bounds)
            return ()
        with motion.sequence():
            preview = self.preview(bounds)
            return tuple(
                motion.move_to(p.x, p.y, p.z, p.r, wait=wait, timeout=timeout)
                for p in preview.points
            )
