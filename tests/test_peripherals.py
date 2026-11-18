"""Offline peripheral usage and rejection paths, with no serial transport."""

from unittest.mock import patch

import pytest

from dobot_usb import Dobot
from dobot_usb.errors import ProtocolError, RequestTimeout, RobotAlarmError
from dobot_usb.peripherals import (
    IO,
    AngleSensorErrors,
    HomePosition,
    IOFunction,
    Motors,
    Network,
    PWMState,
    Rail,
    RailParameters,
    Sensors,
    Settings,
    Teaching,
    TeachingMode,
    VelocityRatios,
)
from dobot_usb.tools import ToolOffset

from .simulation import SimulatedTransport
from .test_tools import FakeBackend


@pytest.mark.parametrize(
    ("command", "group_method"),
    [
        ("io_multiplexing", "multiplexing"),
        ("io_do", "output"),
        ("io_pwm", "pwm_state"),
        ("io_di", "read"),
        ("io_adc", "analog"),
    ],
)
@pytest.mark.parametrize("advanced", [False, True])
def test_eio_get_rejects_reply_from_another_port(command, group_method, advanced):
    transport = SimulatedTransport()
    with Dobot(transport) as robot:
        command_id = robot.advanced.describe(command).command
        wrong_port_reply = transport.request(command_id, bytes([19]))
        with patch.object(transport, "request", return_value=wrong_port_reply) as request:
            with pytest.raises(ProtocolError, match="requested EIO 2 but received EIO 19") as error:
                if advanced:
                    robot.advanced.get(command, address=2)
                else:
                    getattr(robot.io, group_method)(2)
        assert error.value.command == command_id
        request.assert_called_once_with(command_id, bytes([2]), write=False, queued=False)
        assert transport.closed


@pytest.mark.parametrize("advanced", [False, True])
def test_active_alarm_blocks_rail_motion_before_queue_or_write(advanced):
    transport = SimulatedTransport()
    transport.responses[20] = bytes([1]) + bytes(15)
    with Dobot(transport) as robot:
        with pytest.raises(RobotAlarmError) as error:
            if advanced:
                robot.advanced.set("ptp_with_l", mode=2, l=10, x=200, y=0, z=60, r=0, wait=False)
            else:
                robot.rail.move_to(10, x=200, y=0, z=60, r=0, wait=False)
        assert error.value.alarms == (0,)
        assert error.value.stage == "not_sent"
        assert transport.requests == [20]


def test_io_does_not_configure_ports_implicitly():
    backend = FakeBackend(
        replies={
            "io_multiplexing": {"address": 2, "multiplex": 2},
            "io_do": {"address": 2, "level": True},
            "io_di": {"address": 3, "level": False},
            "io_adc": {"address": 4, "value": 2048},
            "io_pwm": {"address": 5, "frequency": 1000, "duty_cycle": 50},
        }
    )
    io = IO(backend)
    assert backend.calls == []
    io.multiplex(2, IOFunction.OUTPUT)
    assert io.multiplexing(2) == IOFunction.OUTPUT
    assert io.write(2, True, wait=False, timeout=3) is backend.receipt
    assert io.output(2) is True
    assert io.read(3) is False
    assert io.analog(4) == 2048
    io.pwm(5, frequency=1000, duty_cycle=50)
    assert io.pwm_state(5) == PWMState(1000, 50)
    assert [call[1] for call in backend.calls] == [
        "io_multiplexing",
        "io_multiplexing",
        "io_do",
        "io_do",
        "io_di",
        "io_adc",
        "io_pwm",
        "io_pwm",
    ]
    assert backend.calls[2][2:] == (
        {"address": 2, "level": True},
        {"wait": False, "timeout": 3.0, "queued": None},
    )
    assert [call[2] for call in backend.calls if call[0] == "read"] == [
        {"address": 2},
        {"address": 2},
        {"address": 3},
        {"address": 4},
        {"address": 5},
    ]


def test_motor_speed_is_signed_steps_not_conveyor_distance():
    backend = FakeBackend()
    motors = Motors(backend)
    motors.run(0, -100, wait=False)
    motors.stop(0)
    assert [call[2] for call in backend.calls] == [
        {"index": 0, "enabled": True, "speed": -100},
        {"index": 0, "enabled": False, "speed": 0},
    ]


def test_sensors_use_explicit_revision_and_ir_read_port():
    backend = FakeBackend(
        replies={"color_sensor": {"r": 1, "g": 2, "b": 3}, "ir_switch": {"state": True}}
    )
    sensors = Sensors(backend)
    sensors.configure_color(enabled=True, port=0, version=1)
    sensors.configure_infrared(enabled=True, port=1, version=0)
    assert sensors.color() == (1, 2, 3)
    assert sensors.infrared(port=1) is True
    assert backend.calls[0][2] == {"enabled": True, "port": 0, "version": 1}
    assert backend.calls[1][2] == {"enabled": True, "port": 1, "version": 0}
    assert backend.calls[-1] == ("read", "ir_switch", {"port": 1})


def test_rail_move_has_explicit_arm_target_and_does_not_read_stale_pose():
    backend = FakeBackend(
        replies={
            "pose_l": {"l": 42},
            "device_with_l": {"enabled": True},
            "ptp_l_params": {"velocity": 30, "acceleration": 60},
            "jog_l_params": {"velocity": 5, "acceleration": 10},
        }
    )
    rail = Rail(backend)
    assert rail.position() == 42
    assert rail.configuration() == {"enabled": True}
    rail.configure(enabled=True, version=1)
    rail.speed(mm_s=30, mm_s2=60)
    rail.speed(mm_s=5, mm_s2=10, jogging=True)
    assert rail.parameters() == RailParameters(30, 60)
    assert rail.parameters(jogging=True) == RailParameters(5, 10)
    backend.calls.clear()
    assert rail.move_to(100, x=200, y=0, z=80, r=0, wait=False) is backend.receipt
    rail.move_to(150, x=210, y=10, z=90, r=5, path="joint")
    assert len(backend.calls) == 2
    assert backend.calls[0][1:3] == (
        "ptp_with_l",
        {"mode": 2, "l": 100.0, "x": 200.0, "y": 0.0, "z": 80.0, "r": 0.0},
    )
    assert backend.calls[1][2]["mode"] == 1
    with pytest.raises(TypeError):
        rail.move_to(200)
    assert len(backend.calls) == 2


def test_teaching_reads_trigger_without_capturing_or_moving():
    backend = FakeBackend(
        replies={
            "hht_trigger_mode": {"mode": 1},
            "hht_output_enabled": {"enabled": False},
            "hht_trigger_output": {"triggered": True},
        }
    )
    teaching = Teaching(backend)
    assert teaching.mode() == TeachingMode.PERIODIC
    assert teaching.enabled() is False
    assert teaching.triggered() is True
    teaching.set_mode(TeachingMode.KEY_RELEASED)
    teaching.enable()
    teaching.enable(False)
    assert [call[2] for call in backend.calls[-3:]] == [
        {"mode": 0},
        {"enabled": True},
        {"enabled": False},
    ]


def test_settings_identification_tcp_independent_homing_and_calibration():
    backend = FakeBackend(
        replies={
            "device_sn": {"serial_number": "offline"},
            "device_name": {"name": "bench"},
            "device_version": {"version": "unknown"},
            "device_id": {"device_id": (1, 2, 3)},
            "home_params": {"x": 200, "y": 0, "z": 80, "r": 0},
            "ptp_common_params": {"velocity_ratio": 20, "acceleration_ratio": 30},
            "angle_sensor_error": {"rear_arm_angle": -0.5, "front_arm_angle": 0.25},
            "end_effector_params": {"x_bias": 1, "y_bias": 2, "z_bias": 3},
            "alarms": {"alarms": (1, 2)},
        }
    )
    settings = Settings(backend)
    assert settings.identification()["device_name"] == {"name": "bench"}
    assert all(call[0] == "read" for call in backend.calls)
    assert settings.tcp() == ToolOffset(1, 2, 3)
    settings.set_tcp(x=4, y=5, z=6)
    assert backend.calls[-1][1:3] == (
        "end_effector_params",
        {"x_bias": 4.0, "y_bias": 5.0, "z_bias": 6.0},
    )
    settings.set_name("bench 2")
    settings.set_serial_number("offline2")
    assert backend.calls[-2][2] == {"name": "bench 2"}
    assert settings.home_position() == HomePosition(200, 0, 80, 0)
    settings.set_home_position(x=210, y=1, z=90, r=2)
    assert settings.velocity_ratios() == VelocityRatios(20, 30)
    settings.set_velocity_ratios(velocity=25, acceleration=50)
    assert settings.angle_sensor_errors() == AngleSensorErrors(-0.5, 0.25)
    settings.set_angle_sensor_errors(rear_arm_angle_error=0.1, front_arm_angle_error=-0.2)
    assert backend.calls[-1][2] == {"rear_arm_angle": 0.1, "front_arm_angle": -0.2}
    settings.reset_pose(manual=True, rear_arm_angle=45, front_arm_angle=45)
    settings.set_lost_step_threshold(5)
    settings.check_lost_steps()
    assert settings.alarms() == {"alarms": (1, 2)}
    settings.clear_alarms()
    assert backend.calls[-1][1:3] == ("clear_alarms", {})
    assert not any(call[1] == "home" for call in backend.calls)


def test_network_configuration_keeps_password_out_of_repr_and_does_not_cache_it():
    backend = FakeBackend(
        replies={
            "wifi_config_mode": {"enabled": True},
            "wifi_ssid": {"ssid": "offline"},
            "wifi_password": {"password": "test-only-secret"},
            "wifi_ip_address": {"address": "192.168.1.2", "dhcp": False},
            "wifi_netmask": {"address": "255.255.255.0"},
            "wifi_gateway": {"address": "192.168.1.1"},
            "wifi_dns": {"address": "1.1.1.1"},
            "wifi_connect_status": {"connected": False},
        }
    )
    network = Network(backend)
    assert network.config_mode() is True
    assert network.ssid() == "offline"
    secret = network.password()
    assert secret.reveal() == "test-only-secret"
    assert "test-only-secret" not in repr(secret)
    assert "test-only-secret" not in str(secret)
    assert "test-only-secret" not in repr(network)
    assert "password" not in vars(network)
    network.set_config_mode(False)
    network.set_ssid("offline2")
    network.set_password("test-only-secret2")
    network.set_ip_address("192.168.1.2", dhcp=False)
    network.set_netmask("255.255.255.0")
    network.set_gateway("192.168.1.1")
    network.set_dns("1.1.1.1")
    assert backend.calls[-4][2] == {"address": "192.168.1.2", "dhcp": False}
    assert network.ip_address() == {"address": "192.168.1.2", "dhcp": False}
    assert network.netmask() == {"address": "255.255.255.0"}
    assert network.gateway() == {"address": "192.168.1.1"}
    assert network.dns() == {"address": "1.1.1.1"}
    assert network.connected() is False


@pytest.mark.parametrize(
    "action",
    [
        lambda b: IO(b).write(0, True),
        lambda b: IO(b).read(21),
        lambda b: IO(b).analog(True),
        lambda b: IO(b).multiplex(1, 5),
        lambda b: IO(b).write(2, 1),
        lambda b: IO(b).pwm(1, frequency=9, duty_cycle=50),
        lambda b: IO(b).pwm(1, frequency=1000, duty_cycle=101),
        lambda b: IO(b).pwm(1, frequency=float("inf"), duty_cycle=50),
        lambda b: Motors(b).run(2, 10),
        lambda b: Motors(b).run(0, 1.5),
        lambda b: Motors(b).run(0, 2**31),
        lambda b: Motors(b).stop(-1),
        lambda b: Sensors(b).configure_color(enabled=True, port=0, version=2),
        lambda b: Sensors(b).infrared(port=-1),
        lambda b: Rail(b).configure(enabled=1, version=0),
        lambda b: Rail(b).speed(mm_s=5, mm_s2=-1),
        lambda b: Rail(b).move_to(1, x=1, y=2, z=float("nan"), r=0),
        lambda b: Rail(b).move_to(1, x=1, y=2, z=3, r=0, path="jump"),
        lambda b: Teaching(b).set_mode(2),
        lambda b: Teaching(b).enable(1),
        lambda b: Settings(b).set_home_position(x=1, y=2, z=3, r=float("nan")),
        lambda b: Settings(b).set_velocity_ratios(velocity=101, acceleration=10),
        lambda b: Settings(b).set_name("bad\0name"),
        lambda b: Settings(b).set_lost_step_threshold(-1),
        lambda b: Network(b).set_ip_address("::1", dhcp=False),
        lambda b: Network(b).set_ip_address("1.2.3.4", dhcp=1),
        lambda b: Network(b).set_dns("256.1.2.3"),
        lambda b: Network(b).set_password("secret\0bad"),
        lambda b: Network(b).set_password("a" * 253),
        lambda b: Network(b).set_ssid("\ud800"),
    ],
)
def test_invalid_arguments_never_reach_backend(action):
    backend = FakeBackend()
    with pytest.raises(ValueError):
        action(backend)
    assert backend.calls == []


def test_read_errors_do_not_turn_into_plausible_values():
    backend = FakeBackend(
        replies={
            "io_di": {"level": "off"},
            "io_adc": {"value": 9999},
            "color_sensor": {"r": 1, "g": 2},
            "pose_l": {"l": float("nan")},
        }
    )
    for action in (
        lambda: IO(backend).read(1),
        lambda: IO(backend).analog(2),
        lambda: Sensors(backend).color(),
        lambda: Rail(backend).position(),
    ):
        with pytest.raises(ProtocolError):
            action()


def test_uncertain_motor_write_is_not_retried_or_stopped_implicitly():
    backend = FakeBackend(failure=RequestTimeout("uncertain motor command"))
    with pytest.raises(RequestTimeout):
        Motors(backend).run(0, 50)
    assert len(backend.calls) == 1
    assert backend.calls[0][2]["enabled"] is True


def test_bad_password_error_omits_secret_value():
    backend = FakeBackend()
    with pytest.raises(ValueError) as error:
        Network(backend).set_password("test-secret\0bad")
    assert "test-secret" not in str(error.value)
    assert backend.calls == []
