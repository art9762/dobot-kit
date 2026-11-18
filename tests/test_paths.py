"""Offline paths preview and preflight tests."""

from contextlib import nullcontext
from typing import Any

import pytest

from dobotkit.models import Point
from dobotkit.paths import Bounds, Path


class FakeMotion:
    sequence = staticmethod(nullcontext)

    def __init__(self, fail_at: int | None = None) -> None:
        self.calls: list[Any] = []
        self.fail_at = fail_at

    def move_to(self, *args: object, **kwargs: object) -> str:
        self.calls.append((args, kwargs))
        if len(self.calls) == self.fail_at:
            raise RuntimeError("uncertain command")
        return f"handle{len(self.calls)}"


BOUNDS = Bounds(Point(-10, -10, -10, -180), Point(10, 10, 10, 180))


def test_immutable_builder_and_offline_preview() -> None:
    source = [Point(1, 2, 3, 4)]
    path = Path(source)
    source.append(Point(9, 9, 9, 9))
    longer = path.then(Point(-1, 5, 0, 10))
    assert len(path.points) == 1
    preview = longer.preview(BOUNDS)
    assert preview.points == longer.points
    assert preview.bounds == Bounds(Point(-1, 2, 0, 4), Point(1, 5, 3, 10))


def test_empty_path_is_noop() -> None:
    motion = FakeMotion()
    assert Path().preview().bounds is None
    assert Path().run(motion) == ()
    assert motion.calls == []


def test_run_submits_explicit_r_linear_points() -> None:
    motion = FakeMotion()
    path = Path([Point(1, 2, 3, 4), Point(5, 6, 7, 8)])
    assert path.run(motion, bounds=BOUNDS, wait=False, timeout=2) == ("handle1", "handle2")
    assert motion.calls == [
        ((1.0, 2.0, 3.0, 4.0), {"wait": False, "timeout": 2}),
        ((5.0, 6.0, 7.0, 8.0), {"wait": False, "timeout": 2}),
    ]


def test_late_point_out_of_bounds_rejects_before_any_submission() -> None:
    motion = FakeMotion()
    path = Path([Point(0, 0, 0, 0), Point(11, 0, 0, 0)])
    with pytest.raises(ValueError, match="point 1"):
        path.run(motion, bounds=BOUNDS)
    assert motion.calls == []


@pytest.mark.parametrize(
    "point",
    [
        Point(float("nan"), 0, 0, 0),
        Point(0, 0, 0, float("inf")),
        Point(0, True, 0, 0),
        Point(0, 0, 1e100, 0),
        (1, 2, 3, 4),
    ],
)
def test_bad_point_rejected_on_build(point: Any) -> None:
    with pytest.raises(ValueError):
        Path([Point(0, 0, 0, 0), point])


@pytest.mark.parametrize(
    "minimum,maximum",
    [
        (Point(1, 0, 0, 0), Point(0, 0, 0, 0)),
        (Point(0, 0, 0, 1), Point(0, 0, 0, 0)),
        (Point(float("inf"), 0, 0, 0), Point(0, 0, 0, 0)),
    ],
)
def test_invalid_bounds(minimum: Point, maximum: Point) -> None:
    with pytest.raises(ValueError):
        Bounds(minimum, maximum)


@pytest.mark.parametrize("kwargs", [{"wait": 1}, {"timeout": 0}, {"bounds": {}}])
def test_bad_run_options_reject_before_io(kwargs: Any) -> None:
    motion = FakeMotion()
    with pytest.raises(ValueError):
        Path([Point(0, 0, 0, 0)]).run(motion, **kwargs)
    assert motion.calls == []


def test_failure_stops_without_replay_or_rollback() -> None:
    motion = FakeMotion(fail_at=2)
    path = Path(Point(i, 0, 0, 0) for i in range(3))
    with pytest.raises(RuntimeError):
        path.run(motion)
    assert len(motion.calls) == 2


def test_bounds_are_inclusive_in_all_four_axes() -> None:
    assert BOUNDS.contains(BOUNDS.minimum)
    assert BOUNDS.contains(BOUNDS.maximum)
    assert not BOUNDS.contains(Point(0, 0, 0, 181))
