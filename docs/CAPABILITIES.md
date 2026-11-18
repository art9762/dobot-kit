# Матрица функциональности

База реестра — 69 подразделов 1.3.1–1.19.7 Communication Protocol V1.1.5 для классического Magician со стандартной прошивкой. Их названия связывают API с документацией контроллера. Имена для обычных задач и отдельных инструментов приведены в [справочнике](API-REFERENCE.md). [Версии источников](SOURCES.md)

Кодеки поддерживают **67 из 69 подразделов**, включая 47 направлений GET и 56 SET. Для `ptp_po_with_l` и `queue_start_download` спецификация и официальный код расходятся по формату. Обе команды вызывают `UnsupportedCommand` до отправки. [Разбор форматов](PROTOCOL-IMPLEMENTATION.md)

В таблицах R означает чтение, W — запись. Статус «офлайн» означает проверку байтов и ответа без устройства. Пометка USB относится лишь к указанному действию. На версии 3.7.0 короткий маршрут подтверждён по показаниям контроллера. На версии 4.0.3 оператор увидел на бумаге два отдельных штриха и рисунок «67». Для остальных оснасток факт действия требует отдельной проверки. [Аппаратный отчёт](HARDWARE.md)

## Базовые функции

| Раздел | Команда / возможность | ID | Операции | Кодек и проверка |
| --- | --- | --- | --- | --- |
| 1.3.1 | DeviceSN | 0 | R/W | `device_sn` · USB, чтение при подключении |
| 1.3.2 | DeviceName | 1 | R/W | `device_name` · USB, чтение при подключении |
| 1.3.3 | DeviceVersion | 2 | R | `device_version` · USB, 3.7.0 и 4.0.3 |
| 1.3.4 | DeviceWithL: наличие rail | 3 | R/W | `device_with_l` · USB, чтение конфигурации неподвижного основания |
| 1.3.5 | DeviceTime | 4 | R | `device_time` · офлайн |
| 1.3.6 | DeviceID | 5; PDF: 4 | R | `device_id` · офлайн |
| 1.4.1 | Pose: XYZ/R и J1–J4 | 10 | R | `pose` · USB, чтение XYZ/R и суставов |
| 1.4.2 | ResetPose | 11 | W | `reset_pose` · офлайн |
| 1.4.3 | PoseL | 13 | R | `pose_l` · офлайн |
| 1.5.1 | AlarmsState | 20 | R | `alarms` · USB, чтение |
| 1.5.2 | ClearAllAlarmsState | 20 write; PDF: 21 | W | `clear_alarms` · офлайн |
| 1.6.1 | HOMEParams | 30 | R/W | `home_params` · офлайн |
| 1.6.2 | HOMECmd | 31 | W | `home` · офлайн |
| 1.6.3 | AutoLeveling | 32; PDF: 30 | R/W | `auto_leveling` · офлайн |
| 1.7.1 | HHTTrigMode | 40 | R/W | `hht_trigger_mode` · офлайн |
| 1.7.2 | HHTTrigOutputEnabled | 41 | R/W | `hht_output_enabled` · офлайн |
| 1.7.3 | HHTTrigOutput | 42 | R | `hht_trigger_output` · офлайн |
| 1.8.1 | EndEffectorParams: смещение инструмента | 60 | R/W | `end_effector_params` · USB, чтение TCP |
| 1.8.2 | EndEffectorLaser | 61 | R/W | `laser` · USB, чтение неактивного выхода |
| 1.8.3 | EndEffectorSuctionCup | 62 | R/W | `suction` · USB, чтение неактивного выхода |
| 1.8.4 | EndEffectorGripper | 63 | R/W | `gripper` · USB, чтение неактивного выхода |

## Движение

| Раздел | Команда / возможность | ID | Операции | Кодек и проверка |
| --- | --- | --- | --- | --- |
| 1.9.1 | JOGJointParams | 70 | R/W | `jog_joint_params` · офлайн |
| 1.9.2 | JOGCoordinateParams | 71 | R/W | `jog_coordinate_params` · офлайн |
| 1.9.3 | JOGCommonParams | 72 | R/W | `jog_common_params` · офлайн |
| 1.9.4 | JOGCmd: ось / направление / остановка | 73 | W | `jog` · офлайн |
| 1.9.5 | JOGLParams | 74 | R/W | `jog_l_params` · офлайн |
| 1.10.1 | PTPJointParams | 80 | R/W | `ptp_joint_params` · USB, чтение, запись и восстановление |
| 1.10.2 | PTPCoordinateParams | 81 | R/W | `ptp_coordinate_params` · USB, чтение, запись и восстановление |
| 1.10.3 | PTPJumpParams | 82 | R/W | `ptp_jump_params` · офлайн |
| 1.10.4 | PTPCommonParams | 83 | R/W | `ptp_common_params` · USB, чтение, запись и восстановление |
| 1.10.5 | PTPCmd: все документированные modes | 84 | W | `ptp` · USB, MOVL с пером (3.7.0, 4.0.3), след на бумаге на 4.0.3 |
| 1.10.6 | PTPLParams | 85 | R/W | `ptp_l_params` · офлайн |
| 1.10.7 | PTPWithLCmd | 86 | W | `ptp_with_l` · офлайн |
| 1.10.8 | PTPJump2Params | 87 | R/W | `ptp_jump2_params` · офлайн |
| 1.10.9 | PTPPOCmd: движение и вывод | 88 | W | `ptp_po` · офлайн |
| 1.10.10 | PTPPOWithLCmd | 89 | W | Явный отказ: `ptp_po_with_l` |
| 1.11.1 | CPParams | 90 | R/W | `cp_params` · офлайн |
| 1.11.2 | CPCmd | 91 | W | `cp` · USB, пробный подъём по телеметрии без видимого движения |
| 1.11.3 | CPLECmd: траектория и мощность лазера | 92 | W | `cp_laser` · офлайн |
| 1.12.1 | ARCParams | 100 | R/W | `arc_params` · офлайн |
| 1.12.2 | ARCCmd | 101 | W | `arc` · офлайн |
| 1.13.1 | WAITCmd: ожидание в очереди | 110 | W | `wait` · офлайн |
| 1.14.1 | TRIGCmd: ожидание входа | 120 | W | `trigger` · офлайн |

Перечисление `PTPMode` сохраняет десять режимов из таблицы протокола, включая JUMP с линейным участком. Для абсолютных XYZ доступны MOVL, MOVJ и JUMP, для относительных XYZ — MOVL и MOVJ. Координированное движение с рельсой принимает полную цель XYZ/R/L. [Методы движения](MOTION.md)

## I/O, периферия и настройки

| Раздел | Команда / возможность | ID | Операции | Кодек и проверка |
| --- | --- | --- | --- | --- |
| 1.15.1 | IOMultiplexing | 130 | R/W | `io_multiplexing` · офлайн |
| 1.15.2 | IODO | 131 | R/W | `io_do` · офлайн |
| 1.15.3 | IOPWM | 132 | R/W | `io_pwm` · офлайн |
| 1.15.4 | IODI | 133 | R | `io_di` · офлайн |
| 1.15.5 | IOADC | 134 | R | `io_adc` · офлайн |
| 1.15.6 | EMotor | 135 | W | `external_motor` · офлайн |
| 1.15.7 | ColorSensor | 137 | R/W | `color_sensor` · офлайн |
| 1.15.8 | IRSwitch | 138 | R/W | `ir_switch` · офлайн |
| 1.16.1 | AngleSensorStaticError | 140 | R/W | `angle_sensor_error` · офлайн |
| 1.17.1 | WIFIConfigMode | 150 | R/W | `wifi_config_mode` · офлайн |
| 1.17.2 | WIFISSID | 151 | R/W | `wifi_ssid` · офлайн |
| 1.17.3 | WIFIPassword | 152 | R/W | `wifi_password` · офлайн |
| 1.17.4 | WIFIIPAddress | 153 | R/W | `wifi_ip_address` · офлайн |
| 1.17.5 | WIFINetmask | 154 | R/W | `wifi_netmask` · офлайн |
| 1.17.6 | WIFIGateway | 155 | R/W | `wifi_gateway` · офлайн |
| 1.17.7 | WIFIDNS | 156 | R/W | `wifi_dns` · офлайн |
| 1.17.8 | WIFIConnectStatus | 157 | R | `wifi_connect_status` · офлайн |
| 1.18.1 | LostStepParams | 170 | W | `lost_step_params` · офлайн |
| 1.18.2 | LostStepCmd | 171 | W | `lost_step` · офлайн |

## Очередь и автономное исполнение

| Раздел | Команда / возможность | ID | Операции | Кодек и проверка |
| --- | --- | --- | --- | --- |
| 1.19.1 | QueuedCmdStartExec | 240 | W | `queue_start` · USB, запуск очереди MOVL |
| 1.19.2 | QueuedCmdStopExec | 241 | W | `queue_stop` · офлайн |
| 1.19.3 | QueuedCmdForceStopExec | 242 | W | `queue_force_stop` · USB, ACK |
| 1.19.4 | QueuedCmdStartDownload | 243 | W | Явный отказ: `queue_start_download` |
| 1.19.5 | QueuedCmdStopDownload | 244 | W | `queue_stop_download` · офлайн |
| 1.19.6 | QueuedCmdClear | 245 | W | `queue_clear` · USB, ACK |
| 1.19.7 | QueuedCmdCurrentIndex | 246 | R | `queue_index` · USB, индексы целей MOVL |

## Расширения за пределами V1.1.5

В официальном DobotLink есть дополнительные команды для нескольких моделей и аксессуаров. Ниже перечислены кандидаты для будущих профилей прошивки. Для включения в пакет нужны формат операции и подтверждение применимости к классическому Magician. [Исходный код DobotLink на закреплённой версии](https://github.com/Dobot-Arm/DobotLink/blob/80c6ad78010721648ad671cc47320604d303f1d9/Plugins/MagicDevicePlugin/Protocol2/DMagicianProtocol.cpp)

| ID | Кандидат | Условие реализации |
| --- | --- | --- |
| 7–9 | Название продукта, OLED, progress bar | Проверить применимость модели |
| 15, 33 | CheckPoseLimit, GoHomeStatus | Подтвердить формат / firmware |
| 50–51 | ArmOrientation, MotorMode в карте decoder | Найти документированный контракт для классического Magician |
| 64–69 | Тип инструмента, servo angle, speed ratios, config / real-time status | Разделить Magician и Lite / Box |
| 75 | JOGCmdTime | Проверить ограничение времени и остановку |
| 93–95 | Head hold, CP common, CP2 | Подтвердить семантику траектории |
| 102–103 | CircleCmd, ARC common | Отдельный профиль и fixtures |
| 136, 139 | EMotorS, IIC | Нужны соответствующие аксессуары |
| 141–143 | Коэффициенты / база / LRHand calibration | Явная процедура калибровки и обратимость |
| 172 | CollisionCheck | Не заявлять аппаратную защиту по одному flag |
| 192–198 | Device status / app, upgrade и restart | Отдельные сервисные операции прошивки |
| 200 | Цветовой объект / координаты | Проверить аппаратный vision kit |
| 210–215 | Seeed distance / temperature / light / color / RGB LED, MCU temperature | Проверить набор датчиков |
| 216 | Мультиплексированные sensor / OLED / XBee / voice / BLE / hand-model subcommands | Разделить по subcommand и оборудованию |
| 247 | Queue remaining space | Удален из базового PDF; не предполагать наличие |

Для переноса предмета, рисования пером и других составных действий используются очереди, настроенный инструмент и точки рабочего места. Практические сценарии собраны в [руководстве по инструментам](TOOLS.md).

## Транспорты и отдельные режимы

| Возможность | Решение |
| --- | --- |
| USB serial | Реализован через pyserial, один владелец порта |
| TTL serial | Тот же базовый протокол, адаптер и уровни подключения требуют проверки |
| Wi-Fi UDP, порт 8899 | В протоколе описан; отдельная будущая реализация с подтверждением доставки и без автоповтора действий |
| BLE | UUID есть в протоколе; отдельный транспорт и реальные испытания |
| Несколько рук | Несколько независимых соединений с явным port; отказ одной не должен смешивать ответы другой |
| 3D-печать | Отдельная 3D Printer Firmware и G-code через последовательный порт принтера. Доступны работа с нарезанным файлом из Python и открытие в локальном хосте. Аппаратная печать образца остаётся в плане. [Интерфейс печати](PRINTING.md) |
| Magician Lite / Magic Box, E6, CR, MG400, M1 Pro | Другие профили / протоколы; текущий пакет их поддержку не заявляет |

## Как строка становится поддержанной

Для каждой операции фиксируют версию источника, поля и единицы, поведение при тайм-ауте, влияние на очередь и результаты испытаний. Байтовые тесты проверяют допустимые и ошибочные запросы и ответы. Отметку USB добавляют с описанием выполненного действия, а физический результат указывают по наблюдению или измерению.
