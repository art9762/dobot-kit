import math
import struct

import pytest

from dobot_usb import Dobot
from dobot_usb.errors import CommandInvalidated, RequestTimeout
from dobot_usb.geometry import circle_path
from tests.test_dobot import MagicianDouble


@pytest.fixture
def magician(monkeypatch: pytest.MonkeyPatch) -> MagicianDouble:
    device = MagicianDouble()
    monkeypatch.setattr("serial.Serial", device.open)
    return device


@pytest.fixture(autouse=True)
def no_motion_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("dobot_usb._robot.time.sleep", lambda _: None)


def moves(device: MagicianDouble) -> list[tuple[float, ...]]:
    return [struct.unpack("<4f", f.params[1:]) for f in device.writes if f.command == 84]


def test_calibration_is_read_only_and_returns_immutable_pose(magician: MagicianDouble) -> None:
    with Dobot("test-port") as robot:
        assert robot.origin is None
        assert robot.calibrate() == magician.pose
        assert robot.origin == magician.pose
        supplied = [210, 20, 40, 0]
        assert robot.calibrate(supplied) == tuple(supplied)
        supplied[0] = 999
        assert robot.origin == (210, 20, 40, 0)
        assert robot.calibrate((1, 2, 3)) == (1, 2, 3, 30)
    assert magician.writes == []


def test_failed_calibration_preserves_previous_origin(magician: MagicianDouble) -> None:
    with Dobot("test-port") as robot:
        old = robot.calibrate()
        with pytest.raises(ValueError):
            robot.calibrate((1, float("nan"), 3, 0))
        assert robot.origin == old
    assert magician.writes == []


@pytest.mark.parametrize("axis", ["x", "y", "z", "angle"])
def test_set_axis_is_absolute_and_needs_no_calibration(magician: MagicianDouble, axis: str) -> None:
    expected = list(magician.pose)
    expected[("x", "y", "z", "angle").index(axis)] = 12.5
    with Dobot("test-port") as robot:
        assert getattr(robot, f"set_{axis}")(**{axis: 12.5}) is None
        assert robot.origin is None
    assert moves(magician) == [tuple(expected)]
    assert magician.pose == tuple(expected)


def test_setters_preserve_latest_pose_and_saved_origin(magician: MagicianDouble) -> None:
    with Dobot("test-port") as robot:
        origin = robot.calibrate((100, 10, 20, 0))
        robot.set_x(210)
        robot.set_y(-10)
        robot.set_z(55)
        robot.set_angle(0)
        robot.set_x(210)
        assert robot.origin == origin
        robot.move_x(5)
    assert moves(magician) == [
        (210, 5, 50, 30),
        (210, -10, 50, 30),
        (210, -10, 55, 30),
        (210, -10, 55, 0),
        (210, -10, 55, 0),
        (105, -10, 55, 0),
    ]


@pytest.mark.parametrize("axis", ["x", "y", "z", "angle"])
@pytest.mark.parametrize(
    "value", [True, None, "5", float("nan"), float("inf"), -float("inf"), 1e39]
)
def test_bad_set_axis_does_no_io(magician: MagicianDouble, axis: str, value: object) -> None:
    with Dobot("test-port") as robot:
        initial_requests = magician.requests[:]
        with pytest.raises(ValueError):
            getattr(robot, f"set_{axis}")(value)
        assert magician.requests == initial_requests
    assert magician.writes == []


@pytest.mark.parametrize("axis", ["x", "y", "z", "angle"])
def test_set_axis_never_retries_lost_ack(magician: MagicianDouble, axis: str) -> None:
    magician.drop_ack = 84
    with Dobot("test-port") as robot:
        with pytest.raises(RequestTimeout):
            getattr(robot, f"set_{axis}")(10)
    assert len(moves(magician)) == 1


@pytest.mark.parametrize("method", ["move_x", "move_y", "move_z", "move_angle"])
def test_axis_requires_calibration_without_writes(magician: MagicianDouble, method: str) -> None:
    with Dobot("test-port") as robot:
        with pytest.raises(ValueError, match="calibrate"):
            getattr(robot, method)(5)
    assert magician.writes == []


def test_relative_does_not_accumulate_and_axis_keeps_other_coordinates(
    magician: MagicianDouble,
) -> None:
    with Dobot("test-port") as robot:
        robot.calibrate()
        robot.move_x(10)
        robot.move_x(10)
        robot.move_y(-5)
        robot.move_z(2)
        robot.move_angle(4)
        robot.move_relative(1, 2, 3, 4)
        robot.move_relative()
    assert moves(magician) == [
        (210, 5, 50, 30),
        (210, 5, 50, 30),
        (210, 0, 50, 30),
        (210, 0, 52, 30),
        (210, 0, 52, 34),
        (201, 7, 53, 34),
        (200, 5, 50, 30),
    ]


@pytest.mark.parametrize(
    ("method", "args", "kwargs"),
    [
        ("calibrate", ((1, 2),), {}),
        ("calibrate", ("1234",), {}),
        ("move_relative", (float("inf"),), {}),
        ("move_x", (True,), {}),
        ("draw_line", ((1, 2, 3), (4, float("nan"), 5)), {}),
        ("draw_square", ((200, 0, 50), 0), {}),
        ("draw_triangle", ((200, 0, 50), -1), {}),
        ("draw_rectangle", ((200, 0, 50), 10, 20), {"plane": "yx"}),
        ("draw_circle", ((200, 0, 50), 5), {"segments": True}),
        ("draw_circle", ((200, 0, 50), 5), {"segments": 11}),
        ("draw_circle", ((200, 0, 50), 5), {"segments": 721}),
        ("draw_rectangle", ((3e38, 0, 50), 3e38, 10), {}),
    ],
)
def test_bad_helpers_do_not_write(
    magician: MagicianDouble, method: str, args: tuple, kwargs: dict
) -> None:
    with Dobot("test-port") as robot:
        robot.calibrate()
        with pytest.raises(ValueError):
            getattr(robot, method)(*args, **kwargs)
    assert magician.writes == []


@pytest.mark.parametrize("plane", ["xy", "xz", "yz"])
def test_rectangle_plane_and_closure(magician: MagicianDouble, plane: str) -> None:
    start = (200, 5, 50, 15)
    with Dobot("test-port") as robot:
        robot.draw_rectangle(start, 10, 20, plane=plane)
    path = moves(magician)
    assert len(path) == 5
    assert path[0] == path[-1] == start
    axes = ["xyz".index(axis) for axis in plane]
    expected = list(start)
    expected[axes[0]] += 10
    expected[axes[1]] += 20
    assert path[2] == tuple(expected)
    assert [f.command for f in magician.writes].count(245) == 1


def test_line_keeps_one_angle_for_all_three_coordinate_points(magician: MagicianDouble) -> None:
    with Dobot("test-port") as robot:
        robot.draw_line((210, 5, 50), (220, 10, 50))
    assert moves(magician) == [(210, 5, 50, 30), (220, 10, 50, 30)]


def test_square_and_triangle(magician: MagicianDouble) -> None:
    with Dobot("test-port") as robot:
        robot.draw_square((200, 5, 50, 0), 10)
        robot.draw_triangle((200, 5, 50, 0), 10)
    path = moves(magician)
    assert len(path) == 9
    assert path[2] == (210, 15, 50, 0)
    assert path[7] == pytest.approx((205, 5 + math.sqrt(3) * 5, 50, 0))
    assert path[8] == (200, 5, 50, 0)


@pytest.mark.parametrize("plane", ["xy", "xz", "yz"])
def test_circle_radius_and_exact_closure(plane: str) -> None:
    start = (200, 5, 50, 0)
    path = circle_path(start, 5, 24, plane)
    centre = list(start[:3])
    centre["xyz".index(plane[0])] -= 5
    assert len(path) == 25
    assert path[0] == path[-1] == start
    assert all(math.dist(p[:3], centre) == pytest.approx(5) for p in path)


def test_circle_execution(magician: MagicianDouble) -> None:
    with Dobot("test-port") as robot:
        robot.draw_circle((200, 5, 50), 5, segments=12)
    path = moves(magician)
    assert len(path) == 13
    assert path[0] == path[-1] == (200, 5, 50, 30)
    assert path[6] == pytest.approx((190, 5, 50, 30))


def test_stop_between_segments_cannot_restart_path(
    magician: MagicianDouble, monkeypatch: pytest.MonkeyPatch
) -> None:
    with Dobot("test-port") as robot:
        original = robot._move_to

        def stop_after_first(target: tuple[float, float, float, float]) -> None:
            original(target)
            robot.stop()

        monkeypatch.setattr(robot, "_move_to", stop_after_first)
        with pytest.raises(CommandInvalidated):
            robot.draw_square((200, 5, 50), 10)
    assert len(moves(magician)) == 1


def test_lost_ack_does_not_continue_or_replay_path(magician: MagicianDouble) -> None:
    magician.drop_ack = 84
    with Dobot("test-port") as robot:
        with pytest.raises(RequestTimeout):
            robot.draw_square((200, 5, 50), 10)
    assert len(moves(magician)) == 1
