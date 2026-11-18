"""Flat-sheet pen plans are built offline and run through the existing motion facade."""

import itertools
import struct
from collections.abc import Iterator
from typing import Any

import pytest

from dobotkit import (
    Bounds,
    CommandInvalidated,
    Dobot,
    DobotError,
    ExecutionState,
    ExecutionTimeout,
    Path,
    Point,
    Pose,
)
from dobotkit.pen import pen_path

from .simulation import SimulatedTransport

PAPER = Point(200, 0, 50, 6)
START = Pose(202, -1, 50, 5.5, (0, 30, 30, 0))
STROKES = [[(0, 0), (2, 1), (2, 1)], [(5, 2), (7, 3)]]


def test_two_strokes_approach_and_travel_above_the_sheet() -> None:
    path = pen_path(PAPER, STROKES, start=START)
    assert isinstance(path, Path)
    assert path.points == (
        Point(202, -1, 53, 5.5),
        Point(200, 0, 53, 5.5),
        Point(200, 0, 50, 5.5),
        Point(202, 1, 50, 5.5),
        Point(202, 1, 53, 5.5),
        Point(205, 2, 53, 5.5),
        Point(205, 2, 50, 5.5),
        Point(207, 3, 50, 5.5),
        Point(207, 3, 53, 5.5),
    )


def test_air_pass_keeps_stroke_and_transit_above_the_sheet() -> None:
    path = pen_path(PAPER, STROKES, start=START, lift_mm=1, dry_run=True)
    assert path.points[0] == Point(202, -1, 52, 5.5)
    assert [p.z for p in path.points] == [52, 52, 51, 51, 52, 52, 51, 51, 52]


def test_high_start_descends_only_at_first_stroke_xy() -> None:
    path = pen_path(PAPER, STROKES, start=Point(210, 1, 75, 6))
    assert path.points[:2] == (Point(200, 0, 75, 6), Point(200, 0, 50, 6))


def test_lazy_inputs_with_disjoint_strokes() -> None:
    strokes = ((point for point in stroke) for stroke in STROKES)
    assert pen_path(PAPER, strokes, start=START) == pen_path(PAPER, STROKES, start=START)


def test_input_and_preview_are_independent_of_robot_and_later_edits() -> None:
    strokes = [[(0, 1), (3, 4)]]
    path = pen_path(PAPER, strokes, start=PAPER)
    strokes[0][1] = (9, 9)
    assert path.preview().bounds == Bounds(Point(200, 0, 50, 6), Point(203, 4, 53, 6))


@pytest.mark.parametrize(
    "strokes",
    [
        [],
        None,
        [None],
        [[]],
        [[(2, 2)]],
        [[(2, 1), (2, 1)]],
        [["12", "21"]],
        [[(0, True), (2, 2)]],
        [[(0, float("nan")), (2, 2)]],
        [[(1,), (2, 2)]],
        [[(0, 0), (25.1, 0)]],
        [[(0, 0), (0, -26)]],
        [[(0, 0), (1, 1)]] * 33,
        [itertools.repeat((1, 1))],
        [[(-25, 0), (25, 0)] * 6],
    ],
)
def test_invalid_or_over_budget_input_fails_during_planning(strokes: Any) -> None:
    with pytest.raises(ValueError):
        pen_path(PAPER, strokes, start=PAPER)


@pytest.mark.parametrize(
    "kw",
    [
        {"start": Point(226, 0, 50, 0)},
        {"start": Point(200, 0, 49, 6)},
        {"start": Point(200, 0, 51, float("inf"))},
        {"start": (200, 0, 50, 6)},
        {"lift_mm": False},
        {"lift_mm": -1},
        {"lift_mm": 0},
        {"lift_mm": 11},
        {"lift_mm": 1e-10},
        {"dry_run": 2},
        {"bounds": {}},
        {"bounds": Bounds(Point(200, 0, 50, 0), Point(220, 6, 52.9, 10))},
    ],
)
def test_invalid_staging_or_clearance(kw: Any) -> None:
    with pytest.raises(ValueError):
        pen_path(PAPER, STROKES, **{"start": PAPER, **kw})


def test_bounds_also_check_start_even_if_absent_from_targets() -> None:
    high = Point(205, -1, 60, 6)
    bounds = Bounds(Point(190, 0, 30, -20), Point(220, 10, 60, 20))
    with pytest.raises(ValueError, match="start"):
        pen_path(PAPER, STROKES, start=high, bounds=bounds)


def test_real_facade_and_simulator_execute_only_linear_ptp_points() -> None:
    transport = SimulatedTransport()
    with Dobot(transport) as robot:
        taught = robot.pose()
        path = pen_path(taught, STROKES, start=taught, dry_run=True)
        assert all(frame.control == 0 for frame in transport.transactions)
        handles = path.run(robot.motion)
        assert all(handle.state == ExecutionState.COMPLETED for handle in handles)
        actual = robot.pose()
        assert (actual.x, actual.y, actual.z, actual.r) == (
            taught.x + 7,
            taught.y + 3,
            taught.z + 6,
            taught.r,
        )
        writes = [frame for frame in transport.transactions if frame.control & 1]
        assert {frame.command for frame in writes} == {84, 240}
        assert [struct.unpack("<B4f", frame.params) for frame in writes if frame.command == 84] == [
            (2, p.x, p.y, p.z, p.r) for p in path.points
        ]


def test_draw_uses_fresh_start_and_reports_every_controller_checkpoint() -> None:
    transport = SimulatedTransport()
    with Dobot(transport) as robot:
        origin = robot.pose()
        robot.move_to(origin.x + 4, origin.y, origin.z, origin.r + 10, wait=False)
        report = robot.pen.draw(STROKES, origin=origin, lift_mm=2, dry_run=True)

    assert report.start.x == origin.x + 4
    assert report.route == pen_path(origin, STROKES, start=report.start, lift_mm=2, dry_run=True)
    assert report.completed_points == len(report.route.points)
    assert report.unconfirmed_queue_index is None
    for i, checkpoint in enumerate(report.checkpoints):
        expected = report.route.points[i]
        assert checkpoint.target == expected
        assert checkpoint.actual == Pose(
            expected.x, expected.y, expected.z, expected.r, report.start.joints
        )
        assert checkpoint.queue_index == checkpoint.observed_queue_index == i + 2
    assert {f.command for f in transport.transactions if f.control & 1} == {84, 240}


@pytest.mark.parametrize("tool", [61, 62, 63])
@pytest.mark.parametrize("flags", [bytes([1, 0]), bytes([0, 1]), bytes([1, 1])])
def test_draw_refuses_all_powered_tool_states_before_writing(tool: int, flags: bytes) -> None:
    transport = SimulatedTransport()
    transport.responses[tool] = flags
    with Dobot(transport) as robot, pytest.raises(DobotError, match="Passive pen") as problem:
        robot.pen.draw(STROKES, origin=PAPER)
    assert problem.value.stage == "not_sent" and problem.value.pen_report is None
    assert all(f.control == 0 for f in transport.transactions)


def test_draw_validates_whole_route_before_first_move() -> None:
    transport = SimulatedTransport()
    with Dobot(transport) as robot, pytest.raises(ValueError, match="25 mm"):
        robot.pen.draw([[(0, 0), (3, 4)], [(0, 0), (50, 50)]], origin=PAPER)
    assert all(f.control == 0 for f in transport.transactions)


def test_draw_retains_verified_prefix_when_pose_stays_behind(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = SimulatedTransport()
    request = transport.request
    stale_pose = None
    submitted = 0

    def stuck_on_second_point(command: int, params: bytes = b"", **kwargs: Any) -> bytes:
        nonlocal submitted, stale_pose
        if command == 84 and kwargs.get("write"):
            submitted += 1
            if submitted == 2:
                stale_pose = transport.responses[10]
        data = request(command, params, **kwargs)
        if command == 10 and stale_pose is not None:
            return stale_pose
        return data

    monkeypatch.setattr(transport, "request", stuck_on_second_point)
    with Dobot(transport) as robot, pytest.raises(ExecutionTimeout) as problem:
        robot.pen.draw(STROKES, origin=PAPER, timeout=0.16)
    assert submitted == 2
    report = problem.value.pen_report
    assert report is not None
    assert report.completed_points == 1 and report.unconfirmed_queue_index == 2
    assert report.checkpoints[0].target == report.route.points[0]


def test_stop_after_checkpoint_blocks_remaining_pen_moves(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = SimulatedTransport()
    with Dobot(transport) as robot:
        observe_index = robot.queue.index

        def stop_after_checkpoint(*, timeout: float) -> int:
            index = observe_index(timeout=timeout)
            robot.stop()
            return index

        monkeypatch.setattr(robot.queue, "index", stop_after_checkpoint)
        with pytest.raises(CommandInvalidated) as problem:
            robot.pen.draw(STROKES, origin=PAPER)
        assert problem.value.stage == "not_sent"
        report = problem.value.pen_report
        assert report is not None and report.completed_points == 1
        assert report.unconfirmed_queue_index is None
        moves = [f for f in transport.transactions if f.command == 84 and f.control & 1]
        assert len(moves) == 1


def test_draw_exposes_accepted_move_if_queue_wait_times_out() -> None:
    transport = SimulatedTransport(auto_complete=False)
    with Dobot(transport) as robot, pytest.raises(ExecutionTimeout) as problem:
        robot.pen.draw(STROKES, origin=PAPER, timeout=0.16)
    assert problem.value.stage == "accepted"
    assert problem.value.command == 84 and problem.value.queue_index == 1
    report = problem.value.pen_report
    assert report is not None and report.completed_points == 0
    assert report.unconfirmed_queue_index == 1
    assert len([f for f in transport.transactions if f.command == 84 and f.control & 1]) == 1


def test_stop_during_planning_prevents_first_waypoint() -> None:
    transport = SimulatedTransport()
    with Dobot(transport) as robot:

        def interrupted_strokes() -> Iterator[list[tuple[int, int]]]:
            robot.stop()
            yield [(0, 0), (2, 0)]

        with pytest.raises(CommandInvalidated) as problem:
            robot.pen.draw(interrupted_strokes(), origin=PAPER)
        assert problem.value.stage == "not_sent"
        assert not any(f.command == 84 and f.control & 1 for f in transport.transactions)
