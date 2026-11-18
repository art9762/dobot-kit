"""Named peripheral commands for the classic Magician protocol.

No constructor performs IO. Optional accessories must be selected and verified by
the caller; these groups do not probe for them or change port multiplexing implicitly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from ipaddress import IPv4Address
from typing import TYPE_CHECKING, Literal

from .errors import ProtocolError
from .tools import (
    Tool,
    ToolOffset,
    _boolean,
    _Group,
    _integer,
    _number,
    _result_bool,
    _result_number,
)

if TYPE_CHECKING:
    from .execution import CommandHandle
    from .models import Point, Pose


def _text(name: str, value: object) -> str:
    # Max frame payload minus command/control and trailing NUL.
    if not isinstance(value, str) or "\0" in value:
        raise ValueError(f"{name} must be text without NUL")
    try:
        size = len(value.encode("utf-8"))
    except UnicodeEncodeError:
        raise ValueError(f"{name} must be valid UTF-8 text") from None
    if size > 252:
        raise ValueError(f"{name} exceeds protocol frame capacity")
    return value


def _result_integer(data: dict[str, object], name: str, low: int, high: int) -> int:
    try:
        return _integer(name, data.get(name), low, high)
    except ValueError as exc:
        raise ProtocolError(f"Expected integer field {name}") from exc


class IOFunction(IntEnum):
    UNCONFIGURED = 0
    PWM = 1
    OUTPUT = 2
    INPUT = 3
    ANALOG = 4


@dataclass(frozen=True)
class PWMState:
    frequency: float
    duty_cycle: float


class IO(_Group):
    """EIO addresses are 1..20; supported functions depend on the actual port."""

    def multiplex(
        self, address: int, function: IOFunction, *, wait: bool = True, timeout: float = 30.0
    ) -> CommandHandle:
        port = _integer("address", address, 1, 20)
        mode = _integer("function", function, 0, 4)
        return self._write(
            "io_multiplexing", address=port, multiplex=mode, wait=wait, timeout=timeout
        )

    def multiplexing(self, address: int) -> IOFunction:
        data = self._backend.read("io_multiplexing", address=_integer("address", address, 1, 20))
        return IOFunction(_result_integer(data, "multiplex", 0, 4))

    mode = multiplexing
    set_mode = multiplex

    def write(
        self, address: int, level: bool, *, wait: bool = True, timeout: float = 30.0
    ) -> CommandHandle:
        port = _integer("address", address, 1, 20)
        value = _boolean("level", level)
        return self._write("io_do", address=port, level=value, wait=wait, timeout=timeout)

    def read(self, address: int) -> bool:
        data = self._backend.read("io_di", address=_integer("address", address, 1, 20))
        return _result_bool(data, "level")

    def output(self, address: int) -> bool:
        data = self._backend.read("io_do", address=_integer("address", address, 1, 20))
        return _result_bool(data, "level")

    def pwm(
        self,
        address: int,
        frequency: float,
        duty_cycle: float,
        *,
        wait: bool = True,
        timeout: float = 30.0,
    ) -> CommandHandle:
        """Frequency in Hz, duty cycle in percent. Does not select port function."""
        port = _integer("address", address, 1, 20)
        hz = _number("frequency", frequency, minimum=0)
        duty = _number("duty_cycle", duty_cycle, minimum=0)
        if not 10 <= hz <= 1_000_000 or duty > 100:
            raise ValueError("frequency must be 10..1000000 Hz; duty_cycle must be 0..100 percent")
        return self._write(
            "io_pwm", address=port, frequency=hz, duty_cycle=duty, wait=wait, timeout=timeout
        )

    def pwm_state(self, address: int) -> PWMState:
        data = self._backend.read("io_pwm", address=_integer("address", address, 1, 20))
        return PWMState(_result_number(data, "frequency"), _result_number(data, "duty_cycle"))

    def analog(self, address: int) -> int:
        """Return raw 12-bit ADC units; no voltage calibration is inferred."""
        data = self._backend.read("io_adc", address=_integer("address", address, 1, 20))
        return _result_integer(data, "value", 0, 4095)


class Motors(_Group):
    """External steppers 0/1. Signed speed is pulses (steps) per second, never mm/s."""

    def run(
        self, index: int, speed_steps_s: int, *, wait: bool = True, timeout: float = 30.0
    ) -> CommandHandle:
        motor = _integer("index", index, 0, 1)
        speed = _integer("speed_steps_s", speed_steps_s, -(2**31), 2**31 - 1)
        return self._write(
            "external_motor", index=motor, enabled=True, speed=speed, wait=wait, timeout=timeout
        )

    def stop(self, index: int, *, wait: bool = True, timeout: float = 30.0) -> CommandHandle:
        return self._write(
            "external_motor",
            index=_integer("index", index, 0, 1),
            enabled=False,
            speed=0,
            wait=wait,
            timeout=timeout,
        )


class Sensors(_Group):
    """Classic color and IR accessories; version and port must be explicit."""

    def _configure(
        self, command: str, *, enabled: bool, port: int, version: int, wait: bool, timeout: float
    ) -> CommandHandle:
        enable = _boolean("enabled", enabled)
        connector = _integer("port", port, 0, 255)
        revision = _integer("version", version, 0, 1)
        return self._write(
            command, enabled=enable, port=connector, version=revision, wait=wait, timeout=timeout
        )

    def configure_color(
        self, *, enabled: bool, port: int, version: int, wait: bool = True, timeout: float = 30.0
    ) -> CommandHandle:
        return self._configure(
            "color_sensor", enabled=enabled, port=port, version=version, wait=wait, timeout=timeout
        )

    def color(self) -> tuple[int, int, int]:
        data = self._backend.read("color_sensor")
        return (
            _result_integer(data, "r", 0, 255),
            _result_integer(data, "g", 0, 255),
            _result_integer(data, "b", 0, 255),
        )

    def configure_infrared(
        self, *, enabled: bool, port: int, version: int, wait: bool = True, timeout: float = 30.0
    ) -> CommandHandle:
        return self._configure(
            "ir_switch", enabled=enabled, port=port, version=version, wait=wait, timeout=timeout
        )

    def infrared(self, port: int) -> bool:
        data = self._backend.read("ir_switch", port=_integer("port", port, 0, 255))
        return _result_bool(data, "state")

    ir = infrared
    set_ir = configure_infrared
    set_color = configure_color


@dataclass(frozen=True)
class RailParameters:
    velocity: float
    acceleration: float


class Rail(_Group):
    def position(self) -> float:
        """Controller rail coordinate in mm; not independent position metrology."""
        return _result_number(self._backend.read("pose_l"), "l")

    def configuration(self) -> dict[str, object]:
        return self._backend.read("device_with_l")

    def configure(
        self, *, enabled: bool, version: int, wait: bool = True, timeout: float = 30.0
    ) -> CommandHandle:
        enable = _boolean("enabled", enabled)
        revision = _integer("version", version, 0, 1)
        return self._write(
            "device_with_l", enabled=enable, version=revision, wait=wait, timeout=timeout
        )

    def parameters(self, *, jogging: bool = False) -> RailParameters:
        _boolean("jogging", jogging)
        data = self._backend.read("jog_l_params" if jogging else "ptp_l_params")
        return RailParameters(
            _result_number(data, "velocity"), _result_number(data, "acceleration")
        )

    def speed(
        self,
        mm_s: float,
        mm_s2: float,
        *,
        jogging: bool = False,
        wait: bool = True,
        timeout: float = 30.0,
    ) -> CommandHandle:
        _boolean("jogging", jogging)
        velocity = _number("mm_s", mm_s, minimum=0)
        acceleration = _number("mm_s2", mm_s2, minimum=0)
        return self._write(
            "jog_l_params" if jogging else "ptp_l_params",
            velocity=velocity,
            acceleration=acceleration,
            wait=wait,
            timeout=timeout,
        )

    def move(
        self,
        position: float,
        target: Point | Pose,
        *,
        path: Literal["linear", "joint"] = "linear",
        wait: bool = True,
        timeout: float = 30.0,
    ) -> CommandHandle:
        """Move to an absolute rail coordinate and an explicit XYZ/R target."""
        return self.move_to(
            position,
            x=target.x,
            y=target.y,
            z=target.z,
            r=target.r,
            path=path,
            wait=wait,
            timeout=timeout,
        )

    def move_to(
        self,
        position: float,
        *,
        x: float,
        y: float,
        z: float,
        r: float,
        path: Literal["linear", "joint"] = "linear",
        wait: bool = True,
        timeout: float = 30.0,
    ) -> CommandHandle:
        """Coordinated absolute target. Arm coordinates are explicit, never read/held implicitly."""
        if path not in ("linear", "joint"):
            raise ValueError("path must be linear or joint")
        values: dict[str, object] = {
            key: _number(key, val)
            for key, val in (("l", position), ("x", x), ("y", y), ("z", z), ("r", r))
        }
        return self._write(
            "ptp_with_l",
            mode=2 if path == "linear" else 1,
            wait=wait,
            timeout=timeout,
            queued=None,
            **values,
        )


class TeachingMode(IntEnum):
    KEY_RELEASED = 0
    PERIODIC = 1


class Teaching(_Group):
    def mode(self) -> TeachingMode:
        return TeachingMode(_result_integer(self._backend.read("hht_trigger_mode"), "mode", 0, 1))

    def set_mode(
        self, mode: TeachingMode, *, wait: bool = True, timeout: float = 30.0
    ) -> CommandHandle:
        value = _integer("mode", mode, 0, 1)
        return self._write("hht_trigger_mode", mode=value, wait=wait, timeout=timeout)

    def enabled(self) -> bool:
        return _result_bool(self._backend.read("hht_output_enabled"), "enabled")

    def enable(
        self, enabled: bool = True, *, wait: bool = True, timeout: float = 30.0
    ) -> CommandHandle:
        return self._write(
            "hht_output_enabled", enabled=_boolean("enabled", enabled), wait=wait, timeout=timeout
        )

    def triggered(self) -> bool:
        return _result_bool(self._backend.read("hht_trigger_output"), "triggered")


@dataclass(frozen=True)
class HomePosition:
    x: float
    y: float
    z: float
    r: float


@dataclass(frozen=True)
class VelocityRatios:
    velocity_ratio: float
    acceleration_ratio: float


@dataclass(frozen=True)
class AngleSensorErrors:
    rear_arm_angle_error: float
    front_arm_angle_error: float


class Settings(_Group):
    def tcp(self) -> ToolOffset:
        """Read end-effector XYZ bias in mm."""
        return Tool(self._backend).get_offset()

    def set_tcp(
        self, *, x: float, y: float, z: float, wait: bool = True, timeout: float = 30.0
    ) -> CommandHandle:
        return Tool(self._backend).offset(x=x, y=y, z=z, wait=wait, timeout=timeout)

    def identification(self) -> dict[str, dict[str, object]]:
        """Sequential reads, not an atomic snapshot; no settings are written."""
        return {
            name: self._backend.read(name)
            for name in ("device_sn", "device_name", "device_version", "device_id")
        }

    ids = identification

    def set_name(self, name: str, *, wait: bool = True, timeout: float = 30.0) -> CommandHandle:
        return self._write("device_name", name=_text("name", name), wait=wait, timeout=timeout)

    def set_serial_number(
        self, serial_number: str, *, wait: bool = True, timeout: float = 30.0
    ) -> CommandHandle:
        return self._write(
            "device_sn",
            serial_number=_text("serial_number", serial_number),
            wait=wait,
            timeout=timeout,
        )

    set_serial = set_serial_number

    def home_position(self) -> HomePosition:
        data = self._backend.read("home_params")
        return HomePosition(*(_result_number(data, key) for key in ("x", "y", "z", "r")))

    def set_home_position(
        self, x: float, y: float, z: float, r: float, *, wait: bool = True, timeout: float = 30.0
    ) -> CommandHandle:
        values: dict[str, object] = {
            key: _number(key, val) for key, val in (("x", x), ("y", y), ("z", z), ("r", r))
        }
        return self._write("home_params", wait=wait, timeout=timeout, queued=None, **values)

    home_pos = home_position
    set_home = set_home_position

    def velocity_ratios(self) -> VelocityRatios:
        data = self._backend.read("ptp_common_params")
        return VelocityRatios(
            _result_number(data, "velocity_ratio"), _result_number(data, "acceleration_ratio")
        )

    def set_velocity_ratios(
        self, velocity: float, acceleration: float, *, wait: bool = True, timeout: float = 30.0
    ) -> CommandHandle:
        speed = _number("velocity", velocity, minimum=0)
        ramp = _number("acceleration", acceleration, minimum=0)
        if speed > 100 or ramp > 100:
            raise ValueError("velocity and acceleration ratios must be 0..100 percent")
        return self._write(
            "ptp_common_params",
            velocity_ratio=speed,
            acceleration_ratio=ramp,
            wait=wait,
            timeout=timeout,
        )

    ratios = velocity_ratios
    set_ratios = set_velocity_ratios

    def alarms(self) -> dict[str, object]:
        return self._backend.read("alarms")

    def clear_alarms(self, *, wait: bool = True, timeout: float = 30.0) -> CommandHandle:
        """Explicit reset request; this does not remedy the source of an alarm."""
        return self._write("clear_alarms", wait=wait, timeout=timeout)

    def angle_sensor_errors(self) -> AngleSensorErrors:
        data = self._backend.read("angle_sensor_error")
        return AngleSensorErrors(
            _result_number(data, "rear_arm_angle"),
            _result_number(data, "front_arm_angle"),
        )

    def set_angle_sensor_errors(
        self,
        *,
        rear_arm_angle_error: float,
        front_arm_angle_error: float,
        wait: bool = True,
        timeout: float = 30.0,
    ) -> CommandHandle:
        rear = _number("rear_arm_angle_error", rear_arm_angle_error)
        front = _number("front_arm_angle_error", front_arm_angle_error)
        return self._write(
            "angle_sensor_error",
            rear_arm_angle=rear,
            front_arm_angle=front,
            wait=wait,
            timeout=timeout,
        )

    angle_errors = angle_sensor_errors
    set_angle_errors = set_angle_sensor_errors

    def reset_pose(
        self,
        *,
        manual: bool,
        rear_arm_angle: float,
        front_arm_angle: float,
        wait: bool = True,
        timeout: float = 30.0,
    ) -> CommandHandle:
        manual_value = _boolean("manual", manual)
        rear = _number("rear_arm_angle", rear_arm_angle)
        front = _number("front_arm_angle", front_arm_angle)
        return self._write(
            "reset_pose",
            manual=manual_value,
            rear_arm_angle=rear,
            front_arm_angle=front,
            wait=wait,
            timeout=timeout,
        )

    def set_lost_step_threshold(
        self, value: float, *, wait: bool = True, timeout: float = 30.0
    ) -> CommandHandle:
        return self._write(
            "lost_step_params", value=_number("value", value, minimum=0), wait=wait, timeout=timeout
        )

    def check_lost_steps(self, *, wait: bool = True, timeout: float = 30.0) -> CommandHandle:
        return self._write("lost_step", wait=wait, timeout=timeout)


@dataclass(frozen=True)
class WifiPassword:
    """Explicit secret read result; repr/str hide the value. reveal() is intentional."""

    _value: str = field(repr=False)

    def reveal(self) -> str:
        return self._value


class Network(_Group):
    """Configure the optional Wi-Fi module over the current transport.

    This is not a UDP transport. No aggregate status call reads the password.
    The backend must redact wifi_password parameters/replies from its own logs.
    """

    def config_mode(self) -> bool:
        return _result_bool(self._backend.read("wifi_config_mode"), "enabled")

    def set_config_mode(
        self, enabled: bool, *, wait: bool = True, timeout: float = 30.0
    ) -> CommandHandle:
        return self._write(
            "wifi_config_mode", enabled=_boolean("enabled", enabled), wait=wait, timeout=timeout
        )

    def ssid(self) -> str:
        data = self._backend.read("wifi_ssid")
        value = data.get("ssid")
        if not isinstance(value, str):
            raise ProtocolError("Expected ssid text")
        return value

    def set_ssid(self, ssid: str, *, wait: bool = True, timeout: float = 30.0) -> CommandHandle:
        return self._write("wifi_ssid", ssid=_text("ssid", ssid), wait=wait, timeout=timeout)

    def password(self) -> WifiPassword:
        value = self._backend.read("wifi_password").get("password")
        if not isinstance(value, str):
            raise ProtocolError("Expected password text")
        return WifiPassword(value)

    def set_password(
        self, password: str, *, wait: bool = True, timeout: float = 30.0
    ) -> CommandHandle:
        return self._write(
            "wifi_password", password=_text("password", password), wait=wait, timeout=timeout
        )

    def ip_address(self) -> dict[str, object]:
        return self._backend.read("wifi_ip_address")

    def set_ip_address(
        self, address: str, *, dhcp: bool, wait: bool = True, timeout: float = 30.0
    ) -> CommandHandle:
        validated = self._address(address)
        automatic = _boolean("dhcp", dhcp)
        return self._write(
            "wifi_ip_address", address=validated, dhcp=automatic, wait=wait, timeout=timeout
        )

    ip = ip_address
    set_ip = set_ip_address

    @staticmethod
    def _address(address: str) -> str:
        if not isinstance(address, str):
            raise ValueError("address must be a dotted IPv4 string")
        try:
            validated = IPv4Address(address)
        except ValueError:
            raise ValueError("address must be a dotted IPv4 string") from None
        return str(validated)

    def netmask(self) -> dict[str, object]:
        return self._backend.read("wifi_netmask")

    def set_netmask(
        self, address: str, *, wait: bool = True, timeout: float = 30.0
    ) -> CommandHandle:
        return self._write(
            "wifi_netmask", address=self._address(address), wait=wait, timeout=timeout
        )

    def gateway(self) -> dict[str, object]:
        return self._backend.read("wifi_gateway")

    def set_gateway(
        self, address: str, *, wait: bool = True, timeout: float = 30.0
    ) -> CommandHandle:
        return self._write(
            "wifi_gateway", address=self._address(address), wait=wait, timeout=timeout
        )

    def dns(self) -> dict[str, object]:
        return self._backend.read("wifi_dns")

    def set_dns(self, address: str, *, wait: bool = True, timeout: float = 30.0) -> CommandHandle:
        return self._write("wifi_dns", address=self._address(address), wait=wait, timeout=timeout)

    def connected(self) -> bool:
        return _result_bool(self._backend.read("wifi_connect_status"), "connected")
