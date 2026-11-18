"""Перенести предмет присоской между двумя точками, обученными вручную."""

from dobot_usb import connect


def main() -> None:
    port = input("Порт руки с присоской: ").strip()
    with connect(port) as robot:
        input("Подведи пустую присоску к точке захвата, отпусти Unlock и нажми Enter. ")
        pick = robot.pose()
        input("Подведи к точке укладки, отпусти Unlock и нажми Enter. ")
        place = robot.pose()
        lift_mm = float(input("Подъём над самой высокой из точек старта, захвата и укладки (мм): "))
        if input("Путь свободен. Выполнить перенос? [да/нет] ").strip().lower() == "да":
            robot.suction.transfer(pick, place, lift_mm)


if __name__ == "__main__":
    main()
