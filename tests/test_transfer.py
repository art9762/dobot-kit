"""Exercise the complete suction/gripper transfer through the wire-command layers."""

import struct
import sys
from pathlib import Path
from runpy import run_path

import pytest

from dobotkit import CommandInvalidated, Dobot, DobotError, ExecutionTimeout, Point, Pose, ToolState

from .simulation import SimulatedTransport

PICK = Pose(205, 3, 52, 15, (1, 2, 3, 4))
PLACE = Point(220, -7, 40, -15)


@pytest.mark.parametrize("name,command", [("suction", 62), ("gripper", 63)])
def test_transfer_uses_pending_start_then_observes_the_route_and_outputs(name, command):
    sim = SimulatedTransport()
    with Dobot(sim) as robot:
        # The first queue receipt is still pending when the helper starts.
        robot.move_to(210, 2, 58, 25, wait=False)
        previous = len(sim.transactions)
        getattr(robot, name).pick_and_place(PICK, PLACE, lift_mm=7)
        assert robot.pose().z == 65
        assert getattr(robot, name).state() == ToolState(False, False)

    def above(point):
        return (2, point.x, point.y, 65, point.r)

    def at(point):
        return (2, point.x, point.y, point.z, point.r)

    events = [
        struct.unpack("<B4f", f.params) if f.command == 84 else (f.command, f.params)
        for f in sim.transactions[previous:]
        if f.control & 1
    ]
    open_jaws = [(63, bytes([1, 0]))] if name == "gripper" else []
    off = (command, bytes(2))
    assert events == [
        *open_jaws,
        (2, 210, 2, 65, 25),
        above(PICK),
        at(PICK),
        (command, bytes([1, 1])),
        above(PICK),
        above(PLACE),
        at(PLACE),
        *(open_jaws or [off]),
        above(PLACE),
        *([off] if name == "gripper" else []),
    ]


@pytest.mark.parametrize("name", ["suction", "gripper"])
@pytest.mark.parametrize("active_output", [61, 62, 63])
def test_active_output_blocks_transfer_before_any_write(name, active_output):
    sim = SimulatedTransport()
    sim.responses[active_output] = bytes([1, 1])
    with Dobot(sim) as robot, pytest.raises(DobotError, match="inactive") as err:
        getattr(robot, name).pick_and_place(PICK, PLACE, lift_mm=5)
    assert err.value.stage == "not_sent"
    assert all(not frame.control & 1 for frame in sim.transactions)
    assert sim.responses[active_output] == bytes([1, 1])


@pytest.mark.parametrize(
    "pick,place,lift",
    [
        (PICK, Point(0, float("nan"), 0, 0), 5),
        (Point(0, 0, 0, float("inf")), PLACE, 5),
        (PICK, PLACE, False),
        (PICK, PLACE, 0),
        (PICK, PLACE, -1),
        (PICK, PLACE, float("nan")),
        (PICK, Point(0, 0, 3e38, 0), 1e38),  # only the calculated height overflows
        (PICK, PLACE, 1e-8),  # no actual change in float32 wire Z
    ],
)
def test_invalid_route_cannot_even_open_initial_jaws(pick, place, lift):
    sim = SimulatedTransport()
    with Dobot(sim) as robot, pytest.raises(ValueError):
        robot.gripper.pick_and_place(pick, place, lift_mm=lift)
    assert all(not frame.control & 1 for frame in sim.transactions)


@pytest.mark.parametrize("name,command", [("suction", 62), ("gripper", 63)])
@pytest.mark.parametrize("unconfirmed", ["pick", "place"])
def test_pose_mismatch_prevents_the_next_tool_action(monkeypatch, name, command, unconfirmed):
    sim = SimulatedTransport()
    with Dobot(sim) as robot:
        observe = robot.motion.wait_for_pose
        target = Point(PICK.x, PICK.y, PICK.z, PICK.r) if unconfirmed == "pick" else PLACE

        def wrong_reported_pose(point):
            if point == target:
                sim.responses[10] = struct.pack("<8f", 0, 0, 0, 0, 0, 0, 0, 0)
                return observe(point, timeout=0.02)
            return observe(point)

        monkeypatch.setattr(robot.motion, "wait_for_pose", wrong_reported_pose)
        with pytest.raises(ExecutionTimeout):
            getattr(robot, name).pick_and_place(PICK, PLACE, lift_mm=5)
        assert getattr(robot, name).state().active is (unconfirmed == "place")
    writes = [f for f in sim.transactions if f.control & 1]
    outputs = [f.params for f in writes if f.command == command]
    assert outputs == ([bytes([1, 0])] if name == "gripper" else []) + (
        [bytes([1, 1])] if unconfirmed == "place" else []
    )
    assert [f.command for f in writes].count(84) == (3 if unconfirmed == "pick" else 6)
    assert writes[-1].command == 84


@pytest.mark.parametrize("name,command", [("suction", 62), ("gripper", 63)])
def test_pause_at_contact_prevents_grasp_or_queue_restart(monkeypatch, name, command):
    sim = SimulatedTransport()
    with Dobot(sim) as robot:
        observe = robot.motion.wait_for_pose

        def pause_at_pick(point):
            pose = observe(point)
            if point.x == PICK.x and point.z == PICK.z:
                robot.queue.pause()
            return pose

        monkeypatch.setattr(robot.motion, "wait_for_pose", pause_at_pick)
        with pytest.raises(CommandInvalidated):
            getattr(robot, name).pick_and_place(PICK, PLACE, lift_mm=5)
    writes = [f for f in sim.transactions if f.control & 1]
    assert writes[-1].command == 241
    assert [f.params for f in writes if f.command == command] == (
        [bytes([1, 0])] if name == "gripper" else []
    )


def test_unconfirmed_vacuum_stops_in_place_after_single_grasp(monkeypatch):
    sim = SimulatedTransport()
    with Dobot(sim) as robot:
        monkeypatch.setattr(robot.suction, "state", lambda: ToolState(False, False))
        with pytest.raises(DobotError, match="Expected output"):
            robot.suction.pick_and_place(PLACE, PICK, lift_mm=3)
    writes = [f for f in sim.transactions if f.control & 1]
    assert writes[-1].command == 62
    assert [f.params for f in writes if f.command == 62] == [bytes([1, 1])]
    assert [f.command for f in writes].count(84) == 3


@pytest.mark.parametrize("script", ["pick_and_place", "gripper"])
def test_example_entry_point_uses_selected_head_without_clearing(monkeypatch, script):
    sim = SimulatedTransport()
    monkeypatch.setattr(Dobot, "connect", lambda port: Dobot(sim))
    if script == "gripper":
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "gripper.py",
                "--port",
                "example",
                "--pick",
                "205",
                "3",
                "52",
                "15",
                "--place",
                "220",
                "-7",
                "40",
                "-15",
                "--lift-mm",
                "5",
            ],
        )
    else:
        answers = iter(["example", "", "", "5", "да"])
        monkeypatch.setattr("builtins.input", lambda prompt: next(answers))
    path = Path(__file__).resolve().parents[1] / "examples" / f"{script}.py"
    run_path(str(path), run_name="__main__")
    writes = [f.command for f in sim.transactions if f.control & 1]
    assert set(writes) == {84, 240, 63 if script == "gripper" else 62}
    assert writes.count(84) == 7
