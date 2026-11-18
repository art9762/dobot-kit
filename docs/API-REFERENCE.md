# Справочник Python API

`Dobot`, `Point`, `Pose`, `Path`, `Bounds`, `pen_path` и `discover` импортируются из `dobot_usb`. Ниже `robot` обозначает открытое соединение с одним классическим Magician. Расстояния указаны в мм, углы в градусах, длительности в секундах. [Примеры использования](API.md)

## Подключение и чтение

| Вызов | Результат |
| --- | --- |
| `discover()` | Список `PortInfo` с полями `device`, `description`, `vid`, `pid`, `serial_number`. Перечисляет кандидатов по USB-мосту. Конкретный порт выбирает оператор. |
| `Dobot.connect(port, *, timeout=1.0, read_only=False)` | Открывает указанный порт, проверяет чтение состояния и возвращает `Dobot`. `timeout` ограничивает один обмен. Режим `read_only=True` разрешает диагностические запросы. |
| `robot.device_info()` | `DeviceInfo(firmware, firmware_raw, serial_number, name)`. Пустой или стёртый идентификатор даёт `None`. |
| `robot.pose()` | `Pose(x, y, z, r, joints)` с четырьмя углами суставов. |
| `robot.alarms()` | Кортеж номеров активных битов ошибок. Пустой кортеж означает, что контроллер не сообщил ошибок. |
| `robot.status()` | `Status(device, pose, alarms)` из последовательных чтений. |
| `robot.tool_state(tool)` | `ToolState(enabled, active)` для `"suction"`, `"gripper"` или `"laser"`. |
| `robot.queue_index()` | Текущий индекс исполненной команды по данным контроллера. |
| `robot.close()` | Освобождает порт. Клиент при закрытии не посылает команд остановки или отключения выходов. Контекстный менеджер `with Dobot.connect(port) as robot:` вызывает `close()` при выходе. |

## Движение

Следующие методы доступны у `robot` и `robot.motion`. Все возвращают `CommandHandle`.

```text
move_to(x, y, z, r=None, *, path="linear", wait=True, timeout=30)
move_to(point_or_pose, *, path="linear", wait=True, timeout=30)
move_by(*, x=0, y=0, z=0, r=0, path="linear", wait=True, timeout=30)
move_joints(j1, j2, j3, j4, *, relative=False, wait=True, timeout=30)
speed(*, mm_s, mm_s2, deg_s=None, deg_s2=None)
home(*, wait=True, timeout=60)
```

Сохраните положение через `saved = robot.pose()`. После других перемещений вернуться к этим XYZ/R можно вызовом `robot.move_to(saved)`. Для возврата по суставным углам передайте `*saved.joints` в `move_joints`. При числовом вызове без R библиотека ждёт предыдущие команды клиента и читает текущий R. Для `wait=False` передайте R явно либо передайте `Point` или `Pose`.

Для абсолютного движения `path` принимает `"linear"`, `"joint"` или `"jump"`. Для относительного доступны первые два. До первого JUMP задайте `robot.motion.jump(height=..., z_limit=...)`. `speed()` задаёт номинальную декартову скорость PTP. Пропущенные угловые параметры считываются с контроллера, процентные коэффициенты задаются через `robot.settings`.

У группы `robot.motion` также есть следующие методы.

```text
ptp(mode, x, y, z, r, *, wait=True, timeout=30)
jump(*, height, z_limit)
arc(via: Point, to: Point, *, wait=True, timeout=30)
line_to(x, y, z, *, velocity=0, wait=True, timeout=30)
line_by(*, x=0, y=0, z=0, velocity=0, wait=True, timeout=30)
jog(axis, direction, *, duration, timeout=30)
jog_stop(*, is_joint=False, timeout=30)
wait_for_pose(target: Point, *, timeout=5, linear_tolerance_mm=0.3, angular_tolerance_deg=0.65)
sequence()
```

`ptp` принимает `PTPMode` или номер режима 0–9. Методы `line_to` и `line_by` используют CP, у которого зарезервирован аргумент `velocity=0`. Параметры CP задаются через `cp_params`. Для `jog` ось — одна из `x/y/z/r/j1/j2/j3/j4`, направление — `1` или `-1`, длительность — больше нуля и до 5 секунд включительно.

`wait_for_pose` возвращает второе из двух согласующихся чтений позы без ошибок контроллера. В блоке `with robot.motion.sequence():` остановка, пауза или очистка прерывает дальнейшую отправку команд исходной последовательности. Параллельные задания на одной руке согласует вызывающая программа. [Движение и маршруты](MOTION.md)

## Очередь и результат команды

| Вызов | Действие |
| --- | --- |
| `robot.queue.index(*, timeout=30)` | Прочитать индекс. |
| `robot.queue.wait(*, timeout=30)` | Дождаться последней к моменту вызова принятой команды очереди этого клиента. |
| `robot.queue.delay(seconds, *, wait=True, timeout=30)` | Записать ожидание в очередь контроллера. |
| `robot.queue.trigger(*, wait=True, timeout=30, **params)` | Ждать срабатывания входа с параметрами команды `trigger`. |
| `robot.queue.start(*, timeout=30)` / `pause(*, timeout=30)` | Запустить или приостановить очередь. |
| `robot.queue.clear(*, timeout=30)` | Удалить ожидающие команды, отозвать прежние квитанции. |
| `robot.stop(*, clear=True, timeout=30)` / `robot.queue.stop(...)` | Запросить принудительную остановку очереди, затем очистить её при `clear=True`. Управление выходами выполняется отдельно. |

`wait=False` у команды очереди возвращает квитанцию, когда контроллер принял команду. `handle.wait(timeout=30)` ждёт её индекса. У немедленной команды `queue_index` равен `None`, повторное `handle.wait()` вызывает `OutcomeUnknown`. `handle.cancel()` отменяет ожидание по конкретной квитанции. Уже принятая команда остаётся у контроллера.

У квитанции доступны поля `command`, `queue_index`, `session_id`, `state`. При ошибке `DobotError.stage` указывает, что известно об отправке и приёме команды. При тайм-ауте отправленные действия автоматически не повторяются. [Очередь, ошибки и квитанции](EXECUTION.md)

## Маршруты и перо

```text
Point(x, y, z, r)
Bounds(minimum: Point, maximum: Point)
Path(points=())
path.then(point) -> Path
path.preview(bounds=None) -> Preview
path.run(robot.motion, *, bounds=None, wait=True, timeout=30) -> tuple[CommandHandle, ...]
pen_path(origin, strokes, *, start, lift_mm=3, dry_run=False, bounds=None) -> Path
robot.pen.check_outputs() -> None
robot.pen.draw(strokes, *, origin, lift_mm=3, dry_run=False, bounds=None, timeout=30) -> PenReport
```

`Path` хранит абсолютные точки линейных перемещений. `preview()` вычисляет фактические границы точек, `run()` проверяет весь список и отправляет его по порядку. `Bounds` ограничивает значения XYZ/R. Достижимость и свободный ход учитывает оператор.

Для `pen_path` и `robot.pen.draw` каждый штрих — список пар `(dx, dy)` в мм от обученной точки лёгкого касания горизонтального листа. Точку касания передают в `origin` как `Point` или `Pose`. `pen_path` строит маршрут в памяти от переданной стартовой позы. `draw` считывает актуальную позу, проверяет отключение активных инструментов и выполняет маршрут с подъёмами между штрихами и в конце. `dry_run=True` при вызове `draw` физически проводит пером над листом. Для каждой цели `timeout` применяется к отдельным ожиданиям и движению.

`PenReport` хранит `start`, `route`, `completed_points` и кортеж `checkpoints`. В каждой подтверждённой точке записаны `target`, `actual`, `queue_index` и `observed_queue_index`. При ошибке во время исполнения `exc.pen_report` содержит подтверждённую часть и `unconfirmed_queue_index`, если индекс был получен. [Ограничения маршрута и пример](MOTION.md#перо-на-плоском-листе)

## Сменная оснастка

```text
robot.suction.pick_and_place(pick: Point | Pose, place: Point | Pose, *, lift_mm: float) -> None
robot.gripper.pick_and_place(pick: Point | Pose, place: Point | Pose, *, lift_mm: float) -> None
```

Оба метода берут предмет в обученной точке `pick`, укладывают в `place` и заканчивают над местом укладки. Переход проходит на высоте наибольшего Z старта, захвата и укладки плюс `lift_mm`. Каждая цель проверяется по позе, состояние инструмента считывается после переключения выхода.

| Объект | Методы |
| --- | --- |
| `robot.suction` | `on()`, `off()`, `state()` |
| `robot.gripper` | `open()` разводит губки, `close()` сводит их, `release()` отключает управление, `state()` читает выход. |
| `robot.laser` | `arm()` и `disarm()` меняют локальное разрешение, `armed` показывает его состояние. `on()` и `off()` отправляют команды выходу, `state()` читает два бита его состояния. |
| `robot.tool` | `select(name)` выбирает локальный профиль `pen`, `suction`, `gripper` или `laser`. `current` хранит выбранный профиль. `offset(*, x, y, z, wait=True, timeout=30)` записывает смещение TCP в мм, `get_offset()` его читает. |

Методы управления выходами принимают именованные `wait=True` и `timeout=30`. Выход лазера переключается немедленной командой. Удержание предмета и результат работы оснастки проверяют на месте. [Работа с инструментами](TOOLS.md)

## 3D-печать

Для штатной печатающей головки применяется отдельный режим **3D Printer Firmware**. Подключение принтера, работа с готовым заданием и запуск через Python описаны в [руководстве по печати](PRINTING.md).

Импортируй `Printer` из `dobot_usb.printing`. Открывай соединение через `with Printer.connect(port, timeout=5.0) as printer:`.

| Вызов | Результат |
| --- | --- |
| `printer.firmware` | Имя прошивки из ответа контроллера |
| `printer.temperature()` | `Temperature(current_c, target_c)` в °C. Уставка может быть `None` |
| `printer.print_file(path, wait_timeout=300.0)` | `PrintResult(path, acknowledged_commands)` после подтверждения строк и завершения очереди движений прошивки |

`dobot_usb.printing.inspect_gcode(path)` проверяет структуру нарезанного файла и возвращает `PrintJob(path, commands, motion_commands, extruder_moves)`. Счётчик `extruder_moves` учитывает команды движения с параметром E, включая ретракты. Если нужно открыть файл в установленном хосте, вызовите `dobot_usb.printing.open_print_job(path, application=...)`. Дальнейшим запуском задания и портом в этом режиме управляет хост.

## Периферия и настройки

Методы ниже вызываются у соответствующего объекта открытого соединения. Типы `IOFunction` и `TeachingMode` импортируются из `dobot_usb.peripherals`. [Диапазоны, единицы и форматы результатов](PERIPHERALS.md)

### `robot.io`

```text
multiplex(address: int, function: IOFunction, *, wait: bool = True, timeout: float = 30.0) -> CommandHandle
multiplexing(address: int) -> IOFunction
write(address: int, level: bool, *, wait: bool = True, timeout: float = 30.0) -> CommandHandle
read(address: int) -> bool
output(address: int) -> bool
pwm(address: int, *, frequency: float, duty_cycle: float, wait: bool = True, timeout: float = 30.0) -> CommandHandle
pwm_state(address: int) -> PWMState
analog(address: int) -> int
```

### `robot.motors`

```text
run(index: int, speed_steps_s: int, *, wait: bool = True, timeout: float = 30.0) -> CommandHandle
stop(index: int, *, wait: bool = True, timeout: float = 30.0) -> CommandHandle
```

### `robot.conveyor`

```text
configure(*, index: int, steps_per_mm: float) -> None
run(mm_s: float, *, wait: bool = True, timeout: float = 30.0) -> CommandHandle
stop(*, wait: bool = True, timeout: float = 30.0) -> CommandHandle
```

### `robot.sensors`

```text
configure_color(*, enabled: bool, port: int, version: int, wait: bool = True, timeout: float = 30.0) -> CommandHandle
color() -> tuple[int, int, int]
configure_infrared(*, enabled: bool, port: int, version: int, wait: bool = True, timeout: float = 30.0) -> CommandHandle
infrared(*, port: int) -> bool
```

### `robot.rail`

```text
position() -> float
configuration() -> dict[str, object]
configure(*, enabled: bool, version: int, wait: bool = True, timeout: float = 30.0) -> CommandHandle
parameters(*, jogging: bool = False) -> RailParameters
speed(*, mm_s: float, mm_s2: float, jogging: bool = False, wait: bool = True, timeout: float = 30.0) -> CommandHandle
move_to(position: float, *, x: float, y: float, z: float, r: float, path: Literal['linear', 'joint'] = 'linear', wait: bool = True, timeout: float = 30.0) -> CommandHandle
```

### `robot.teaching`

```text
mode() -> TeachingMode
set_mode(mode: TeachingMode, *, wait: bool = True, timeout: float = 30.0) -> CommandHandle
enabled() -> bool
enable(enabled: bool = True, *, wait: bool = True, timeout: float = 30.0) -> CommandHandle
triggered() -> bool
```

### `robot.settings`

```text
tcp() -> ToolOffset
set_tcp(*, x: float, y: float, z: float, wait: bool = True, timeout: float = 30.0) -> CommandHandle
identification() -> dict[str, dict[str, object]]
set_name(name: str, *, wait: bool = True, timeout: float = 30.0) -> CommandHandle
set_serial_number(serial_number: str, *, wait: bool = True, timeout: float = 30.0) -> CommandHandle
home_position() -> HomePosition
set_home_position(*, x: float, y: float, z: float, r: float, wait: bool = True, timeout: float = 30.0) -> CommandHandle
velocity_ratios() -> VelocityRatios
set_velocity_ratios(*, velocity: float, acceleration: float, wait: bool = True, timeout: float = 30.0) -> CommandHandle
alarms() -> dict[str, object]
clear_alarms(*, wait: bool = True, timeout: float = 30.0) -> CommandHandle
angle_sensor_errors() -> AngleSensorErrors
set_angle_sensor_errors(*, rear_arm_angle_error: float, front_arm_angle_error: float, wait: bool = True, timeout: float = 30.0) -> CommandHandle
reset_pose(*, manual: bool, rear_arm_angle: float, front_arm_angle: float, wait: bool = True, timeout: float = 30.0) -> CommandHandle
set_lost_step_threshold(value: float, *, wait: bool = True, timeout: float = 30.0) -> CommandHandle
check_lost_steps(*, wait: bool = True, timeout: float = 30.0) -> CommandHandle
```

### `robot.network`

```text
config_mode() -> bool
set_config_mode(enabled: bool, *, wait: bool = True, timeout: float = 30.0) -> CommandHandle
ssid() -> str
set_ssid(ssid: str, *, wait: bool = True, timeout: float = 30.0) -> CommandHandle
password() -> WifiPassword
set_password(password: str, *, wait: bool = True, timeout: float = 30.0) -> CommandHandle
ip_address() -> dict[str, object]
set_ip_address(address: str, *, dhcp: bool, wait: bool = True, timeout: float = 30.0) -> CommandHandle
netmask() -> dict[str, object]
set_netmask(address: str, *, wait: bool = True, timeout: float = 30.0) -> CommandHandle
gateway() -> dict[str, object]
set_gateway(address: str, *, wait: bool = True, timeout: float = 30.0) -> CommandHandle
dns() -> dict[str, object]
set_dns(address: str, *, wait: bool = True, timeout: float = 30.0) -> CommandHandle
connected() -> bool
```

## Именованные команды протокола

`robot.advanced.commands` перечисляет имена команд. `describe(name)` возвращает `CommandSpec` с ID, допустимыми направлениями и правилами очереди. `get(name, **params)` возвращает словарь полей ответа. `set(name, /, *, wait=True, timeout=30, queued=None, **params)` возвращает квитанцию, а для команд управления очередью — `None`. `read(command)` возвращает исходные байты для диагностических ID 0, 1, 2, 10, 20, 61, 62, 63, 246. Таблицу поддерживаемых команд можно посмотреть без подключения командой `dobot commands`. [Поля всех операций протокола](PROTOCOL-IMPLEMENTATION.md)
