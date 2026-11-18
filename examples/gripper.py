"""Перенести предмет схватом между двумя измеренными точками."""

import argparse

from dobot_usb import Dobot, Point, connect


def pick_and_place(robot: Dobot, pick: Point, place: Point, *, lift_mm: float) -> None:
    """Перенести предмет схватом. XYZ и подъём в миллиметрах, R в градусах."""
    robot.gripper.transfer(pick, place, lift_mm)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", required=True)
    for flag in ("--pick", "--place"):
        parser.add_argument(flag, nargs=4, type=float, metavar=("X", "Y", "Z", "R"), required=True)
    parser.add_argument(
        "--lift-mm",
        type=float,
        required=True,
        help="подъём над точками старта, захвата и укладки в мм",
    )
    args = parser.parse_args()
    with connect(args.port) as robot:
        pick_and_place(robot, Point(*args.pick), Point(*args.place), lift_mm=args.lift_mm)
        print("Перенос завершён. Управление губками отключено.")


if __name__ == "__main__":
    main()
