"""Planning and observed execution for a passive pen on a horizontal sheet."""

from __future__ import annotations

import math
import struct
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .errors import DobotError
from .models import Point, Pose
from .motion import validated_point
from .paths import Bounds, Path
from .validation import number, options

if TYPE_CHECKING:
    from .client import Dobot

_MAX_OFFSET = 25.0
_MAX_LENGTH = 500.0
_MAX_STROKES = 32
_MAX_VERTICES = 256


def _point(value: Point | Pose, name: str) -> Point:
    if isinstance(value, Pose):
        value = Point(value.x, value.y, value.z, value.r)
    return validated_point(value, name)


def _wire_z(value: float) -> float:
    return float(struct.unpack("<f", struct.pack("<f", number(value, "pen z")))[0])


def pen_path(
    origin: Point | Pose,
    strokes: Iterable[Iterable[Sequence[float]]],
    *,
    start: Point | Pose,
    lift_mm: float = 3.0,
    dry_run: bool = False,
    bounds: Bounds | None = None,
) -> Path:
    """Build absolute linear PTP targets from XY offsets in mm, without device IO.

    Supply the taught contact pose on a level sheet and the actual start pose immediately
    before running. All targets preserve start.r. The first approach rises at the current
    XY if necessary, travels above the first vertex, then descends vertically. Each stroke
    ends with a vertical lift. dry_run raises both drawing and travel planes by lift_mm.

    Up to 32 strokes, 256 input vertices, ±25 mm of XY offset from the origin (also for
    the start), 500 mm total 3D travel and 0 < lift_mm <= 10. Optional absolute bounds
    include the initial pose and all targets. These are planner limits. The caller owns
    tool calibration, workspace clearance, motion rates and the controller queue.
    """
    paper = _point(origin, "origin")
    current = _point(start, "start")
    lift = number(lift_mm, "lift_mm")
    if not 0 < lift <= 10:
        raise ValueError("lift_mm must be positive and at most 10 mm")
    if type(dry_run) is not bool:
        raise ValueError("dry_run must be bool")
    if bounds is not None and (not isinstance(bounds, Bounds) or not bounds.contains(current)):
        raise ValueError("start must be inside the supplied Bounds")
    if abs(current.x - paper.x) > _MAX_OFFSET or abs(current.y - paper.y) > _MAX_OFFSET:
        raise ValueError("start must be within ±25 mm of the origin in XY")
    if current.z < paper.z:
        raise ValueError("start is below the taught paper plane")
    draw_z = paper.z + (lift if dry_run else 0.0)
    up_z = draw_z + lift
    if _wire_z(up_z) <= _wire_z(draw_z) or (dry_run and _wire_z(draw_z) <= _wire_z(paper.z)):
        raise ValueError("lift_mm is too small to represent at the taught z")
    travel_z = max(up_z, current.z)
    vertices = 0
    length = 0.0
    points: list[Point] = []

    def target(x: float, y: float, z: float) -> None:
        nonlocal length, current
        point = validated_point(Point(x, y, z, current.r))
        if point == current:
            return
        length += math.dist((current.x, current.y, current.z), (x, y, z))
        if length > _MAX_LENGTH:
            raise ValueError("pen route exceeds 500 mm total travel")
        points.append(point)
        current = point

    if not isinstance(strokes, Iterable):
        raise ValueError("strokes must be an iterable of XY polylines")
    for stroke_index, stroke in enumerate(strokes):
        if stroke_index >= _MAX_STROKES:
            raise ValueError("at most 32 strokes per pen path")
        if not isinstance(stroke, Iterable):
            raise ValueError("each stroke must be an iterable of XY vertices")
        previous: tuple[float, float] | None = None
        moved = False
        for vertex in stroke:
            vertices += 1
            if vertices > _MAX_VERTICES:
                raise ValueError("at most 256 vertices per pen path")
            if not isinstance(vertex, Sequence) or isinstance(vertex, (bytes, str, bytearray)):
                raise ValueError("each pen vertex must be an XY pair")
            if len(vertex) != 2:
                raise ValueError("each pen vertex must be an XY pair")
            dx, dy = number(vertex[0], "vertex.x"), number(vertex[1], "vertex.y")
            if max(abs(dx), abs(dy)) > _MAX_OFFSET:
                raise ValueError("pen vertices must be within ±25 mm of the origin in XY")
            xy = (paper.x + dx, paper.y + dy)
            if previous is None:
                target(current.x, current.y, travel_z)
                target(*xy, travel_z)
            elif previous != xy:
                moved = True
            target(*xy, draw_z)
            previous = xy
        if not moved:
            raise ValueError("each pen stroke must contain at least two distinct vertices")
        target(current.x, current.y, up_z)
        travel_z = up_z
    if not points:
        raise ValueError("at least one pen stroke is required")
    route = Path(points)
    if bounds is not None:
        route.preview(bounds)
    return route


@dataclass(frozen=True)
class PenCheckpoint:
    """One linear target with its queue receipt, sampled index and observed pose."""

    target: Point
    actual: Pose
    queue_index: int
    observed_queue_index: int


@dataclass(frozen=True)
class PenReport:
    """Telemetry for the verified prefix of a pen route, in submission order."""

    start: Pose
    route: Path
    checkpoints: tuple[PenCheckpoint, ...]
    # A receipt alone cannot establish that the next target was reached.
    unconfirmed_queue_index: int | None = None

    @property
    def completed_points(self) -> int:
        return len(self.checkpoints)


class Pen:
    """Passive pen on a level sheet, sharing the connected robot's motion and queue."""

    def __init__(self, robot: Dobot) -> None:
        self._robot = robot

    def check_outputs(self) -> None:
        """Read powered outputs and require both control and activation to be off."""
        for tool in ("laser", "gripper", "suction"):
            state = self._robot.tool_state(tool)
            if state.enabled or state.active:
                raise DobotError(
                    f"Passive pen requires disabled {tool} output, got {state}", stage="not_sent"
                )

    def draw(
        self,
        strokes: Iterable[Iterable[Sequence[float]]],
        *,
        origin: Point | Pose,
        lift_mm: float = 3.0,
        dry_run: bool = False,
        bounds: Bounds | None = None,
        timeout: float = 30.0,
    ) -> PenReport:
        """Execute linear strokes relative to a taught contact point on a level sheet.

        Wait for this client's pending moves, plan from a fresh pose, then confirm
        each target by queue and telemetry. The timeout is per wait or motion operation.
        dry_run executes an elevated pass. Set PTP rates explicitly before calling.
        A DobotError after planning carries pen_report with the verified prefix.
        No failed move is retried or followed by an automatic lift, stop or queue clear.
        """
        options(True, timeout)
        robot = self._robot
        checkpoints: list[PenCheckpoint] = []
        unconfirmed: int | None = None
        with robot.motion.sequence():
            robot.queue.wait(timeout=timeout)
            self.check_outputs()
            start = robot.pose()
            route = pen_path(
                origin, strokes, start=start, lift_mm=lift_mm, dry_run=dry_run, bounds=bounds
            )
            try:
                robot.motion.wait_for_pose(_point(start, "start"), timeout=timeout)
                for target in route.points:
                    handle = robot.move_to(target.x, target.y, target.z, target.r, timeout=timeout)
                    # MOVL is queued; the executor validates its uint64 receipt.
                    assert handle.queue_index is not None
                    unconfirmed = handle.queue_index
                    actual = robot.motion.wait_for_pose(target, timeout=timeout)
                    index = robot.queue.index(timeout=timeout)
                    checkpoints.append(PenCheckpoint(target, actual, unconfirmed, index))
                    unconfirmed = None
            except DobotError as exc:
                exc.pen_report = PenReport(
                    start,
                    route,
                    tuple(checkpoints),
                    unconfirmed if unconfirmed is not None else exc.queue_index,
                )
                raise
        return PenReport(start, route, tuple(checkpoints))
