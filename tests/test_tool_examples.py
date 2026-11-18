"""Exercise tool recipes with a controlled in-memory transport."""

from pathlib import Path
from runpy import run_path

import pytest

from dobot_usb import CommandInvalidated, Dobot, Point, ToolState

from .simulation import SimulatedTransport

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
mark_line = run_path(str(EXAMPLES / "laser.py"))["mark_line"]
pick_and_place = run_path(str(EXAMPLES / "gripper.py"))["pick_and_place"]


def test_gripper_transfers_then_releases_at_clearance() -> None:
    transport = SimulatedTransport()
    with Dobot(transport) as robot:
        pick_and_place(robot, Point(200, 0, 45, 0), Point(204, 3, 44, 10), lift_mm=5)
        end = robot.pose()
        assert (end.x, end.y, end.z, end.r) == (204, 3, 55, 10)
        assert robot.gripper.state() == ToolState(False, False)
        states = [f.params for f in transport.transactions if f.command == 63 and f.control & 1]
        assert states == [b"\x01\x00", b"\x01\x01", b"\x01\x00", b"\x00\x00"]


def test_line_starts_and_finishes_with_output_off() -> None:
    transport = SimulatedTransport()
    with Dobot(transport) as robot:
        start = robot.pose()
        mark_line(robot, 3, -1)
        end = robot.pose()
        assert (end.x, end.y, end.z, end.r) == (start.x + 3, start.y - 1, start.z, start.r)
        assert not robot.laser.armed and robot.laser.state() == ToolState(False, False)
        route = [
            (f.command, f.params if f.command == 61 else None)
            for f in transport.transactions
            if f.command in (61, 84) and f.control & 1
        ]
        assert route == [(61, b"\0\0"), (61, b"\1\1"), (84, None), (61, b"\0\0")]


def test_stop_after_laser_on_prevents_motion_and_still_sends_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = SimulatedTransport()
    with Dobot(transport) as robot:
        enable = robot.laser.on

        def stop_after_enable() -> None:
            enable()
            robot.stop()

        monkeypatch.setattr(robot.laser, "on", stop_after_enable)
        with pytest.raises(CommandInvalidated):
            mark_line(robot, 5, 0)
        assert not robot.laser.armed and robot.laser.state() == ToolState(False, False)
        assert not any(f.command == 84 and f.control & 1 for f in transport.transactions)
        writes = [f.command for f in transport.transactions if f.control & 1]
        assert writes[-3:] == [242, 245, 61]
