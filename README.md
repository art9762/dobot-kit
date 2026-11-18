# Dobot Kit

Python-библиотека для классического **DOBOT Magician**. Подключаешь руку по USB, читаешь позу и вызываешь методы нужной насадки. Координаты задаются в миллиметрах, повороты — в градусах.

[PyPI](https://pypi.org/project/dobot-usb/) · [Документация](https://art9762.github.io/dobot-kit/) · [Примеры Python](https://github.com/art9762/dobot-kit/tree/v0.2.0a4/examples) · [Скачать версию 0.2.0a4](https://github.com/art9762/dobot-kit/releases/tag/v0.2.0a4)

## Первые 15 минут

### 1. Установить · 2 минуты

Установи или обнови `dobot-usb` в окружении Python 3.11 или новее. Короткие вызовы в примерах доступны с версии 0.2.0a4.

```sh
python -m pip install -U dobot-usb
```

Подключи Magician по USB и посмотри, какой порт ему назначила система.

```sh
dobot ports
```

На macOS и Linux команда Python может называться `python3`, на Windows — `py`. Если Python просит отдельное окружение, создай его через `python -m venv .venv`, затем активируй через `source .venv/bin/activate` или `.\.venv\Scripts\Activate.ps1` в Windows PowerShell.

`dobot ports` показывает кандидатов. Скопируй поле `device` своей руки, например `COM3` на Windows или путь `/dev/cu.…` на macOS. Если список пуст, проверь кабель USB и наличие порта в ОС. Известный порт можно указать вручную. Если рук несколько, первый раз подключи их по одной и подпиши порты.

### 2. Узнать положение · 3 минуты

Освободи порт в DobotStudio или пульте. Сохрани программу как `arm.py` и выполни `python arm.py`.

```python
from dobot_usb import connect

port = input("Порт руки: ").strip()
with connect(port, read_only=True) as arm:
    state = arm.status()
    print("Прошивка:", state.device.firmware)
    print("XYZ, мм:", state.pose.x, state.pose.y, state.pose.z)
    print("Ошибки:", state.alarms)
```

Ты увидишь координаты именно этой руки. Пустой кортеж `()` означает, что контроллер не сообщает ошибок. Ту же проверку из терминала делает `dobot status COM3` со своим портом.

### 3. Запомнить позу и вернуться · 4 минуты

Для первого движения освободи пространство над инструментом и убедись, что старая очередь больше никому не нужна. Пример рассчитан на свободную руку со стандартной прошивкой и выключенными выходами. Сохрани его как `move.py` и запусти `python move.py`. Рука поднимется на 2 мм и вернётся в запомненную позу.

```python
from dobot_usb import connect

port = input("Порт руки: ").strip()
with connect(port) as arm:
    arm.stop(clear=True)  # Отбросить прежние задания перед новым сеансом
    with arm.sequence():
        start = arm.pose()
        arm.speed(5, 10)
        arm.shift(z=2)
        print("Z до и после подъёма:", start.z, arm.pose().z)
        arm.move(start)
```

`shift` задаёт смещение, `move` принимает сохранённую позу или координаты XYZ. `sequence()` связывает несколько действий в одно задание: остановка прерывает его и между точками. `speed(5, 10)` задаёт 5 мм/с и 10 мм/с². Настройка сохраняется в контроллере. [Скорости и движение](https://github.com/art9762/dobot-kit/blob/v0.2.0a4/docs/MOTION.md)

### 4. Выбрать инструмент · 6 минут

| Что установлено | Что можно сделать из Python | С чего начать |
| --- | --- | --- |
| Присоска | `arm.suction.transfer(pick, place, 10)` переносит предмет между обученными точками | [Перенос между двумя обученными точками](https://github.com/art9762/dobot-kit/blob/v0.2.0a4/examples/pick_and_place.py) |
| Пневмосхват | Тот же вызов через `arm.gripper.transfer(...)`. Губками можно отдельно управлять через `open()` и `close()` | [Захват и перенос](https://github.com/art9762/dobot-kit/blob/v0.2.0a4/examples/gripper.py) |
| Держатель пера | `arm.draw(strokes, paper)` рисует XY-штрихи с подъёмом между ними | [Два штриха на листе](https://github.com/art9762/dobot-kit/blob/v0.2.0a4/docs/MOTION.md#перо-на-плоском-листе) |
| Лазер | `arm.laser.arm()` разрешает включение, `on()` и `off()` управляют выходом | [Работа с лазерной головкой](https://github.com/art9762/dobot-kit/blob/v0.2.0a4/docs/TOOLS.md) |
| Головка 3D-печати | В режиме принтерной прошивки `Printer.connect(port)` открывает соединение, `printer.print("part.gcode")` отправляет нарезанный файл | [Печать из Python](https://github.com/art9762/dobot-kit/blob/v0.2.0a4/docs/PRINTING.md) |

Для пера `paper = arm.pose()` запоминает положение кончика при лёгком касании листа. Две линии длиной 5 мм задаются так:

```python
strokes = [[(0, 0), (5, 0)], [(5, 3), (0, 3)]]
report = arm.draw(strokes, paper)
print(report.completed_points)
```

Этот фрагмент выполняется внутри открытого соединения. Метод закончит рисунок с поднятым пером. `dry_run=True` выполняет реальный пробный проход **над** бумагой. Для проверки координат без движения используй `pen_path(...).preview()`. [Выбор насадки и настройка её смещения](https://github.com/art9762/dobot-kit/blob/v0.2.0a4/docs/TOOLS.md)

## Совместимость

Вызовы из 0.2.0a3, включая `Dobot.connect`, `move_to` и `pen.draw`, продолжают работать. [Короткие имена и сигнатуры](https://github.com/art9762/dobot-kit/blob/v0.2.0a4/docs/API-REFERENCE.md)

### Переход с версии 0.2.0a2

Если ты ставил `dobot-kit` из GitHub, создай новое окружение, установи `dobot-usb` и замени `dobotkit` на `dobot_usb` в импортах своих скриптов. Например, `from dobotkit import Dobot` станет `from dobot_usb import Dobot`. [Предыдущий релиз на GitHub](https://github.com/art9762/dobot-kit/releases/tag/v0.2.0a2)

## Когда что-то пошло не так

- **Порт занят или рук несколько.** Закрой программу, которая держит порт, и проверь соответствие по `dobot status …`. Каждой руке нужен собственный клиент. [Две руки в одном скрипте](https://github.com/art9762/dobot-kit/blob/v0.2.0a4/docs/API.md#несколько-рук)
- **История очереди неизвестна.** Начни с чтения состояния. Первая запись в очередь может запустить оставшиеся движения. Вызывай `stop(clear=True)`, только когда предыдущее задание можно отбросить.
- **Команда получила таймаут.** Сначала выясни состояние руки. Если контроллер уже принял движение, повторная отправка может выполнить его дважды. `arm.stop()` запрашивает остановку и очистку очереди по действующей связи. Выход из `with` освобождает порт. Инструменты выключаются отдельно. [Остановка, ошибки и ожидание](https://github.com/art9762/dobot-kit/blob/v0.2.0a4/docs/EXECUTION.md)

[API](https://github.com/art9762/dobot-kit/blob/v0.2.0a4/docs/API.md) · [Сигнатуры](https://github.com/art9762/dobot-kit/blob/v0.2.0a4/docs/API-REFERENCE.md) · [Рельс, конвейер, I/O и датчики](https://github.com/art9762/dobot-kit/blob/v0.2.0a4/docs/PERIPHERALS.md) · [Матрица протокола](https://github.com/art9762/dobot-kit/blob/v0.2.0a4/docs/CAPABILITIES.md) · [Проверки версии](https://github.com/art9762/dobot-kit/blob/v0.2.0a4/docs/VALIDATION.md)

На Magician 4.0.3 оператор наблюдал два отдельных штриха и цифры «67» на бумаге при испытании предыдущей версии. Результаты по прошивкам и оснастке собраны в [аппаратном отчёте](https://github.com/art9762/dobot-kit/blob/v0.2.0a4/docs/HARDWARE.md).
