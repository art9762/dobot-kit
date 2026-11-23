"""Operator-run checks. Explicit port; motion requires a physical setup and confirmation."""

from __future__ import annotations

import argparse
import math
import time

from dobot_usb import Dobot
from dobot_usb.errors import DobotError


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", required=True, help="Explicit serial port, e.g. COM3")
    modes = parser.add_subparsers(dest="mode", required=True)
    modes.add_parser("position", help="Read pose and remember it; no controller SET")
    color = modes.add_parser("color", help="Configure selected sensor and read ten reports")
    color.add_argument("--sensor-port", required=True, choices=["GP1", "GP2", "GP4", "GP5"])
    color.add_argument("--sensor-version", required=True, type=int, choices=[1, 2])
    motion = modes.add_parser("motion", help="Small axis move and return; requires MOVE")
    motion.add_argument("--axis", choices=["x", "y", "z", "angle"], required=True)
    motion.add_argument("--offset", type=float, default=2)
    args = parser.parse_args()
    if args.mode == "motion" and (not math.isfinite(args.offset) or not 0 < abs(args.offset) <= 5):
        parser.error("offset must be finite, nonzero and between -5 and 5 mm/degrees")

    with Dobot(args.port) as robot:
        origin = robot.calibrate()
        print("Position / saved origin (mm, degrees):", origin)
        if args.mode == "position":
            return
        if args.mode == "color":
            robot.color_sensor(port=args.sensor_port, version=args.sensor_version)
            print("Configuration ACK received; physical sensing is not yet verified.")
            for _ in range(10):
                print("RGB:", robot.read_color())
                time.sleep(0.2)
            return

        axis = ("x", "y", "z", "angle").index(args.axis)
        coordinates = list(origin)
        coordinates[axis] += args.offset
        target = (coordinates[0], coordinates[1], coordinates[2], coordinates[3])
        print("Will move in a straight line to:", target, "then return to:", origin)
        print("Speed and acceleration will be set to 5% and LEFT at 5%.")
        print("Check the complete route, tool clearance, workspace and emergency stop.")
        if input("Type MOVE to execute; anything else cancels: ").strip() != "MOVE":
            print("Cancelled; no control commands sent.")
            return
        if robot.position() != origin:
            print("Cancelled: the starting pose changed. No control commands sent.")
            return
        try:
            robot.speed(5)
            if robot.position() != origin:
                raise DobotError("Starting pose changed after setting speed; no move sent.")
            robot.move(*target)
            reported = robot.position()
            print("Target position reported:", reported)
            # Do not start the return from a pose outside the same tolerances
            # used by Dobot.move() to confirm the outbound target.
            if math.dist(reported[:3], target[:3]) > 0.3 or abs(reported[3] - target[3]) > 0.65:
                raise DobotError("Pose changed after the outbound move; no return move sent.")
            robot.move(*origin)
            print("Origin position reported:", robot.position())
        except BaseException:
            # Best-effort stop only; never retry a failed move or return automatically.
            try:
                robot.stop()
            except DobotError as stop_error:
                print("Stop was not confirmed:", stop_error)
            raise


if __name__ == "__main__":
    main()
