"""Read the selected arm without starting motion or changing outputs."""

from dobot_usb import connect

if __name__ == "__main__":
    port = input("Порт из dobot ports: ").strip()
    with connect(port, read_only=True) as arm:
        state = arm.status()
        print("Рука:", state.device.name, "Прошивка:", state.device.firmware)
        print("Поза:", state.pose)
        print("Ошибки:", state.alarms)
