"""Teach Cartesian waypoints by hand. Preview before choosing to run the route."""

from dobotkit import Dobot, Path, Point

if __name__ == "__main__":
    port = input("Порт руки: ").strip()
    with Dobot.connect(port) as arm:
        points = []
        print("Подведи инструмент к точке, отпусти Unlock и нажми Enter.")
        while input("Enter — записать позу, q — закончить: ").strip().lower() != "q":
            pose = arm.pose()
            points.append(Point(pose.x, pose.y, pose.z, pose.r))
        route = Path(points)
        print("Точек:", len(points), "Границы координат:", route.preview().bounds)
        if points:
            print("Путь начинается прямым движением от текущей позы к первой точке.")
            answer = input("Если путь свободен, очистить очередь и пройти все точки? [да/нет] ")
            if answer.strip().lower() == "да":
                arm.stop(clear=True)
                with arm.motion.sequence():
                    arm.speed(mm_s=10, mm_s2=30)
                    route.run(arm.motion)
