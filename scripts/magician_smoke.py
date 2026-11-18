"""Explicit hardware smoke test for a stationary Magician with a passive pen."""

import argparse
import json
from dataclasses import asdict
from math import dist

from dobotkit import Dobot, PenCheckpoint, Point, Pose, RobotAlarmError

SLOW: dict[str, dict[str, object]] = {
    "ptp_joint_params": {"velocity": (5.0,) * 4, "acceleration": (10.0,) * 4},
    "ptp_coordinate_params": {
        "xyz_velocity": 5.0,
        "r_velocity": 5.0,
        "xyz_acceleration": 10.0,
        "r_acceleration": 10.0,
    },
    "ptp_common_params": {"velocity_ratio": 100.0, "acceleration_ratio": 100.0},
}


def point(pose: Pose) -> Point:
    return Point(pose.x, pose.y, pose.z, pose.r)


def set_rates(robot: Dobot, name: str, values: dict[str, object]) -> None:
    robot.advanced.set(name, queued=False, **values)
    if robot.advanced.get(name) != values:
        raise RuntimeError(f"Controller readback did not match {name}")


def exercise(robot: Dobot, kind: str) -> dict[str, object]:
    status = robot.status()
    if status.alarms:
        raise RobotAlarmError("Controller alarms before exercise", alarms=status.alarms)
    robot.pen.check_outputs()
    if robot.advanced.get("device_with_l")["enabled"]:
        raise RuntimeError("Exercise is for a Magician on a fixed base")
    offset = robot.advanced.get("end_effector_params")
    saved = {name: robot.advanced.get(name) for name in SLOW}

    robot.stop(clear=True, timeout=3)
    changed = []
    try:
        with robot.motion.sequence():
            start = robot.motion.wait_for_pose(point(status.pose), timeout=3)
            start_index = robot.queue_index()
            for name, values in SLOW.items():
                changed.append(name)
                set_rates(robot, name, values)
            if kind == "lift":
                target = Point(start.x, start.y, start.z + 2, start.r)
                handle = robot.move_to(target.x, target.y, target.z, target.r, timeout=4)
                actual = robot.motion.wait_for_pose(target, timeout=3)
                assert handle.queue_index is not None
                checkpoints = (
                    PenCheckpoint(target, actual, handle.queue_index, robot.queue_index()),
                )
            else:
                paper = kind == "pen-paper"
                strokes = (
                    [[(0.0, 0.0), (5.0, 0.0)], [(5.0, 3.0), (0.0, 3.0)]]
                    if paper
                    else [[(0.0, 0.0), (2.0, 0.0)], [(2.0, 2.0), (0.0, 2.0)]]
                )
                report = robot.pen.draw(
                    strokes,
                    origin=start,
                    dry_run=not paper,
                    lift_mm=3.0 if paper else 2.0,
                    timeout=4,
                )
                checkpoints = report.checkpoints
    finally:
        # Restore speed only after force-stop and clear succeed on the original connection.
        robot.stop(clear=True, timeout=3)
        for name in reversed(changed):
            set_rates(robot, name, saved[name])

    return {
        "exercise": kind,
        "firmware": status.device.firmware,
        "firmware_reply_bytes": len(status.device.firmware_raw),
        "tool_offset_mm": offset,
        "start": asdict(start),
        "start_queue_index": start_index,
        "checkpoints": [
            {
                "target": asdict(c.target),
                "actual": asdict(c.actual),
                "error_mm": dist(
                    (c.target.x, c.target.y, c.target.z), (c.actual.x, c.actual.y, c.actual.z)
                ),
                "queue_ack": c.queue_index,
                "queue_index": c.observed_queue_index,
            }
            for c in checkpoints
        ],
        "alarms_after": robot.alarms(),
        "speed_settings_restored": True,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", required=True, help="Explicit port released by other clients")
    parser.add_argument(
        "--exercise",
        required=True,
        choices=("lift", "pen-air", "pen-paper"),
        help="pen-paper starts with the nib already touching a secured sheet at the first stroke",
    )
    args = parser.parse_args()
    with Dobot.connect(args.port, timeout=0.75) as robot:
        print(json.dumps(exercise(robot, args.exercise), indent=2))


if __name__ == "__main__":
    main()
