"""Controller-observed transfer of one part with a suction cup or gripper."""

from __future__ import annotations

import struct
from dataclasses import replace
from typing import TYPE_CHECKING

from .errors import DobotError
from .models import Point, Pose, ToolState
from .motion import validated_point
from .tools import Gripper, Suction
from .validation import number

if TYPE_CHECKING:
    from .client import Dobot
    from .interfaces import Backend


def _point(value: Point | Pose, name: str) -> Point:
    if isinstance(value, Pose):
        value = Point(value.x, value.y, value.z, value.r)
    return validated_point(value, name)


def _wire_z(value: float) -> float:
    return float(struct.unpack("<f", struct.pack("<f", value))[0])


def _expect_output(head: Suction | Gripper, *, enabled: bool, active: bool) -> None:
    state = head.state()
    if state != ToolState(enabled, active):
        raise DobotError(f"Expected output enabled={enabled}, active={active}, observed {state}")


def _pick_and_place(
    robot: Dobot, head: Suction | Gripper, pick: Point | Pose, place: Point | Pose, lift_mm: float
) -> None:
    pickup = _point(pick, "pick")
    dropoff = _point(place, "place")
    lift = number(lift_mm, "lift_mm")
    if lift <= 0:
        raise ValueError("lift_mm must be positive")

    def move(target: Point) -> None:
        robot.move_to(target)
        robot.motion.wait_for_pose(target)

    with robot.motion.sequence():
        robot.queue.wait()
        start = _point(robot.pose(), "start")
        highest = max(start.z, pickup.z, dropoff.z)
        height = number(highest + lift, "transfer height")
        # A lift must still rise after encoding the controller's float32 coordinates.
        if _wire_z(height) <= _wire_z(highest):
            raise ValueError("lift_mm is too small to represent at the transfer height")
        start_up = replace(start, z=height)
        pick_up = replace(pickup, z=height)
        place_up = replace(dropoff, z=height)

        for name in ("suction", "gripper", "laser"):
            if robot.tool_state(name).active:
                raise DobotError(f"Transfer requires an inactive {name} output", stage="not_sent")
        robot.motion.wait_for_pose(start)
        if isinstance(head, Gripper):
            head.open()
            _expect_output(head, enabled=True, active=False)
            grasp, drop = head.close, head.open
        else:
            grasp, drop = head.on, head.off

        move(start_up)
        move(pick_up)
        move(pickup)
        grasp()
        _expect_output(head, enabled=True, active=True)
        move(pick_up)
        move(place_up)
        move(dropoff)
        drop()
        _expect_output(head, enabled=isinstance(head, Gripper), active=False)
        move(place_up)
        if isinstance(head, Gripper):
            head.release()
            _expect_output(head, enabled=False, active=False)


class SuctionHead(Suction):
    def __init__(self, backend: Backend, robot: Dobot) -> None:
        super().__init__(backend)
        self._robot = robot

    def pick_and_place(self, pick: Point | Pose, place: Point | Pose, *, lift_mm: float) -> None:
        """Pick with vacuum, release at place, and lift above all three poses by lift_mm."""
        _pick_and_place(self._robot, self, pick, place, lift_mm)


class GripperHead(Gripper):
    def __init__(self, backend: Backend, robot: Dobot) -> None:
        super().__init__(backend)
        self._robot = robot

    def pick_and_place(self, pick: Point | Pose, place: Point | Pose, *, lift_mm: float) -> None:
        """Transfer with the jaws, then disable gripper control at the final clearance."""
        _pick_and_place(self._robot, self, pick, place, lift_mm)
