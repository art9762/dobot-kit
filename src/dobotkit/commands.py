"""Pure parameter codecs for classic Magician V1.1.5 (no transport or IO).

Layouts are explicitly listed below, with reconciled errata documented in
``docs/PROTOCOL-IMPLEMENTATION.md``. Values use mm/degrees unless noted there.
The WAIT codec deliberately exposes wire milliseconds as ``timeout_ms``.
"""

from __future__ import annotations

import math
import struct
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import IntEnum
from ipaddress import IPv4Address

from .errors import ProtocolError, UnsupportedCommand


class PTPMode(IntEnum):
    """The ten enumerators in V1.1.5 Program 17; no mode substitution."""

    JUMP_XYZ = 0
    MOVJ_XYZ = 1
    MOVL_XYZ = 2
    JUMP_ANGLE = 3
    MOVJ_ANGLE = 4
    MOVL_ANGLE = 5
    MOVJ_INC = 6
    MOVL_INC = 7
    MOVJ_XYZ_INC = 8
    JUMP_MOVL_XYZ = 9


@dataclass(frozen=True)
class _Field:
    name: str
    kind: str
    minimum: float | int | None = None
    maximum: float | int | None = None
    count: int = 1
    default: object = None

    def validate(self, value: object) -> object:
        if self.count != 1:
            if not isinstance(value, (tuple, list)) or len(value) != self.count:
                raise TypeError(f"{self.name} must contain exactly {self.count} numbers")
            scalar = _Field(self.name, self.kind, self.minimum, self.maximum)
            return tuple(scalar.validate(v) for v in value)
        if self.kind == "?":
            if type(value) is not bool:
                raise TypeError(f"{self.name} must be bool")
            return value
        if self.kind == "f":
            if isinstance(value, bool) or not isinstance(value, (float, int)):
                raise TypeError(f"{self.name} must be a real number")
            try:
                number = float(value)
                rounded = struct.unpack("<f", struct.pack("<f", number))[0]
            except (OverflowError, struct.error) as exc:
                raise ValueError(f"{self.name} does not fit float32") from exc
            if not math.isfinite(number) or not math.isfinite(rounded):
                raise ValueError(f"{self.name} must be finite float32")
            self._range(number)
            self._range(rounded)
            return rounded
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError(f"{self.name} must be an integer")
        limits = {
            "B": (0, 255),
            "H": (0, 65535),
            "I": (0, 2**32 - 1),
            "Q": (0, 2**64 - 1),
            "i": (-(2**31), 2**31 - 1),
        }
        lo, hi = limits[self.kind]
        if not lo <= value <= hi:
            raise ValueError(f"{self.name} does not fit {self.kind}")
        self._range(value)
        return int(value)

    def _range(self, number: float | int) -> None:
        if self.minimum is not None and number < self.minimum:
            raise ValueError(f"{self.name} must be >= {self.minimum}")
        if self.maximum is not None and number > self.maximum:
            raise ValueError(f"{self.name} must be <= {self.maximum}")


Encoder = Callable[[Mapping[str, object]], bytes]
Decoder = Callable[[bytes], dict[str, object]]


def _keys(params: Mapping[str, object], fields: Sequence[_Field]) -> None:
    unknown = params.keys() - {field.name for field in fields}
    if unknown:
        raise TypeError(f"unexpected parameters: {', '.join(sorted(unknown))}")
    missing = [f.name for f in fields if f.name not in params and f.default is None]
    if missing:
        raise TypeError(f"missing parameters: {', '.join(missing)}")


@dataclass(frozen=True)
class _Layout:
    fields: tuple[_Field, ...] = ()

    @property
    def format(self) -> str:
        # Bool is encoded as uint8 so noncanonical response values can be rejected.
        return "<" + "".join(("B" if f.kind == "?" else f.kind) * f.count for f in self.fields)

    def encode(self, params: Mapping[str, object]) -> bytes:
        _keys(params, self.fields)
        values: list[object] = []
        for field in self.fields:
            value = field.validate(params.get(field.name, field.default))
            if field.count != 1:
                assert isinstance(value, tuple)
                values.extend(value)
            else:
                values.append(value)
        return struct.pack(self.format, *values)

    def decode(self, data: bytes) -> dict[str, object]:
        size = struct.calcsize(self.format)
        if len(data) != size:
            raise ValueError(f"expected {size} response bytes, received {len(data)}")
        values = struct.unpack(self.format, data)
        result: dict[str, object] = {}
        offset = 0
        for field in self.fields:
            value: object
            if field.count != 1:
                value = values[offset : offset + field.count]
            elif field.kind == "?":
                if values[offset] not in (0, 1):
                    raise ValueError(f"{field.name} must be a binary flag")
                value = bool(values[offset])
            else:
                value = values[offset]
            result[field.name] = field.validate(value)
            offset += field.count
        return result


@dataclass(frozen=True)
class CommandSpec:
    """Validated request/GET reply contract, independent of queue execution.

    Unsupported operations raise UnsupportedCommand; bad caller values raise
    TypeError/ValueError; malformed GET replies raise ProtocolError. SET replies
    are handled by the executor, not by decode().
    """

    name: str
    command: int
    section: str
    queueable: bool
    default_queued: bool
    _get: Encoder | None = None
    _set: Encoder | None = None
    _decode: Decoder | None = None
    unsupported_reason: str | None = None

    @property
    def get_supported(self) -> bool:
        return self._get is not None and self._decode is not None

    @property
    def set_supported(self) -> bool:
        return self._set is not None

    def encode_get(self, **params: object) -> bytes:
        if not self.get_supported or self._get is None:
            raise UnsupportedCommand(self.unsupported_reason or f"{self.name} has no GET")
        return self._bounded(self._get(params))

    def encode_set(self, **params: object) -> bytes:
        if self._set is None:
            raise UnsupportedCommand(self.unsupported_reason or f"{self.name} has no SET")
        return self._bounded(self._set(params))

    def decode(self, data: bytes) -> dict[str, object]:
        if not self.get_supported or self._decode is None:
            raise UnsupportedCommand(self.unsupported_reason or f"{self.name} has no GET")
        if not isinstance(data, bytes):
            raise TypeError("response must be bytes")
        try:
            self._bounded(data)
            return self._decode(data)
        except (TypeError, ValueError, OverflowError, struct.error) as exc:
            # Do not include data or decoded values (notably Wi-Fi passwords).
            raise ProtocolError(f"invalid {self.name} response: {exc}") from None

    @staticmethod
    def _bounded(data: bytes) -> bytes:
        if len(data) > 253:
            raise ValueError("parameters exceed the one-byte payload length")
        return data


def _f(
    name: str,
    minimum: float | None = None,
    maximum: float | None = None,
    *,
    count: int = 1,
    default: object = None,
) -> _Field:
    return _Field(name, "f", minimum, maximum, count, default)


def _u(
    name: str, maximum: int = 255, minimum: int = 0, *, kind: str = "B", default: object = None
) -> _Field:
    return _Field(name, kind, minimum, maximum, default=default)


def _b(name: str) -> _Field:
    return _Field(name, "?")


def _text_codec(name: str, encoding: str = "utf-8") -> tuple[Encoder, Decoder]:
    def encode(params: Mapping[str, object]) -> bytes:
        _keys(params, (_Field(name, "text"),))
        value = params[name]
        if not isinstance(value, str):
            raise TypeError(f"{name} must be str")
        if "\0" in value:
            raise ValueError(f"{name} must not contain NUL")
        try:
            return value.encode(encoding, errors="strict") + b"\0"
        except UnicodeError:
            raise ValueError(f"{name} cannot be encoded as {encoding}") from None

    def decode(data: bytes) -> dict[str, object]:
        # PDF says char[n]; observed/vendor readers also accept a trailing NUL.
        # Permit zero padding after the terminator, but never hide nonzero bytes.
        head, separator, tail = data.partition(b"\0")
        if separator and any(tail):
            raise ValueError("nonzero bytes after string terminator")
        identity = name in ("serial_number", "name")
        if identity and (not head or all(byte == 0xFF for byte in head)):
            return {name: None}
        try:
            value = head.decode(encoding, errors="strict")
        except UnicodeError:
            raise ValueError(f"invalid {encoding} string") from None
        if identity and not value.isprintable():
            raise ValueError("device identity contains control characters")
        return {name: value}

    return encode, decode


def _version(data: bytes) -> dict[str, object]:
    if len(data) not in (3, 14):
        raise ValueError("version requires 3 or 14 bytes for classic Magician")
    result: dict[str, object] = dict(zip(("major", "minor", "revision"), data[:3], strict=True))
    if len(data) == 14:
        result.update(extension=data[3:13], hardware_version=data[13])
    return result


def _alarms(data: bytes) -> dict[str, object]:
    # V1.1.5 Table 26 declares a 16-byte bitmap, including all zero bytes.
    if len(data) != 16:
        raise ValueError("alarm bitmap requires 16 bytes")
    return {"bitmap": data, "alarms": tuple(i for i in range(128) if data[i // 8] & (1 << (i % 8)))}


_CP_FIELDS = (
    _f("plan_acceleration", 0),
    _f("junction_velocity", 0),
    _f("acceleration", 0),
    _f("period", 0),
    _b("real_time_track"),
)


def _cp_params(params: Mapping[str, object]) -> bytes:
    unknown = params.keys() - {f.name for f in _CP_FIELDS}
    if unknown:
        raise TypeError(f"unexpected CP parameters: {', '.join(sorted(unknown))}")
    real_time = _b("real_time_track").validate(params.get("real_time_track"))
    union_name = "period" if real_time else "acceleration"
    fields = (_CP_FIELDS[0], _CP_FIELDS[1], _f(union_name, 0), _b("real_time_track"))
    return _Layout(fields).encode(params)


def _decode_cp_params(data: bytes) -> dict[str, object]:
    layout = _Layout((_CP_FIELDS[0], _CP_FIELDS[1], _f("union", 0), _b("real_time_track")))
    values = layout.decode(data)
    name = "period" if values["real_time_track"] else "acceleration"
    values[name] = values.pop("union")
    return values


def _ip_codec(dhcp: bool = False) -> tuple[Encoder, Decoder]:
    fields = ((_b("dhcp"),) if dhcp else ()) + (_Field("address", "ip"),)

    def encode(params: Mapping[str, object]) -> bytes:
        _keys(params, fields)
        address = params["address"]
        if not isinstance(address, (str, IPv4Address)):
            raise TypeError("address must be an IPv4 string or IPv4Address")
        packed = IPv4Address(address).packed
        if dhcp:
            return bytes((int(bool(_b("dhcp").validate(params["dhcp"]))),)) + packed
        return packed

    def decode(data: bytes) -> dict[str, object]:
        if len(data) != (5 if dhcp else 4):
            raise ValueError("invalid IPv4 structure length")
        result: dict[str, object] = {"address": str(IPv4Address(data[-4:]))}
        if dhcp:
            result.update(_Layout((_b("dhcp"),)).decode(data[:1]))
        return result

    return encode, decode


def _trigger(params: Mapping[str, object]) -> bytes:
    mode = _u("mode", 1).validate(params.get("mode"))
    fields = (
        _u("address", 20, 1),
        _u("mode", 1),
        _u("condition", 1 if mode == 0 else 3),
        _u("threshold", 1 if mode == 0 else 4095, kind="H"),
    )
    return _Layout(fields).encode(params)


_POSITION = tuple(_f(n) for n in ("x", "y", "z", "r"))
_PTP = (_u("mode", 9),) + _POSITION
_ADDRESS = (_u("address", 20, 1),)


def _arc(params: Mapping[str, object]) -> bytes:
    _keys(params, (_Field("cir_point", "point"), _Field("to_point", "point")))
    encoded = b""
    for key in ("cir_point", "to_point"):
        point = params[key]
        if not isinstance(point, Mapping) or not all(isinstance(k, str) for k in point):
            raise TypeError(f"{key} must be a mapping with x/y/z/r")
        encoded += _Layout(_POSITION).encode(point)
    return encoded


def _ptp_po(params: Mapping[str, object]) -> bytes:
    _keys(params, _PTP + (_Field("outputs", "outputs"),))
    encoded = _Layout(_PTP).encode({k: params[k] for k in ("mode", "x", "y", "z", "r")})
    outputs = params["outputs"]
    if not isinstance(outputs, (list, tuple)):
        raise TypeError("outputs must be a sequence of ratio/address/level mappings")
    # 17 byte PTP + 1 byte count + n * 4; 253 is maximum parameter length.
    if len(outputs) > 58:
        raise ValueError("at most 58 synchronized outputs fit a packet")
    encoded += bytes((len(outputs),))
    for output in outputs:
        if not isinstance(output, Mapping) or not all(isinstance(k, str) for k in output):
            raise TypeError("each output must be a mapping")
        encoded += _Layout((_u("ratio", 100), _u("address", 20, 1, kind="H"), _b("level"))).encode(
            output
        )
    return encoded


COMMANDS: dict[str, CommandSpec] = {}


def _add(
    name: str,
    command: int,
    section: str,
    fields: tuple[_Field, ...] = (),
    *,
    get: bool = True,
    set: bool = True,
    queueable: bool = False,
    default_queued: bool | None = None,
    get_fields: tuple[_Field, ...] = (),
    reply_fields: tuple[_Field, ...] | None = None,
    encoder: Encoder | None = None,
    decoder: Decoder | None = None,
    unsupported_reason: str | None = None,
) -> None:
    COMMANDS[name] = CommandSpec(
        name,
        command,
        section,
        queueable,
        queueable if default_queued is None else default_queued,
        _Layout(get_fields).encode if get else None,
        (encoder or _Layout(fields).encode) if set else None,
        (decoder or _Layout(reply_fields if reply_fields is not None else fields).decode)
        if get
        else None,
        unsupported_reason,
    )


# Explicit schemas, in PDF subsection order. No generated format inference.
for _name, _id, _section, _key, _encoding in (
    ("device_sn", 0, "1.3.1", "serial_number", "latin-1"),
    ("device_name", 1, "1.3.2", "name", "utf-8"),
    ("wifi_ssid", 151, "1.17.2", "ssid", "utf-8"),
    ("wifi_password", 152, "1.17.3", "password", "utf-8"),
):
    _enc, _dec = _text_codec(_key, _encoding)
    _add(_name, _id, _section, encoder=_enc, decoder=_dec)

_add("device_version", 2, "1.3.3", set=False, decoder=_version)
_add("device_with_l", 3, "1.3.4", (_b("enabled"), _u("version", 1)), reply_fields=(_b("enabled"),))
_add("device_time", 4, "1.3.5", (_u("time_ms", 2**32 - 1, kind="I"),), set=False)
_add("device_id", 5, "1.3.6", (_Field("device_id", "I", count=3),), set=False)
_add("pose", 10, "1.4.1", _POSITION + (_f("joints", count=4),), set=False)
_add(
    "reset_pose",
    11,
    "1.4.2",
    (_b("manual"), _f("rear_arm_angle"), _f("front_arm_angle")),
    get=False,
)
_add("pose_l", 13, "1.4.3", (_f("l"),), set=False)
_add("alarms", 20, "1.5.1", set=False, decoder=_alarms)
_add("clear_alarms", 20, "1.5.2", get=False)
_add("home_params", 30, "1.6.1", _POSITION, queueable=True)
_add("home", 31, "1.6.2", (_u("reserved", 0, kind="I", default=0),), get=False, queueable=True)
_add(
    "auto_leveling",
    32,
    "1.6.3",
    (_b("enabled"), _f("accuracy", 0)),
    queueable=True,
    reply_fields=(_f("result"),),
)
_add("hht_trigger_mode", 40, "1.7.1", (_u("mode", 1),))
_add("hht_output_enabled", 41, "1.7.2", (_b("enabled"),))
_add("hht_trigger_output", 42, "1.7.3", (_b("triggered"),), set=False)
_add(
    "end_effector_params",
    60,
    "1.8.1",
    tuple(_f(n) for n in ("x_bias", "y_bias", "z_bias")),
    queueable=True,
)
for _name, _id, _section in (
    ("laser", 61, "1.8.2"),
    ("suction", 62, "1.8.3"),
    ("gripper", 63, "1.8.4"),
):
    _add(_name, _id, _section, (_b("enabled"), _b("active")), queueable=True)

_JOINT_PARAMS = (_f("velocity", 0, count=4), _f("acceleration", 0, count=4))
_RATIOS = (_f("velocity_ratio", 0, 100), _f("acceleration_ratio", 0, 100))
_L_PARAMS = (_f("velocity", 0), _f("acceleration", 0))
_COORD_PARAMS = tuple(
    _f(n, 0) for n in ("xyz_velocity", "r_velocity", "xyz_acceleration", "r_acceleration")
)
_add("jog_joint_params", 70, "1.9.1", _JOINT_PARAMS, queueable=True)
_add("jog_coordinate_params", 71, "1.9.2", _JOINT_PARAMS, queueable=True)
_add("jog_common_params", 72, "1.9.3", _RATIOS, queueable=True)
_add(
    "jog",
    73,
    "1.9.4",
    (_b("is_joint"), _u("cmd", 8)),
    get=False,
    queueable=True,
    default_queued=False,
)
_add("jog_l_params", 74, "1.9.5", _L_PARAMS, queueable=True)
_add("ptp_joint_params", 80, "1.10.1", _JOINT_PARAMS, queueable=True)
_add("ptp_coordinate_params", 81, "1.10.2", _COORD_PARAMS, queueable=True)
_add("ptp_jump_params", 82, "1.10.3", (_f("jump_height", 0), _f("z_limit")), queueable=True)
_add("ptp_common_params", 83, "1.10.4", _RATIOS, queueable=True)
_add("ptp", 84, "1.10.5", _PTP, get=False, queueable=True)
_add("ptp_l_params", 85, "1.10.6", _L_PARAMS, queueable=True)
_add("ptp_with_l", 86, "1.10.7", _PTP + (_f("l"),), get=False, queueable=True)
_add(
    "ptp_jump2_params",
    87,
    "1.10.8",
    (_f("start_jump_height", 0), _f("end_jump_height", 0), _f("z_limit")),
    queueable=True,
)
_add("ptp_po", 88, "1.10.9", get=False, queueable=True, encoder=_ptp_po)
_add(
    "ptp_po_with_l",
    89,
    "1.10.10",
    get=False,
    set=False,
    queueable=True,
    unsupported_reason="PTPPOWithL layout unresolved: PDF omits count; pinned DobotLink "
    "serializes only 17 of the 21 rail PTP bytes. No verified fixture resolves this.",
)
_add("cp_params", 90, "1.11.1", queueable=True, encoder=_cp_params, decoder=_decode_cp_params)
for _name, _id, _section in (("cp", 91, "1.11.2"), ("cp_laser", 92, "1.11.3")):
    _add(
        _name,
        _id,
        _section,
        (_u("mode", 1), _f("x"), _f("y"), _f("z"), _f("power", 0, 100)),
        get=False,
        queueable=True,
    )
_add("arc_params", 100, "1.12.1", _COORD_PARAMS, queueable=True)
_add("arc", 101, "1.12.2", get=False, queueable=True, encoder=_arc)
_add("wait", 110, "1.13.1", (_u("timeout_ms", 2**32 - 1, kind="I"),), get=False, queueable=True)
_add("trigger", 120, "1.14.1", get=False, queueable=True, encoder=_trigger)
for _name, _id, _section, _fields, _writable in (
    ("io_multiplexing", 130, "1.15.1", (_u("multiplex", 4),), True),
    ("io_do", 131, "1.15.2", (_b("level"),), True),
    ("io_pwm", 132, "1.15.3", (_f("frequency", 10, 1_000_000), _f("duty_cycle", 0, 100)), True),
    ("io_di", 133, "1.15.4", (_b("level"),), False),
    ("io_adc", 134, "1.15.5", (_u("value", 4095, kind="H"),), False),
):
    _add(
        _name,
        _id,
        _section,
        _ADDRESS + _fields,
        set=_writable,
        queueable=_writable,
        get_fields=_ADDRESS,
    )
_add(
    "external_motor",
    135,
    "1.15.6",
    (_u("index", 1), _b("enabled"), _Field("speed", "i")),
    get=False,
    queueable=True,
)
_SENSOR_CONFIG = (_b("enabled"), _u("port"), _u("version", 1))
_add(
    "color_sensor",
    137,
    "1.15.7",
    _SENSOR_CONFIG,
    queueable=True,
    reply_fields=tuple(_u(n) for n in ("r", "g", "b")),
)
_add(
    "ir_switch",
    138,
    "1.15.8",
    _SENSOR_CONFIG,
    queueable=True,
    get_fields=(_u("port"),),
    reply_fields=(_b("state"),),
)
_add("angle_sensor_error", 140, "1.16.1", (_f("rear_arm_angle"), _f("front_arm_angle")))
_add("wifi_config_mode", 150, "1.17.1", (_b("enabled"),))
for _name, _id, _section, _dhcp in (
    ("wifi_ip_address", 153, "1.17.4", True),
    ("wifi_netmask", 154, "1.17.5", False),
    ("wifi_gateway", 155, "1.17.6", False),
    ("wifi_dns", 156, "1.17.7", False),
):
    _enc, _dec = _ip_codec(_dhcp)
    _add(_name, _id, _section, encoder=_enc, decoder=_dec)
_add("wifi_connect_status", 157, "1.17.8", (_b("connected"),), set=False)
_add("lost_step_params", 170, "1.18.1", (_f("value", 0),), get=False)
_add("lost_step", 171, "1.18.2", get=False, queueable=True)
for _name, _id, _section in (
    ("queue_start", 240, "1.19.1"),
    ("queue_stop", 241, "1.19.2"),
    ("queue_force_stop", 242, "1.19.3"),
    ("queue_stop_download", 244, "1.19.5"),
    ("queue_clear", 245, "1.19.6"),
):
    _add(_name, _id, _section, get=False)
_add(
    "queue_start_download",
    243,
    "1.19.4",
    get=False,
    set=False,
    unsupported_reason="StartDownload layout unresolved: PDF Table 205 specifies two uint32 "
    "(8 bytes), pinned DobotLink emits two uint8 (2 bytes). Requires a firmware fixture.",
)
_add("queue_index", 246, "1.19.7", (_u("queue_index", 2**64 - 1, kind="Q"),), set=False)
