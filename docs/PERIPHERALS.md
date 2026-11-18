# Tool and peripheral groups

Implemented offline in `tools.py` and `peripherals.py` for the classic Magician.
Constructors perform no IO. Groups use only `interfaces.Backend`; importing these
modules does not require an execution implementation at runtime. The integrated Dobot facade constructs and exposes these groups.

No serial port, real accessory, firmware, hosted CI or physical motion was tested.
Controller bits are not proof of grip, laser emission, rail accuracy or accessory presence.

All setters return the backend's unchanged `CommandHandle`. `wait=True` and
`timeout=30.0` are defaults; `wait=False` is passed through. An accepted handle is
never upgraded to completed by a group. Backend exceptions propagate, with no retry,
automatic stop, clear, reset or output shutdown. Numeric and public option validation
occurs before calling the backend; codecs remain responsible for wire representation.

## Ordinary tool use

- `Suction.on()/off()/state()`: on is `(enabled=True, active=True)`, off is `(False, False)`.
- `Gripper.open()/close()/release()/state()`: open is `(True, False)`, close is
  `(True, True)`, release is `(False, False)`. This mapping follows the agreed contract;
  its physical meaning still requires a test of the fitted gripper. Release does not
  promise opening or retaining the item.
- `Tool.select("pen" | "suction" | "gripper" | "laser")` selects an immutable local
  `ToolProfile(name)`. `current` is initially `None`. Selection performs no IO, changes
  no TCP and activates no output. No measured geometry or device detection is implied.
- `Tool.offset(x=..., y=..., z=...)` writes end-effector XYZ bias in mm;
  `get_offset()` returns `ToolOffset(x, y, z)` from a fresh query. There are no invented
  per-tool offsets. The same controller TCP is available as `Settings.tcp()/set_tcp(...)`.
- Tool states are the existing `models.ToolState` with separately checked boolean bits.

### Laser session integration is required

Construct `Laser(backend, is_session_active=callback)`. `arm()` grants local permission
without IO only when the callback reports an active session. `on()` requires that
permission. `off()` is always callable without permission and without a successful
session check; a disconnected backend can still reject the command. Both on and off
use `queued=False` so a queued activation cannot outlive local permission.

`disarm()`, `invalidate()` and `close()` only revoke permission. They do not claim or
request physical shutdown. Use `off()` explicitly for an output shutdown request.
Inactive session observations, callback exceptions, and on/off/state backend exceptions
revoke local permission. No state query creates permission.

**The connection owner must call `laser.invalidate()` on every loss, reconnect or
session replacement, and `laser.close()` on close.** A boolean callback cannot detect
a disconnect/reconnect that happened entirely between calls. Do not share one laser
object across sessions without that hook. The owner's lifecycle synchronization must
serialize session changes and command submission; these groups do not add a second
transport owner or independent polling thread.

## Peripheral APIs

| Group | Public operations and units |
| --- | --- |
| `IO` | `multiplex(address, IOFunction)`, `multiplexing(address)`, `write(address, level)`, `read(address)` for DI, `output(address)` for DO, `pwm(address, frequency=Hz, duty_cycle=percent)`, `pwm_state(address)`, `analog(address)` |
| `Motors` | `run(index, speed_steps_s)`, `stop(index)`; index 0/1, signed int32 pulses/steps per second; stop disables control with speed zero |
| `Sensors` | `configure_color(enabled=..., port=..., version=...)`, `color()` returning RGB byte tuple, `configure_infrared(...)`, `infrared(port=...)` returning bool |
| `Rail` | `position()` in mm, `configuration()`, `configure(enabled=..., version=...)`, `parameters(jogging=False)`, `speed(mm_s=..., mm_s2=..., jogging=False)`, `move_to(position, x=..., y=..., z=..., r=..., path="linear" | "joint")` |
| `Teaching` | `mode()/set_mode(TeachingMode.KEY_RELEASED | PERIODIC)`, `enabled()/enable(bool)`, `triggered()`; no hidden point capture, replay or motion |
| `Settings` | `identification()`, `set_name(...)`, `set_serial_number(...)`, `tcp()/set_tcp(...)`, `home_position()/set_home_position(...)`, `velocity_ratios()/set_velocity_ratios(velocity=..., acceleration=...)`, `alarms()/clear_alarms()`, `angle_sensor_errors()/set_angle_sensor_errors(...)`, `reset_pose(...)`, `set_lost_step_threshold(value)`, `check_lost_steps()` |
| `Network` | `config_mode()/set_config_mode(...)`, `ssid()/set_ssid(...)`, `password()/set_password(...)`, `ip_address()/set_ip_address(address, dhcp=...)`, netmask/gateway/DNS read and set methods, `connected()` |

EIO addresses are 1..20, PWM frequency 10..1,000,000 Hz and duty cycle 0..100 percent;
which functions a physical connector supports requires the hardware profile. IO calls
do not silently select multiplexing. Analog returns raw 0..4095 ADC units, not volts.
`IOFunction` names the documented values 0..4. Rail and sensor hardware versions are
the documented 0/1 enumerators; sensor port mapping must be selected for the actual
accessory. Only the byte domain of sensor port is enforced here, not an invented pin map.

Rail movement requires the complete arm target. It performs no stale pose read to infer
coordinates to hold while changing L. It uses absolute Cartesian PTP modes 2/1; jump,
relative and joint-angle variants remain available through the root motion/advanced
layer. No rail accessory is enabled automatically. The separate integrated Conveyor adapter converts mm/s only after explicit measured steps_per_mm calibration.

Homing position configuration does not execute homing. Velocity ratios are PTP percent
values 0..100 and are separate from absolute speeds. Calibration values are degrees;
`reset_pose(manual=..., rear_arm_angle=..., front_arm_angle=...)` is explicit. Lost-step
threshold is an unconverted protocol value; physical interpretation/threshold selection
must be checked for the controller. Identification is a sequence of four reads, not an
atomic snapshot. Dictionary results retain the decoded protocol schema for identification,
alarms, rail configuration and network addresses; other simple results use frozen types.

Wi-Fi addresses are validated dotted IPv4 strings. Setters do not imply that the module
is fitted, connected, or that UDP transport is implemented. Password is never cached on
the group or included in its repr/logs. Explicit `password()` returns `WifiPassword`
with a redacted repr/str; `reveal()` deliberately returns plaintext. This does not encrypt
memory. No aggregate call reads the password. The backend **must redact** password
parameters, decoded replies, exceptions and packet dumps from its own diagnostics.

## Exact backend schemas and integration notes

`read(command_name, **params) -> dict[str, object]` and
`write(command_name, *, wait, timeout, queued, **params) -> CommandHandle` are the only
dependencies. `queued=None` delegates to command defaults except laser's explicit false.

**Use a positional-only command name or a differently named parameter in Backend.write.**
`write(name, **params)` cannot represent the `device_name` payload field `name` without
Python raising a duplicate-argument TypeError. This branch leaves `interfaces.py` and
the concrete backend to their owner. Its test double uses `write(command_name, /, ...)`.

| Command(s) | SET fields / GET parameters | GET result fields used here |
| --- | --- | --- |
| `laser`, `suction`, `gripper` | `enabled: bool, active: bool` | same |
| `end_effector_params` | `x_bias, y_bias, z_bias: float` | same |
| `io_multiplexing` | `address: int, multiplex: int`; GET address | `multiplex: int` |
| `io_do`, `io_di` | DO: address, level: bool; GET address | `level: bool` |
| `io_pwm` | address, frequency, duty_cycle; GET address | frequency, duty_cycle |
| `io_adc` | GET address | `value: int` |
| `external_motor` | `index: int, enabled: bool, speed: int` | no GET |
| `color_sensor` | `enabled: bool, port: int, version: int`; empty GET | r, g, b integers |
| `ir_switch` | same SET as color; GET `port: int` | `state: bool` |
| `device_with_l` | `enabled: bool, version: int`; empty GET | decoded dictionary |
| `pose_l` | empty GET | `l: float` |
| `jog_l_params`, `ptp_l_params` | `velocity: float, acceleration: float`; empty GET | same |
| `ptp_with_l` | `mode: int, x, y, z, r, l: float` | no GET |
| `hht_trigger_mode` | `mode: int`; empty GET | mode |
| `hht_output_enabled` | `enabled: bool`; empty GET | enabled |
| `hht_trigger_output` | empty GET | `triggered: bool` |
| `device_sn`, `device_name` | `serial_number: str` / `name: str` | decoded dictionary |
| `device_version`, `device_id`, `alarms` | empty GET | decoded dictionary |
| `home_params` | `x, y, z, r: float`; empty GET | same |
| `ptp_common_params` | `velocity_ratio, acceleration_ratio: float`; empty GET | same |
| `angle_sensor_error` | `rear_arm_angle, front_arm_angle: float`; empty GET | same |
| `reset_pose` | `manual: bool, rear_arm_angle, front_arm_angle: float` | no GET |
| `lost_step_params` | `value: float` | no GET |
| `lost_step`, `clear_alarms` | empty SET | no GET |
| `wifi_config_mode` | `enabled: bool`; empty GET | enabled |
| `wifi_ssid`, `wifi_password` | `ssid: str` / `password: str`; empty GET | same |
| `wifi_ip_address` | `address: str, dhcp: bool`; empty GET | decoded dictionary |
| `wifi_netmask`, `wifi_gateway`, `wifi_dns` | `address: str`; empty GET | decoded dictionary |
| `wifi_connect_status` | empty GET | `connected: bool` |

Public calibration setter names include `_error`; backend fields deliberately match
the codec's shorter `rear_arm_angle`/`front_arm_angle`. GETs must return actual bools
for logical fields. Missing or malformed typed results raise `ProtocolError`; they
are not coerced to false, zero or fabricated success.

## Source and evidence boundaries

The format references are the local V1.1.5 protocol text identified in `SOURCES.md`
(sections 1.3.1–1.3.4, 1.4.2–1.4.3, 1.6.1, 1.7, 1.8, 1.9.5, 1.10.6–1.10.7,
1.15–1.18) and the pinned official
[DobotLink encoder](https://github.com/Dobot-Arm/DobotLink/blob/80c6ad78010721648ad671cc47320604d303f1d9/Plugins/MagicDevicePlugin/Protocol2/DMagicianProtocol.cpp).
Code is an independent implementation; no vendor files or binaries are copied into Git.

The official encoder resolves the PDF's conflicting external motor speed type to int32,
and adds the required port byte to IR GET. Payload sizing, enum widths and wire errata
are the command codec owner's responsibility. These source checks establish command
schemas, not the behavior of the user's firmware or accessories.

The initial peripheral branch passed 165 isolated tests. Its 38-setter compatibility check encoded requests without opening a port. The merged library is checked together, including strict typing and transport integration. [Current release checks](VALIDATION.md)
