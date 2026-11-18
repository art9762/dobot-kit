"""Read the selected arm without starting motion or changing outputs."""

from dobotkit import Dobot

if __name__ == "__main__":
    port = input("Порт из dobot ports: ").strip()
    with Dobot.connect(port, read_only=True) as arm:
        state = arm.status()
        print("Рука:", state.device.name, "Прошивка:", state.device.firmware)
        print("Поза:", state.pose)
        print("Ошибки:", state.alarms)
