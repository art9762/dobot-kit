"""Teach Cartesian waypoints by hand. Preview before choosing to run the route."""

from dobot_usb import Path, connect

if __name__ == "__main__":
    port = input("Порт руки: ").strip()
    with connect(port) as arm:
        points = []
        print("Подведи инструмент к точке, отпусти Unlock и нажми Enter.")
        while input("Enter — записать позу, q — закончить: ").strip().lower() != "q":
            points.append(arm.pose())
        route = Path(points)
        print("Точек:", len(points), "Границы координат:", route.preview().bounds)
        if points:
            print("Путь начинается прямым движением от текущей позы к первой точке.")
            answer = input("Если путь свободен, очистить очередь и пройти все точки? [да/нет] ")
            if answer.strip().lower() == "да":
                arm.stop(clear=True)
                with arm.sequence():
                    arm.speed(10, 30)
                    arm.run(route)
