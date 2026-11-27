# Dobot Kit

Управление классическим четырёхосевым **DOBOT Magician** из Python по USB. Библиотека умеет читать положение руки, двигаться по координатам, рисовать фигуры, управлять присоской и схватом, читать RGB и сбрасывать аварии контроллера. Требуется Python 3.11 или новее.

[Установка и примеры](https://art9762.github.io/dobot-kit/guide.html) · [Справочник Python API](https://art9762.github.io/dobot-kit/api.html) · [PyPI](https://pypi.org/project/dobot-usb/0.3.0a4/)

## От установки до первого чтения

Включи питание робота, подключи USB и освободи порт в DobotStudio. Выполни эти команды в активном окружении Python.

```sh
python -m pip install --upgrade dobot-usb==0.3.0a4
python -m serial.tools.list_ports
```

Вместо `COM3` подставь порт из списка. На macOS это обычно путь вида `/dev/cu.…`, на Linux — `/dev/ttyUSB…`. Первый пример только читает положение. При выходе из `with` порт освобождается, в том числе при исключении.

```python
from dobot_usb import Dobot

with Dobot("COM3") as robot:
    x, y, z, angle = robot.position()
    print(f"X={x:.1f}  Y={y:.1f}  Z={z:.1f} мм, угол={angle:.1f}°")
```

## Координаты и движение

| Вызов | Как выбирается цель |
| --- | --- |
| `move(x, y, z, angle=None)` | Абсолютная точка. Если угол пропущен, сохраняется текущий. |
| `set_x(x)`, `set_y(y)`, `set_z(z)`, `set_angle(angle)` | Абсолютное значение одной координаты. Остальные берутся из текущей позы. |
| `calibrate(position=None)`, `origin` | Запоминает начало отсчёта в открытом объекте Python. Без аргумента читает текущую позу. Свойство `origin` возвращает сохранённую точку. |
| `move_x(offset)`, `move_y(offset)`, `move_z(offset)`, `move_angle(offset)` | Для выбранной оси цель равна сохранённому началу плюс смещение. Остальные координаты сохраняются. |
| `move_relative(dx=0, dy=0, dz=0, dangle=0)` | Задаёт все четыре координаты относительно начала. Оси с нулевым смещением возвращаются к значению из `origin`. |

Например, при сохранённом Z = 50 мм вызов `move_z(2)` задаёт Z = 52 мм. Повторный вызов снова задаёт 52 мм. Значения X, Y и Z задаются в миллиметрах, угол — в градусах. Для относительных движений сначала вызови `calibrate()`. Вызов `home()` запускает отдельную процедуру контроллера, во время которой рука движется. [Координаты и калибровка](https://art9762.github.io/dobot-kit/api.html#calibrate)

`speed(percent)` задаёт скорость и ускорение PTP в процентах от 1 до 100. При первом действии с очередью библиотека останавливает и очищает оставшиеся команды предыдущего сеанса. После перемещения она сверяет цель с двумя последовательными показаниями контроллера. [Очередь и подтверждение движения](https://art9762.github.io/dobot-kit/behavior.html#queue)

### Рисование

Доступны `draw_line(start, end)`, `draw_rectangle(start, width, height, *, plane="xy")`, `draw_square(start, side, *, plane="xy")`, `draw_triangle(start, side, *, plane="xy")` и `draw_circle(start, radius, *, segments=72, plane="xy")`.

Координаты абсолютные. Рука сначала идёт по прямой к `start`, затем проходит контур. Для первой пробы выбери свободную высоту над столом и проверь весь путь. Например, `robot.draw_square(robot.position(), 5, plane="xy")` начинает квадрат 5 × 5 мм в текущей точке и идёт в сторону увеличения X и Y. Плоскость задаётся по имени и принимает `"xy"`, `"xz"` или `"yz"`. Для окружности `start` лежит на контуре. Подъём пера между фигурами задаёт программа оператора. [Геометрия фигур и параметры](https://art9762.github.io/dobot-kit/api.html#drawing)

## Присоска и схват

`robot.suck(True)` включает присоску, `robot.suck(False)` выключает. Оба вызова принимают значения Python типа `bool`. Для этого примера установи присоску и оставь предмет на опоре.

```python
from dobot_usb import Dobot

with Dobot("COM3") as robot:
    robot.suck(True)
    try:
        input("Оставь предмет на опоре и нажми Enter, чтобы выключить присоску. ")
    finally:
        robot.suck(False)
```

Для схвата используй `gripper_open()`, `gripper_close()` и `gripper_off()`. Последняя команда отключает управляющий выход. Удержание предмета проверяй перед подъёмом. В старых программах продолжают работать `suction_on()` и `suction_off()`. [Управление оснасткой](https://art9762.github.io/dobot-kit/api.html#tools)

## Сброс ошибок контроллера

После аварии, прервавшей движение, вызови `robot.stop()`, чтобы остановить и очистить очередь. Устранив причину аварии, вызови `robot.clear()` на открытом соединении. Метод считывает аварийные флаги после сброса. При пустом наборе он возвращает `None`. Если флаги остались, их номера доступны в `error.alarms`.

```python
from dobot_usb import Dobot
from dobot_usb.errors import RobotAlarmError

with Dobot("COM3") as robot:
    try:
        robot.clear()
    except RobotAlarmError as error:
        print("Остались активные коды:", error.alarms)
        raise
```

После сброса для нового движения нужна отдельная команда. `close()` и выход из `with` освобождают USB-порт. Чтобы остановить очередь или отключить выход оснастки, вызови соответствующий метод явно. [Восстановление после сбоя](https://art9762.github.io/dobot-kit/behavior.html#recovery)

## Датчик цвета

Выбери порт, к которому физически подключён штатный датчик Magician, и его поколение. Допустимые имена портов — `GP1`, `GP2`, `GP4`, `GP5`, поколения датчика — 1 и 2. В примере ниже датчик второго поколения подключён к GP2.

```python
from dobot_usb import Dobot

with Dobot("COM3") as robot:
    robot.color_sensor(port="GP2", version=2)
    try:
        print(robot.read_color())  # (R, G, B), каналы от 0 до 255
    finally:
        robot.color_sensor(False, port="GP2", version=2)
```

Для опроса используй `watch_color(handler)`. По умолчанию обработчик получает первое показание и затем каждое изменение RGB. Возврат `False` из обработчика завершает опрос. Нулевой RGB сам по себе не позволяет судить о подключении датчика. [Параметры опроса](https://art9762.github.io/dobot-kit/api.html#color) · [Совместимость датчика](https://github.com/art9762/dobot-kit/blob/v0.3.0a4/docs/color-sensor-research.md)

## Примеры и проверка

В репозитории и исходном архиве есть `examples/check_robot.py`. Запускай его из корня распакованных исходников с установленной библиотекой. Выбирай команду для своей задачи.

| Команда | Что произойдёт |
| --- | --- |
| `python examples/check_robot.py --port COM3 position` | Прочитает позу и запомнит её до выхода из программы. |
| `python examples/check_robot.py --port COM3 color --sensor-port GP2 --sensor-version 2` | Настроит выбранный датчик и выведет десять показаний RGB. |
| `python examples/check_robot.py --port COM3 motion --axis z --offset 2` | Покажет цель и запросит подтверждение `MOVE`. При подтверждении установит скорость и ускорение 5%, выполнит смещение и возврат. Настройки останутся на 5%. |

Выпуск **0.3.0a4** обновляет документацию к API версии **0.3.0a3**. Автотесты используют имитацию USB. На физическом Magician с прошивкой 4.0.3 ранее проверялись команды версии 0.3.0a2. Для `clear()` и `suck()` отдельно проверен обмен с имитатором. [Результаты и границы проверок](https://art9762.github.io/dobot-kit/development.html#verification) · [История выпусков](https://github.com/art9762/dobot-kit/blob/v0.3.0a4/CHANGELOG.md)

## Разработка

```sh
uv sync --locked
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv build
```

[Устройство библиотеки](https://art9762.github.io/dobot-kit/development.html#layout) · [Протокол USB](https://art9762.github.io/dobot-kit/protocol.html) · [Лицензия MIT](https://github.com/art9762/dobot-kit/blob/v0.3.0a4/LICENSE)
