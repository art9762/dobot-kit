"""Offline vectors transcribed from protocol field order, not codec round trips."""

import math
import struct
from ipaddress import IPv4Address

import pytest

from dobotkit.commands import COMMANDS, PTPMode
from dobotkit.errors import ProtocolError, UnsupportedCommand
from dobotkit.protocol import Frame, encode

# Independently stated wire examples for every supported SET. Float words are
# IEEE754 little endian: 1=0000803f, 2=00000040, 3=00004040, 4=00008040.
POSITION = {"x": 1, "y": 2, "z": 3, "r": 4}
POSITION_HEX = "0000803f000000400000404000008040"
JOINT = {"velocity": [1, 2, 3, 4], "acceleration": [4, 3, 2, 1]}
JOINT_HEX = POSITION_HEX + "0000804000004040000000400000803f"
RATIOS = {"velocity_ratio": 1, "acceleration_ratio": 2}
COORDINATE = {"xyz_velocity": 1, "r_velocity": 2, "xyz_acceleration": 3, "r_acceleration": 4}
SET_VECTORS = {
    "device_sn": ({"serial_number": "SN"}, "534e00"),
    "device_name": ({"name": "Р"}, "d0a000"),
    "device_with_l": ({"enabled": True, "version": 1}, "0101"),
    "reset_pose": (
        {"manual": True, "rear_arm_angle": 1, "front_arm_angle": 2},
        "010000803f00000040",
    ),
    "clear_alarms": ({}, ""),
    "home_params": (POSITION, POSITION_HEX),
    "home": ({}, "00000000"),
    "auto_leveling": ({"enabled": True, "accuracy": 1}, "010000803f"),
    "hht_trigger_mode": ({"mode": 1}, "01"),
    "hht_output_enabled": ({"enabled": True}, "01"),
    "end_effector_params": ({"x_bias": 1, "y_bias": 2, "z_bias": 3}, "0000803f0000004000004040"),
    "laser": ({"enabled": True, "active": False}, "0100"),
    "suction": ({"enabled": False, "active": True}, "0001"),
    "gripper": ({"enabled": True, "active": True}, "0101"),
    "jog_joint_params": (JOINT, JOINT_HEX),
    "jog_coordinate_params": (JOINT, JOINT_HEX),
    "jog_common_params": (RATIOS, "0000803f00000040"),
    "jog": ({"is_joint": True, "cmd": 8}, "0108"),
    "jog_l_params": ({"velocity": 1, "acceleration": 2}, "0000803f00000040"),
    "ptp_joint_params": (JOINT, JOINT_HEX),
    "ptp_coordinate_params": (COORDINATE, POSITION_HEX),
    "ptp_jump_params": ({"jump_height": 1, "z_limit": 2}, "0000803f00000040"),
    "ptp_common_params": (RATIOS, "0000803f00000040"),
    "ptp": ({"mode": 9, **POSITION}, "09" + POSITION_HEX),
    "ptp_l_params": ({"velocity": 1, "acceleration": 2}, "0000803f00000040"),
    "ptp_with_l": ({"mode": 2, **POSITION, "l": 1}, "02" + POSITION_HEX + "0000803f"),
    "ptp_jump2_params": (
        {"start_jump_height": 1, "end_jump_height": 2, "z_limit": 3},
        "0000803f0000004000004040",
    ),
    # Count byte follows PTP (pinned manufacturer encoder); each PO is B,H,B.
    "ptp_po": (
        {
            "mode": 1,
            **POSITION,
            "outputs": [
                {"ratio": 50, "address": 7, "level": True},
                {"ratio": 100, "address": 20, "level": False},
            ],
        },
        "01" + POSITION_HEX + "023207000164140000",
    ),
    "cp_params": (
        {
            "plan_acceleration": 1,
            "junction_velocity": 2,
            "acceleration": 3,
            "real_time_track": False,
        },
        "0000803f000000400000404000",
    ),
    "cp": ({"mode": 0, "x": 1, "y": 2, "z": 3, "power": 4}, "00" + POSITION_HEX),
    "cp_laser": ({"mode": 1, "x": 1, "y": 2, "z": 3, "power": 4}, "01" + POSITION_HEX),
    "arc_params": (COORDINATE, POSITION_HEX),
    "arc": (
        {"cir_point": POSITION, "to_point": {"x": 4, "y": 3, "z": 2, "r": 1}},
        POSITION_HEX + "0000804000004040000000400000803f",
    ),
    "wait": ({"timeout_ms": 1000}, "e8030000"),
    "trigger": ({"address": 20, "mode": 1, "condition": 3, "threshold": 4095}, "140103ff0f"),
    "io_multiplexing": ({"address": 20, "multiplex": 4}, "1404"),
    "io_do": ({"address": 1, "level": True}, "0101"),
    "io_pwm": ({"address": 4, "frequency": 10, "duty_cycle": 50}, "040000204100004842"),
    "external_motor": ({"index": 1, "enabled": True, "speed": -200}, "010138ffffff"),
    "color_sensor": ({"enabled": True, "port": 2, "version": 1}, "010201"),
    "ir_switch": ({"enabled": False, "port": 3, "version": 0}, "000300"),
    "angle_sensor_error": ({"rear_arm_angle": 1, "front_arm_angle": 2}, "0000803f00000040"),
    "wifi_config_mode": ({"enabled": True}, "01"),
    "wifi_ssid": ({"ssid": "AР"}, "41d0a000"),
    "wifi_password": ({"password": ""}, "00"),
    "wifi_ip_address": ({"dhcp": True, "address": "192.168.1.2"}, "01c0a80102"),
    "wifi_netmask": ({"address": "255.255.255.0"}, "ffffff00"),
    "wifi_gateway": ({"address": "192.168.1.1"}, "c0a80101"),
    "wifi_dns": ({"address": "8.8.4.4"}, "08080404"),
    "lost_step_params": ({"value": 1}, "0000803f"),
    "lost_step": ({}, ""),
    "queue_start": ({}, ""),
    "queue_stop": ({}, ""),
    "queue_force_stop": ({}, ""),
    "queue_stop_download": ({}, ""),
    "queue_clear": ({}, ""),
}

# Responses are separate fixtures, including asymmetric structures.
GET_VECTORS = {
    "device_sn": ("534e00", {"serial_number": "SN"}),
    "device_name": ("d0a000", {"name": "Р"}),
    "device_version": ("040003", {"major": 4, "minor": 0, "revision": 3}),
    "device_with_l": ("01", {"enabled": True}),
    "device_time": ("78563412", {"time_ms": 0x12345678}),
    "device_id": ("78563412f0debc9a04030201", {"device_id": (0x12345678, 0x9ABCDEF0, 0x01020304)}),
    "pose": (JOINT_HEX, {**POSITION, "joints": (4, 3, 2, 1)}),
    "pose_l": ("0000803f", {"l": 1}),
    "alarms": (
        "81000100000000000000000000000080",
        {"bitmap": bytes.fromhex("81000100000000000000000000000080"), "alarms": (0, 7, 16, 127)},
    ),
    "home_params": (POSITION_HEX, POSITION),
    "auto_leveling": ("0000803f", {"result": 1}),
    "hht_trigger_mode": ("01", {"mode": 1}),
    "hht_output_enabled": ("00", {"enabled": False}),
    "hht_trigger_output": ("01", {"triggered": True}),
    "end_effector_params": ("0000803f0000004000004040", {"x_bias": 1, "y_bias": 2, "z_bias": 3}),
    "laser": ("0001", {"enabled": False, "active": True}),
    "suction": ("0100", {"enabled": True, "active": False}),
    "gripper": ("0101", {"enabled": True, "active": True}),
    "jog_joint_params": (JOINT_HEX, {"velocity": (1, 2, 3, 4), "acceleration": (4, 3, 2, 1)}),
    "jog_coordinate_params": (JOINT_HEX, {"velocity": (1, 2, 3, 4), "acceleration": (4, 3, 2, 1)}),
    "jog_common_params": ("0000803f00000040", RATIOS),
    "jog_l_params": ("0000803f00000040", {"velocity": 1, "acceleration": 2}),
    "ptp_joint_params": (JOINT_HEX, {"velocity": (1, 2, 3, 4), "acceleration": (4, 3, 2, 1)}),
    "ptp_coordinate_params": (POSITION_HEX, COORDINATE),
    "ptp_jump_params": ("0000803f00000040", {"jump_height": 1, "z_limit": 2}),
    "ptp_common_params": ("0000803f00000040", RATIOS),
    "ptp_l_params": ("0000803f00000040", {"velocity": 1, "acceleration": 2}),
    "ptp_jump2_params": (
        "0000803f0000004000004040",
        {"start_jump_height": 1, "end_jump_height": 2, "z_limit": 3},
    ),
    "cp_params": (
        "0000803f000000400000404001",
        {"plan_acceleration": 1, "junction_velocity": 2, "period": 3, "real_time_track": True},
    ),
    "arc_params": (POSITION_HEX, COORDINATE),
    "io_multiplexing": ("1404", {"address": 20, "multiplex": 4}),
    "io_do": ("0100", {"address": 1, "level": False}),
    "io_pwm": ("040000204100004842", {"address": 4, "frequency": 10, "duty_cycle": 50}),
    "io_di": ("1401", {"address": 20, "level": True}),
    "io_adc": ("14ff0f", {"address": 20, "value": 4095}),
    "color_sensor": ("ff0080", {"r": 255, "g": 0, "b": 128}),
    "ir_switch": ("01", {"state": True}),
    "angle_sensor_error": ("0000803f00000040", {"rear_arm_angle": 1, "front_arm_angle": 2}),
    "wifi_config_mode": ("00", {"enabled": False}),
    "wifi_ssid": ("41d0a000", {"ssid": "AР"}),
    "wifi_password": ("00", {"password": ""}),
    "wifi_ip_address": ("01c0a80102", {"dhcp": True, "address": "192.168.1.2"}),
    "wifi_netmask": ("ffffff00", {"address": "255.255.255.0"}),
    "wifi_gateway": ("c0a80101", {"address": "192.168.1.1"}),
    "wifi_dns": ("08080404", {"address": "8.8.4.4"}),
    "wifi_connect_status": ("01", {"connected": True}),
    "queue_index": ("0807060504030201", {"queue_index": 0x0102030405060708}),
}


def test_complete_section_inventory_and_fixture_coverage():
    counts = {
        3: 6,
        4: 3,
        5: 2,
        6: 3,
        7: 3,
        8: 4,
        9: 5,
        10: 10,
        11: 3,
        12: 2,
        13: 1,
        14: 1,
        15: 8,
        16: 1,
        17: 8,
        18: 2,
        19: 7,
    }
    sections = {
        f"1.{chapter}.{section}"
        for chapter, count in counts.items()
        for section in range(1, count + 1)
    }
    assert len(COMMANDS) == 69
    assert {s.section for s in COMMANDS.values()} == sections
    assert len({s.section for s in COMMANDS.values()}) == 69
    assert set(SET_VECTORS) == {n for n, s in COMMANDS.items() if s.set_supported}
    assert set(GET_VECTORS) == {n for n, s in COMMANDS.items() if s.get_supported}
    assert all(n == s.name for n, s in COMMANDS.items())
    assert all(not s.default_queued or s.queueable for s in COMMANDS.values())


@pytest.mark.parametrize("name", SET_VECTORS)
def test_golden_set_parameters(name):
    params, expected = SET_VECTORS[name]
    assert COMMANDS[name].encode_set(**params) == bytes.fromhex(expected)


@pytest.mark.parametrize("name", GET_VECTORS)
def test_golden_get_response(name):
    encoded, expected = GET_VECTORS[name]
    assert COMMANDS[name].decode(bytes.fromhex(encoded)) == expected


@pytest.mark.parametrize("name", GET_VECTORS)
def test_get_request_parameters(name):
    params = (
        {"address": 7} if name in {"io_multiplexing", "io_do", "io_pwm", "io_di", "io_adc"} else {}
    )
    if name == "ir_switch":
        params = {"port": 3}
    assert COMMANDS[name].encode_get(**params) == (
        b"\x07" if "address" in params else b"\x03" if "port" in params else b""
    )


@pytest.mark.parametrize("name", SET_VECTORS)
def test_set_rejects_unknown_keywords_and_missing_required_fields(name):
    params, _ = SET_VECTORS[name]
    with pytest.raises(TypeError):
        COMMANDS[name].encode_set(**params, misspelled=True)
    if params:
        reduced = dict(params)
        reduced.pop(next(iter(reduced)))
        with pytest.raises((TypeError, ValueError)):
            COMMANDS[name].encode_set(**reduced)


@pytest.mark.parametrize("name", GET_VECTORS)
def test_get_rejects_unknown_keywords_and_wrong_reply_type(name):
    with pytest.raises(TypeError):
        COMMANDS[name].encode_get(misspelled=True)
    with pytest.raises(TypeError):
        COMMANDS[name].decode(bytearray())


@pytest.mark.parametrize("name", GET_VECTORS)
def test_get_rejects_oversized_response(name):
    with pytest.raises(ProtocolError):
        COMMANDS[name].decode(b"\0" * 254)


@pytest.mark.parametrize(
    "name", set(GET_VECTORS) - {"device_sn", "device_name", "wifi_ssid", "wifi_password"}
)
def test_fixed_get_response_lengths_are_exact(name):
    encoded, _ = GET_VECTORS[name]
    data = bytes.fromhex(encoded)
    for malformed in (data[:-1], data + b"\0"):
        with pytest.raises(ProtocolError):
            COMMANDS[name].decode(malformed)


@pytest.mark.parametrize("name", COMMANDS)
def test_unsupported_directions_are_explicit(name):
    spec = COMMANDS[name]
    if not spec.get_supported:
        with pytest.raises(UnsupportedCommand):
            spec.encode_get()
        with pytest.raises(UnsupportedCommand):
            spec.decode(b"")
    if not spec.set_supported:
        with pytest.raises(UnsupportedCommand):
            spec.encode_set()


def test_documented_refusals_explain_conflict():
    assert {n for n, s in COMMANDS.items() if s.unsupported_reason} == {
        "ptp_po_with_l",
        "queue_start_download",
    }
    for name in ("ptp_po_with_l", "queue_start_download"):
        with pytest.raises(UnsupportedCommand, match="PDF.*DobotLink"):
            COMMANDS[name].encode_set()


def test_known_full_packets_and_queue_flags():
    # Packet bytes independently calculated: ID, control, fields, checksum.
    assert encode(Frame(131, 3, COMMANDS["io_do"].encode_set(address=1, level=True))) == (
        bytes.fromhex("aaaa048303010178")
    )
    assert encode(Frame(133, 0, COMMANDS["io_di"].encode_get(address=7))) == (
        bytes.fromhex("aaaa0385000774")
    )
    assert encode(Frame(110, 3, COMMANDS["wait"].encode_set(timeout_ms=1000))) == (
        bytes.fromhex("aaaa066e03e8030000a4")
    )
    assert COMMANDS["clear_alarms"].command == 20
    assert COMMANDS["device_id"].command == 5
    assert COMMANDS["auto_leveling"].command == 32
    assert COMMANDS["ptp"].default_queued
    assert COMMANDS["jog"].queueable and not COMMANDS["jog"].default_queued
    assert not COMMANDS["queue_stop"].queueable


@pytest.mark.parametrize("mode", list(PTPMode))
def test_ptp_all_ten_modes(mode):
    assert COMMANDS["ptp"].encode_set(mode=mode, **POSITION) == bytes((mode,)) + bytes.fromhex(
        POSITION_HEX
    )
    assert COMMANDS["ptp_with_l"].encode_set(mode=mode, **POSITION, l=0)[0] == mode


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf, 1e39, -1e39, 10**400])
def test_float32_rejects_nonfinite_and_overflow(value):
    with pytest.raises(ValueError):
        COMMANDS["ptp"].encode_set(mode=1, **{**POSITION, "x": value})


@pytest.mark.parametrize("value", [True, "1", None, b"1", complex(1, 0)])
def test_float32_rejects_wrong_type(value):
    with pytest.raises(TypeError):
        COMMANDS["ptp"].encode_set(mode=1, **{**POSITION, "x": value})


@pytest.mark.parametrize("word", ["0000807f", "000080ff", "0000c07f"])
def test_nonfinite_get_float_is_protocol_error(word):
    with pytest.raises(ProtocolError):
        COMMANDS["pose_l"].decode(bytes.fromhex(word))
    with pytest.raises(ProtocolError):
        COMMANDS["ptp_joint_params"].decode(bytes.fromhex(word) + b"\0" * 28)


@pytest.mark.parametrize("value", [0, 1, 2, "true", None])
def test_tools_require_real_bool(value):
    with pytest.raises(TypeError):
        COMMANDS["laser"].encode_set(enabled=value, active=True)


@pytest.mark.parametrize(
    "name,data",
    [
        ("laser", b"\x02\x00"),
        ("suction", b"\x01\xff"),
        ("hht_trigger_mode", b"\x02"),
        ("ir_switch", b"\x02"),
        ("wifi_ip_address", b"\x02\0\0\0\0"),
    ],
)
def test_noncanonical_binary_and_enum_replies_rejected(name, data):
    with pytest.raises(ProtocolError):
        COMMANDS[name].decode(data)


@pytest.mark.parametrize(
    "name,params",
    [
        ("ptp", {"mode": 10, **POSITION}),
        ("ptp", {"mode": True, **POSITION}),
        ("jog", {"is_joint": True, "cmd": 9}),
        ("home", {"reserved": 1}),
        ("device_with_l", {"enabled": True, "version": 2}),
        ("wait", {"timeout_ms": -1}),
        ("wait", {"timeout_ms": 2**32}),
        ("wait", {"timeout_ms": 1.0}),
        ("wait", {"timeout_ms": True}),
        ("io_do", {"address": 0, "level": True}),
        ("io_do", {"address": 21, "level": True}),
        ("io_multiplexing", {"address": 1, "multiplex": 5}),
        ("io_pwm", {"address": 1, "frequency": 9, "duty_cycle": 50}),
        ("io_pwm", {"address": 1, "frequency": 1000001, "duty_cycle": 50}),
        ("io_pwm", {"address": 1, "frequency": 10, "duty_cycle": 101}),
        ("ptp_common_params", {"velocity_ratio": 101, "acceleration_ratio": 100}),
        ("ptp_coordinate_params", {**COORDINATE, "xyz_velocity": -1}),
        ("cp_laser", {"mode": 0, "x": 0, "y": 0, "z": 0, "power": 100.1}),
        ("external_motor", {"index": 2, "enabled": True, "speed": 0}),
        ("external_motor", {"index": 0, "enabled": True, "speed": -(2**31) - 1}),
        ("external_motor", {"index": 0, "enabled": True, "speed": 2**31}),
        ("external_motor", {"index": 0, "enabled": True, "speed": 1.0}),
        ("color_sensor", {"enabled": True, "port": 256, "version": 1}),
        ("ir_switch", {"enabled": True, "port": 1, "version": 2}),
        ("lost_step_params", {"value": -1}),
    ],
)
def test_individual_field_constraints(name, params):
    with pytest.raises((TypeError, ValueError)):
        COMMANDS[name].encode_set(**params)


@pytest.mark.parametrize(
    "name,params",
    [
        ("io_di", {}),
        ("io_adc", {"address": True}),
        ("io_do", {"address": 0}),
        ("ir_switch", {}),
        ("ir_switch", {"port": 256}),
    ],
)
def test_parameterized_reads_require_valid_address(name, params):
    with pytest.raises((TypeError, ValueError)):
        COMMANDS[name].encode_get(**params)


@pytest.mark.parametrize(
    "params",
    [
        {"velocity": [1, 2, 3], "acceleration": [1, 2, 3, 4]},
        {"velocity": [1, 2, 3, 4, 5], "acceleration": [1, 2, 3, 4]},
        {"velocity": "1234", "acceleration": [1, 2, 3, 4]},
        {"velocity": [1, 2, True, 4], "acceleration": [1, 2, 3, 4]},
        {"velocity": [1, 2, 3, 4], "acceleration": [1, -1, 3, 4]},
    ],
)
def test_vector_validation(params):
    with pytest.raises((TypeError, ValueError)):
        COMMANDS["ptp_joint_params"].encode_set(**params)


def test_integer_extremes_and_unsigned_get():
    for speed, word in [(-(2**31), "00000080"), (2**31 - 1, "ffffff7f")]:
        assert COMMANDS["external_motor"].encode_set(index=0, enabled=False, speed=speed) == (
            b"\0\0" + bytes.fromhex(word)
        )
    assert COMMANDS["wait"].encode_set(timeout_ms=2**32 - 1) == b"\xff" * 4
    assert COMMANDS["queue_index"].decode(b"\xff" * 8) == {"queue_index": 2**64 - 1}


def test_cp_union_selects_one_field():
    base = {"plan_acceleration": 1, "junction_velocity": 2, "real_time_track": True}
    assert COMMANDS["cp_params"].encode_set(**base, period=3) == bytes.fromhex(
        "0000803f000000400000404001"
    )
    for extras in ({}, {"acceleration": 3}, {"period": 3, "acceleration": 4}, {"period": -1}):
        with pytest.raises((TypeError, ValueError)):
            COMMANDS["cp_params"].encode_set(**base, **extras)
    assert COMMANDS["cp_params"].decode(bytes.fromhex("0000803f000000400000404000")) == {
        "plan_acceleration": 1,
        "junction_velocity": 2,
        "acceleration": 3,
        "real_time_track": False,
    }


@pytest.mark.parametrize(
    "mode,condition,threshold", [(0, 2, 0), (0, 0, 2), (1, 4, 1), (1, 0, 4096)]
)
def test_trigger_mode_specific_limits(mode, condition, threshold):
    with pytest.raises(ValueError):
        COMMANDS["trigger"].encode_set(
            address=1, mode=mode, condition=condition, threshold=threshold
        )


def test_trigger_digital_and_adc_packed_layout():
    assert COMMANDS["trigger"].encode_set(address=1, mode=0, condition=1, threshold=1) == (
        bytes.fromhex("0100010100")
    )
    assert COMMANDS["trigger"].encode_set(address=2, mode=1, condition=2, threshold=256) == (
        bytes.fromhex("0201020001")
    )


def test_ptp_po_packet_bound_and_nested_validation():
    output = {"ratio": 100, "address": 20, "level": True}
    assert len(COMMANDS["ptp_po"].encode_set(mode=1, **POSITION, outputs=[output] * 58)) == 250
    for outputs in (
        [output] * 59,
        [dict(output, ratio=101)],
        [dict(output, address=21)],
        [dict(output, level=1)],
        [{"ratio": 0, "address": 1}],
        [None],
        b"",
        {},
    ):
        with pytest.raises((TypeError, ValueError)):
            COMMANDS["ptp_po"].encode_set(mode=1, **POSITION, outputs=outputs)
    assert COMMANDS["ptp_po"].encode_set(mode=1, **POSITION, outputs=[]) == (
        b"\x01" + bytes.fromhex(POSITION_HEX) + b"\0"
    )


def test_arc_nested_validation():
    for point in (
        [1, 2, 3, 4],
        {"x": 1, "y": 2, "z": 3},
        {**POSITION, "extra": 1},
        {**POSITION, "r": math.nan},
    ):
        with pytest.raises((TypeError, ValueError)):
            COMMANDS["arc"].encode_set(cir_point=point, to_point=POSITION)


def test_version_extension_preserves_unknown_bytes():
    raw = bytes(range(14))
    assert COMMANDS["device_version"].decode(raw) == {
        "major": 0,
        "minor": 1,
        "revision": 2,
        "extension": bytes(range(3, 13)),
        "hardware_version": 13,
    }


@pytest.mark.parametrize(
    "name,key",
    [
        ("device_sn", "serial_number"),
        ("device_name", "name"),
        ("wifi_ssid", "ssid"),
        ("wifi_password", "password"),
    ],
)
def test_text_validation_and_frame_byte_bound(name, key):
    spec = COMMANDS[name]
    for value in (b"test", 123, None):
        with pytest.raises(TypeError):
            spec.encode_set(**{key: value})
    with pytest.raises(ValueError):
        spec.encode_set(**{key: "a\0b"})
    assert len(spec.encode_set(**{key: "a" * 252})) == 253
    with pytest.raises(ValueError):
        spec.encode_set(**{key: "a" * 253})
    assert spec.decode(b"abc") == {key: "abc"}
    assert spec.decode(b"abc\0\0\0") == {key: "abc"}
    with pytest.raises(ProtocolError):
        spec.decode(b"abc\0secret")


def test_utf8_uses_byte_lengths_and_does_not_leak_password():
    assert len(COMMANDS["wifi_ssid"].encode_set(ssid="Р" * 126)) == 253
    with pytest.raises(ValueError):
        COMMANDS["wifi_ssid"].encode_set(ssid="Р" * 127)
    with pytest.raises(ProtocolError) as error:
        COMMANDS["wifi_password"].decode(b"secret-password\0secret-tail")
    assert "secret" not in str(error.value)
    with pytest.raises(ProtocolError):
        COMMANDS["wifi_ssid"].decode(b"\xff")


def test_serial_number_uses_vendor_latin1_without_lossy_replacement():
    assert COMMANDS["device_sn"].encode_set(serial_number="SN-é") == b"SN-\xe9\0"
    assert COMMANDS["device_sn"].decode(b"SN-\xe9\0") == {"serial_number": "SN-é"}
    with pytest.raises(ValueError):
        COMMANDS["device_sn"].encode_set(serial_number="SN-Р")


def test_erased_device_identity_policy_is_not_applied_to_wifi_fields():
    for spec, key in (("wifi_ssid", "ssid"), ("wifi_password", "password")):
        assert COMMANDS[spec].decode(b"\0") == {key: ""}
        with pytest.raises(ProtocolError):
            COMMANDS[spec].decode(b"\xff\xff\0")


@pytest.mark.parametrize(
    "address",
    [
        "256.1.1.1",
        "1.2.3",
        "1.2.3.4/24",
        "::1",
        "01.2.3.4",
        [1, 2, 3, 4],
        b"\x01\x02\x03\x04",
        0,
        True,
    ],
)
def test_invalid_ipv4_arguments(address):
    with pytest.raises((TypeError, ValueError)):
        COMMANDS["wifi_gateway"].encode_set(address=address)


def test_ipv4_address_object_and_dhcp_validation():
    assert COMMANDS["wifi_dns"].encode_set(address=IPv4Address("1.2.3.4")) == b"\x01\x02\x03\x04"
    with pytest.raises(TypeError):
        COMMANDS["wifi_ip_address"].encode_set(address="1.2.3.4", dhcp=1)


def test_response_domain_constraints():
    for name, data in [
        ("io_adc", "010010"),
        ("io_do", "0001"),
        ("io_multiplexing", "0105"),
        ("io_pwm", "010000803f00004842"),
        ("ptp_common_params", "0000ca4200004842"),
    ]:
        with pytest.raises(ProtocolError):
            COMMANDS[name].decode(bytes.fromhex(data))


def test_float32_maximum_and_rounding():
    maximum = struct.unpack("<f", bytes.fromhex("ffff7f7f"))[0]
    assert COMMANDS["pose_l"].decode(bytes.fromhex("ffff7f7f")) == {"l": maximum}
    data = COMMANDS["ptp"].encode_set(mode=0, x=0.1, y=-0.0, z=maximum, r=-maximum)
    assert data == bytes.fromhex("00cdcccc3d00000080ffff7f7fffff7fff")
