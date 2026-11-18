"""Mark one straight XY line from the prepared start on a connected Magician."""

import argparse
import math
import struct

from dobot_usb import Dobot, Point, ToolState, connect


def mark_line(robot: Dobot, dx_mm: float, dy_mm: float) -> None:
    """Keep the current Z and R. The operator sets the focus, clearance and PTP rates."""
    if not all(math.isfinite(v) for v in (dx_mm, dy_mm)) or (dx_mm == 0 and dy_mm == 0):
        raise ValueError("Specify a finite, nonzero XY displacement in millimetres")
    robot.tool.select("laser")
    try:
        with robot.sequence():
            robot.laser.off()
            if robot.laser.state() != ToolState(False, False):
                raise RuntimeError("Controller did not confirm laser output off")
            robot.wait()
            start = robot.pose()
            # Plan at the same float precision as the controller before enabling the output.
            x, y = struct.unpack("<2f", struct.pack("<2f", start.x + dx_mm, start.y + dy_mm))
            if not (math.isfinite(x) and math.isfinite(y)) or (x, y) == (start.x, start.y):
                raise ValueError("Requested line has no representable endpoint")
            target = Point(x, y, start.z, start.r)
            robot.motion.wait_pose(Point(start.x, start.y, start.z, start.r))
            robot.laser.arm()
            robot.laser.on()
            robot.move(target)
    finally:
        # Send OFF even if the motion sequence was interrupted.
        try:
            robot.laser.off()
            if robot.laser.state() != ToolState(False, False):
                raise RuntimeError("Controller did not confirm laser output off")
        finally:
            robot.laser.disarm()
    robot.motion.wait_pose(target)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", required=True)
    parser.add_argument("--by", nargs=2, type=float, metavar=("DX_MM", "DY_MM"), required=True)
    args = parser.parse_args()
    with connect(args.port) as robot:
        mark_line(robot, *args.by)
        print("Line endpoint reached. Controller reports laser output off.")


if __name__ == "__main__":
    main()
