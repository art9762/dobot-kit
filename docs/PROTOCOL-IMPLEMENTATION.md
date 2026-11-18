# Classic Magician parameter codecs

Implemented offline on 2026-11-16 against **Communication Protocol V1.1.5**, 69
subsections 1.3.1–1.19.7. `dobot_usb.commands.COMMANDS` contains one `CommandSpec`
per subsection. **67 subsections have codecs; two explicitly refuse unresolved
SET layouts.** No serial port, controller, instrument, flash, firmware or real
queue was accessed. These are parameter codecs, not hardware acceptance.

Primary references: [manufacturer PDF](https://download.dobot.cc/product-manual/dobot-magician/pdf/en/Dobot-Communication-Protocol-V1.1.5.pdf),
local PDF SHA-256 `87f26f150ee3a830d2935126e842f356cc0d620277b1f7442516e1f3be5ddbeb`,
and [official DobotLink implementation](https://github.com/Dobot-Arm/DobotLink/blob/80c6ad78010721648ad671cc47320604d303f1d9/Plugins/MagicDevicePlugin/Protocol2/DMagicianProtocol.cpp)
with its [packed structure definitions](https://github.com/Dobot-Arm/DobotLink/blob/80c6ad78010721648ad671cc47320604d303f1d9/Plugins/MagicDevicePlugin/Protocol2/DMagicianProtocol.h),
pinned to `80c6ad78010721648ad671cc47320604d303f1d9`. Sources were inspected to
resolve layouts; vendor code and documents are not copied into this repository.
See [SOURCES.md](SOURCES.md) for licensing and source-depth boundaries.

## Contract and notation

`spec.encode_get(**params)` and `spec.encode_set(**params)` return parameter
bytes only. They never perform IO. `spec.decode(bytes)` returns GET fields as
`dict[str, object]`. Calling an unsupported direction raises `UnsupportedCommand`.
Bad caller arguments raise `TypeError`/`ValueError`; malformed GET replies raise
`ProtocolError`. Unknown keywords and missing fields are errors. SET responses
(empty immediate acknowledgement or queued uint64 index) belong to the executor.

All fields are little endian and packed with no C alignment. `B/H/I/Q/i/f` denote
uint8/uint16/uint32/uint64/int32/IEEE754 float32; `bool` is a canonical byte 0/1.
Python bool fields require actual `bool`, integer fields reject bool and floats,
and numeric float fields accept int/float but reject bool. Floats must remain
finite after float32 conversion; bounds are checked before and after rounding.
Responses obey the same domain checks. Fixed layouts require exact byte counts.
No request or response parameters can exceed 253 bytes (255 minus ID/control).

Coordinates are millimetres, joint/rotation angles degrees. Velocities and
accelerations follow the corresponding mm/s, degrees/s, mm/s², degrees/s² fields.
Two explicit wire-unit exceptions are `wait.timeout_ms` and `device_time.time_ms`,
both uint32 **milliseconds**. Real-time CP `period` is the raw firmware
interpolation-cycle field: the PDF does not identify its time unit; the codec
does not convert or claim seconds. `external_motor.speed` is signed steps/s.
`power`, ratios, duty cycle and PO ratios are percentages (0–100). Numeric codecs
do not establish reachability, real-world speed limits or collision clearance.

`Q` in the queue column means queueable, default queued. `Q/I` means queueable,
default immediate (JOG for interactive control). `I` means immediate only.
Defaults are a library policy, not a firmware assertion. Queued GETs are invalid;
queue metadata applies only to SET. Unsupported SETs retain documented queue
capability metadata but cannot be encoded.

Reusable schemas, with order significant:

- `position`: `x,y,z,r` (`4f`), any finite float32.
- `joint_params`: `velocity[4],acceleration[4]` (`8f`), nonnegative; accept Python
  list/tuple, return tuples; no guessed model-specific maximum speed.
- `coordinate_params`: `xyz_velocity,r_velocity,xyz_acceleration,r_acceleration`
  (`4f`), nonnegative.
- `ratios`: `velocity_ratio,acceleration_ratio` (`2f`), each 0–100.
- `l_params`: `velocity,acceleration` (`2f`), nonnegative.
- `ptp`: `mode` (`B`), then `position`; mode 0–9 (see enum below).
- `address`: EIO `address` (`B`), 1–20.
- `sensor_config`: `enabled` (bool), `port` (`B`), `version` (`B`, 0–1).
  The PDF leaves `Port` enumerators undefined; accept only representable uint8
  and do not claim that every value denotes an available connector.
- `text(key)`: Python string, no embedded NUL, UTF-8 + trailing NUL on SET;
  GET accepts an unterminated string or trailing NUL/zero padding, rejects any
  nonzero bytes after the first NUL. Device SN uses strict Latin-1 instead of
  UTF-8, matching the official writer without lossy replacement. GET of device
  SN/name decodes an empty field or an entirely erased field (all `0xff` before
  the terminator) as `None`. Other device identity text must be printable.
  Wi-Fi fields retain empty strings. Device-specific buffer
  limits below the 253-byte frame bound are not established.
- `IPv4`: `address` as dotted IPv4 string or `IPv4Address`; exactly four bytes on
  wire, decoded as dotted string. No hostnames, IPv6, prefixes or integer coercion.

## Complete subsection map

An empty GET request means **no parameters**. `—` means that direction is absent
in this subsection. Each supported direction has an independent fixed-byte test
fixture in `tests/test_commands.py`; the inventory test checks exhaustive coverage.

| Section | Key (ID) | SET parameters/layout | GET request → response | Queue |
| --- | --- | --- | --- | --- |
| 1.3.1 | `device_sn` (0) | `text(serial_number)` | empty → `serial_number` text | I |
| 1.3.2 | `device_name` (1) | `text(name)` | empty → `name` text | I |
| 1.3.3 | `device_version` (2) | — | empty → 3 or 14 bytes; see below | I |
| 1.3.4 | `device_with_l` (3) | `enabled,version` (`BB`, version 0–1) | empty → `enabled` (bool) | I |
| 1.3.5 | `device_time` (4) | — | empty → `time_ms` (`I`) | I |
| 1.3.6 | `device_id` (5) | — | empty → `device_id` tuple of three `I` | I |
| 1.4.1 | `pose` (10) | — | empty → `position,joints[4]` (`8f`) | I |
| 1.4.2 | `reset_pose` (11) | `manual,rear_arm_angle,front_arm_angle` (`Bff`) | — | I |
| 1.4.3 | `pose_l` (13) | — | empty → `l` (`f`) | I |
| 1.5.1 | `alarms` (20) | — | empty → `bitmap` (16 bytes), `alarms` (set bit indices tuple) | I |
| 1.5.2 | `clear_alarms` (20 write) | empty | — | I |
| 1.6.1 | `home_params` (30) | `position` | empty → same | Q |
| 1.6.2 | `home` (31) | `reserved` (`I`, must equal 0, defaults to 0) | — | Q |
| 1.6.3 | `auto_leveling` (32) | `enabled,accuracy` (`Bf`, accuracy ≥0) | empty → `result` (`f`) | Q |
| 1.7.1 | `hht_trigger_mode` (40) | `mode` (`B`, 0–1) | empty → same | I |
| 1.7.2 | `hht_output_enabled` (41) | `enabled` (bool) | empty → same | I |
| 1.7.3 | `hht_trigger_output` (42) | — | empty → `triggered` (bool) | I |
| 1.8.1 | `end_effector_params` (60) | `x_bias,y_bias,z_bias` (`3f`) | empty → same | Q |
| 1.8.2 | `laser` (61) | `enabled,active` (2 bool) | empty → same | Q |
| 1.8.3 | `suction` (62) | `enabled,active` (2 bool) | empty → same | Q |
| 1.8.4 | `gripper` (63) | `enabled,active` (2 bool) | empty → same | Q |
| 1.9.1 | `jog_joint_params` (70) | `joint_params` | empty → same | Q |
| 1.9.2 | `jog_coordinate_params` (71) | `joint_params` (XYZ/R ordered arrays) | empty → same | Q |
| 1.9.3 | `jog_common_params` (72) | `ratios` | empty → same | Q |
| 1.9.4 | `jog` (73) | `is_joint` (bool), `cmd` (`B`, 0–8) | — | Q/I |
| 1.9.5 | `jog_l_params` (74) | `l_params` | empty → same (8 bytes) | Q |
| 1.10.1 | `ptp_joint_params` (80) | `joint_params` | empty → same | Q |
| 1.10.2 | `ptp_coordinate_params` (81) | `coordinate_params` | empty → same | Q |
| 1.10.3 | `ptp_jump_params` (82) | `jump_height,z_limit` (`2f`, height ≥0) | empty → same | Q |
| 1.10.4 | `ptp_common_params` (83) | `ratios` | empty → same | Q |
| 1.10.5 | `ptp` (84) | `ptp` (`B4f`, 17 bytes) | — | Q |
| 1.10.6 | `ptp_l_params` (85) | `l_params` | empty → same (8 bytes) | Q |
| 1.10.7 | `ptp_with_l` (86) | `ptp,l` (`B5f`, 21 bytes) | — | Q |
| 1.10.8 | `ptp_jump2_params` (87) | `start_jump_height,end_jump_height,z_limit` (`3f`, heights ≥0) | empty → same (12 bytes) | Q |
| 1.10.9 | `ptp_po` (88) | `ptp`, then output count (`B`), then PO records (`BHB`, 4 bytes each) | — | Q |
| 1.10.10 | `ptp_po_with_l` (89) | **refused: unresolved format** | — | Q |
| 1.11.1 | `cp_params` (90) | `plan_acceleration,junction_velocity,acceleration-or-period,real_time_track` (`3fB`) | empty → same selected union key | Q |
| 1.11.2 | `cp` (91) | `mode,x,y,z,power` (`B4f`, mode 0 relative / 1 absolute) | — | Q |
| 1.11.3 | `cp_laser` (92) | same as `cp` | — | Q |
| 1.12.1 | `arc_params` (100) | `coordinate_params` | empty → same | Q |
| 1.12.2 | `arc` (101) | `cir_point,to_point` mappings, each `position` (`8f`, 32 bytes) | — | Q |
| 1.13.1 | `wait` (110) | `timeout_ms` (`I`) | — | Q |
| 1.14.1 | `trigger` (120) | `address,mode,condition,threshold` (`BBBH`, 5 bytes) | — | Q |
| 1.15.1 | `io_multiplexing` (130) | `address,multiplex` (`BB`, multiplex 0–4) | `address` → same | Q |
| 1.15.2 | `io_do` (131) | `address,level` (`BB`, bool level) | `address` → same | Q |
| 1.15.3 | `io_pwm` (132) | `address,frequency,duty_cycle` (`Bff`, 10–1,000,000 Hz; 0–100%) | `address` → same | Q |
| 1.15.4 | `io_di` (133) | — | `address` → `address,level` (`BB`, bool) | I |
| 1.15.5 | `io_adc` (134) | — | `address` → `address,value` (`BH`, value 0–4095) | I |
| 1.15.6 | `external_motor` (135) | `index,enabled,speed` (`BBi`, index 0/1) | — | Q |
| 1.15.7 | `color_sensor` (137) | `sensor_config` (3 bytes) | empty → `r,g,b` (`3B`) | Q |
| 1.15.8 | `ir_switch` (138) | `sensor_config` (3 bytes) | `port` (`B`) → `state` (bool) | Q |
| 1.16.1 | `angle_sensor_error` (140) | `rear_arm_angle,front_arm_angle` (`2f`) | empty → same | I |
| 1.17.1 | `wifi_config_mode` (150) | `enabled` (bool) | empty → same | I |
| 1.17.2 | `wifi_ssid` (151) | `text(ssid)` | empty → same | I |
| 1.17.3 | `wifi_password` (152) | `text(password)` | empty → same; never embedded in codec errors | I |
| 1.17.4 | `wifi_ip_address` (153) | `dhcp` (bool), `IPv4` (5 bytes) | empty → same | I |
| 1.17.5 | `wifi_netmask` (154) | `IPv4` | empty → same | I |
| 1.17.6 | `wifi_gateway` (155) | `IPv4` | empty → same | I |
| 1.17.7 | `wifi_dns` (156) | `IPv4` | empty → same | I |
| 1.17.8 | `wifi_connect_status` (157) | — | empty → `connected` (bool) | I |
| 1.18.1 | `lost_step_params` (170) | `value` (`f`, ≥0) | — | I |
| 1.18.2 | `lost_step` (171) | empty | — | Q |
| 1.19.1 | `queue_start` (240) | empty | — | I |
| 1.19.2 | `queue_stop` (241) | empty | — | I |
| 1.19.3 | `queue_force_stop` (242) | empty | — | I |
| 1.19.4 | `queue_start_download` (243) | **refused: unresolved field widths** | — | I |
| 1.19.5 | `queue_stop_download` (244) | empty | — | I |
| 1.19.6 | `queue_clear` (245) | empty | — | I |
| 1.19.7 | `queue_index` (246) | — | empty → `queue_index` (`Q`) | I |

`ptp_po.outputs` is a list/tuple of mappings: `ratio` (`B`, 0–100), `address`
(`H`, EIO 1–20), `level` (bool). The count is derived, never supplied independently.
At most 58 records fit the frame (17 + 1 + 58×4 = 250 parameter bytes).
An empty list emits an explicit zero count. No rounding of integer ratios.

For `cp_params`, pass `real_time_track=False` with **only** `acceleration`, or
`real_time_track=True` with **only** `period`; supplying both union alternatives
is an error. Plan acceleration, junction velocity and union value must be ≥0.
For `trigger`, mode 0 is digital (condition 0 equal / 1 unequal; threshold 0/1),
mode 1 ADC (condition 0 less / 1 less-or-equal / 2 greater-or-equal / 3 greater;
threshold 0–4095). Address is EIO 1–20.

`PTPMode` preserves the ten declared numeric enumerators:
`JUMP_XYZ=0`, `MOVJ_XYZ=1`, `MOVL_XYZ=2`, `JUMP_ANGLE=3`, `MOVJ_ANGLE=4`,
`MOVL_ANGLE=5`, `MOVJ_INC=6`, `MOVL_INC=7`, `MOVJ_XYZ_INC=8`, `JUMP_MOVL_XYZ=9`.
The misleading 0–8 range comment is superseded by the explicit enum in Program 17.
The codec preserves values and makes no firmware-specific trajectory claims.
JOG `cmd=0` is stop; values 1–8 select signed axis directions in the chosen
joint/coordinate mode. The V1.1.5 JOG command does not add undocumented rail codes.

Version responses decode to `major,minor,revision` for both recognized lengths.
The 14-byte form also returns opaque `extension` bytes 3–12 and
`hardware_version` byte 13, matching the manufacturer classic-Magician reader.
The extension bytes' meanings are not inferred. Lite/Box formats are excluded.
Alarm responses return the exact 16-byte bitmap plus ascending set-bit indices,
without guessing alarm descriptions or accepting truncated bitmaps.

## Resolved errata and remaining ambiguity

These are narrowly justified format choices, not a claim that the manufacturer
implementation is correct everywhere:

| PDF discrepancy | Implemented basis |
| --- | --- |
| DeviceID duplicates DeviceTime ID 4 | Official `encodeGetDeviceID` uses ID 5; response remains three little-endian uint32 from PDF, not the vendor decoder's three uint8 reads |
| Alarm response incorrectly labels ID 11; clear uses 21 | Official map/read/clear use ID 20 with rw selecting direction; bitmap size remains the PDF's 16 bytes |
| AutoLeveling tables reuse ID 30 and show a float in GET request | Official encoders use ID 32, empty GET, SET bool + float; GET returns a float |
| HHT mode tables claim 8 bytes and response rw=1 | Official SET and GET mode field is a uint8; two enum values from Program 6 |
| JOGL/PTPL tables claim 32-byte responses | Two float structures and official `VelocityStruct` require 8 bytes |
| PTPJump2 tables claim 8 bytes | Program 20 and packed manufacturer structure have three floats, 12 bytes |
| PTPPO table omits a count byte | Official `encodeSetPTPPOCmd` explicitly appends uint8 count after the 17-byte PTP; use 18 + 4×n parameters |
| CP structures duplicate cpMode text | Both total length (17) and official packed structure establish exactly one byte, then four floats |
| TRIG table claims 4 bytes | Program 29 and official packed `TRIGCmdStruct` have 3×uint8 + uint16 = 5 |
| All five EIO GET tables omit address | Official GET encoders send one address byte, which this codec requires |
| EMotor table says 2 bytes and float speed | Official packed structure/encoder use uint8 index + uint8 enable + signed int32 speed = 6 bytes, matching shared implementation contract |
| IR SET table says 2 bytes; GET lacks port | Program 37 and official SET specify enabled/port/version (3 bytes); official GET sends port byte |

**Refused `ptp_po_with_l` (1.10.10):** PDF presents a 21-byte rail PTP followed
by PO records without an explicit count; official code constructs the 21-byte
structure but copies `sizeof(PTPCmdStruct)` (17 bytes), then adds a count. That
would omit `l`. The two descriptions cannot establish a trustworthy rail packet.
`set_supported=False`; `encode_set` raises `UnsupportedCommand` with this reason.
Needed: a corrected manufacturer contract or verified firmware-specific packet
fixture. Do not substitute `ptp_po`, drop rail movement, or emit a guessed packet.

**Refused `queue_start_download` (1.19.4):** PDF Table 205 says uint32 totalLoop
+ uint32 linePerLoop (8 bytes); pinned official encoder emits uint8 + uint8
(2 bytes). No firmware profile or fixture distinguishes a source bug from a
protocol revision. `set_supported=False`; `encode_set` raises `UnsupportedCommand`.
Needed: a corrected contract or verified firmware-specific fixture.

Other explicit limits: version extension interpretation, CP period units, sensor
port mapping, per-firmware text-buffer limits and hardware support are unknown.
SN uses the official writer's Latin-1 convention (the PDF only says char[n]);
unrepresentable characters are rejected instead of silently replaced.
SSID/password use the official writer's UTF-8
convention; byte validation is not Wi-Fi security-mode validation. IPv4/netmask
codecs validate the wire address, not a particular network configuration.

## Offline verification

The new test module includes independently specified SET byte vectors and GET
response vectors for every supported direction, three full packet/checksum
vectors, all ten PTP enumerators, exact lengths and errata, queue flags, unsigned
64-bit indices, signed motor extremes, float32 overflow/nonfinite values, strict
types, malformed binary flags, integer bounds, list sizes, nested PO/ARC data,
CP union selection, digital/ADC trigger conditions, address-bearing reads,
UTF-8 byte budgets, embedded NULs, password error redaction and IPv4 validation.
Tests operate entirely on bytes. They establish codec behavior only. No hosted
CI, physical queue completion, firmware acceptance or sensor behavior is claimed.

Validation in this isolated clone: **612 tests passed**, including **512 command
tests** and the 100 existing tests. `ruff check .`, `ruff format --check .`, scoped
`mypy src/dobotkit/commands.py --follow-imports=silent`, `uv build` and
`git diff --check` passed. The initial isolated clone predated the integrated execution module. [Current full-package tests and type checking](VALIDATION.md)
