"""Offline state/queue tests. No serial port is discovered or opened."""

import importlib
import struct
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import pytest

from dobot_usb.errors import ConnectionError, UnsupportedCommand
from dobot_usb.protocol import Frame

from .simulation import SimulatedTransport


def packed(format: str, *values: int | float) -> bytes:
    return struct.pack("<" + format, *values)


def pose(sim: SimulatedTransport) -> tuple[float, ...]:
    return struct.unpack("<8f", sim.request(10))


def index(sim: SimulatedTransport) -> int:
    return int(struct.unpack("<Q", sim.request(246))[0])


# Valid SET fixtures, independently expressed in wire format. All implemented
# SETs are exercised, with optional registry parity checks after integration.
SET_PACKETS = {
    0: b"TEST-SN\0",
    1: "Тест".encode() + b"\0",
    3: b"\x01\x01",
    11: packed("Bff", 1, 10, 20),
    20: b"",
    30: packed("4f", 100, 20, 30, 40),
    31: packed("I", 0),
    32: packed("Bf", 1, 0.1),
    40: b"\x01",
    41: b"\x01",
    60: packed("3f", 1, 2, 3),
    61: b"\x01\x01",
    62: b"\x01\x01",
    63: b"\x01\x01",
    70: packed("8f", *range(8)),
    71: packed("8f", *range(8)),
    72: packed("2f", 30, 40),
    73: b"\x01\x08",
    74: packed("2f", 10, 20),
    80: packed("8f", *range(8)),
    81: packed("4f", 10, 20, 30, 40),
    82: packed("2f", 10, -20),
    83: packed("2f", 20, 30),
    84: packed("B4f", 2, 10, 20, 30, 40),
    85: packed("2f", 10, 20),
    86: packed("B5f", 2, 10, 20, 30, 40, 50),
    87: packed("3f", 10, 20, -30),
    88: packed("B4fB", 2, 10, 20, 30, 40, 1) + packed("BHB", 50, 2, 1),
    90: packed("3fB", 10, 20, 30, 1),
    91: packed("B4f", 0, 10, 20, 30, 40),
    92: packed("B4f", 1, 10, 20, 30, 40),
    100: packed("4f", 10, 20, 30, 40),
    101: packed("8f", *range(8)),
    110: packed("I", 123),
    120: packed("BBBH", 1, 1, 3, 4095),
    130: b"\x02\x03",
    131: b"\x02\x01",
    132: packed("B2f", 2, 1000, 60),
    135: packed("BBi", 1, 1, -3000),
    137: b"\x01\x02\x01",
    138: b"\x01\x02\x01",
    140: packed("2f", -10, 20),
    150: b"\x01",
    151: b"fixture-ssid\0",
    152: b"fixture-password\0",
    153: b"\x01\xc0\xa8\x00\x01",
    154: b"\xff\xff\xff\x00",
    155: b"\xc0\xa8\x00\x01",
    156: b"\x08\x08\x08\x08",
    170: packed("f", 10),
    171: b"",
    240: b"",
    241: b"",
    242: b"",
    244: b"",
    245: b"",
}
QUEUEABLE = {
    30,
    31,
    32,
    60,
    61,
    62,
    63,
    70,
    71,
    72,
    73,
    74,
    80,
    81,
    82,
    83,
    84,
    85,
    86,
    87,
    88,
    90,
    91,
    92,
    100,
    101,
    110,
    120,
    130,
    131,
    132,
    135,
    137,
    138,
    171,
}
ADDRESS_GETS = {130, 131, 132, 133, 134}


def test_construction_and_reads_never_open_usb_or_start_queue() -> None:
    with patch("dobot_usb.transport.serial.Serial", side_effect=AssertionError("USB opened")):
        sim = SimulatedTransport()
        assert sim.requests == sim.transactions == []
        assert not sim.closed and not sim.running
        assert sim.request(0) == b"SIMULATED\0"
        assert sim.request(1).endswith(b"\0")
        assert sim.request(2) == b"\x04\0\x03"
        assert index(sim) == 0
        assert not sim.running


@pytest.mark.parametrize("command", SET_PACKETS)
def test_every_implemented_set_has_empty_immediate_ack(command: int) -> None:
    sim = SimulatedTransport()
    assert sim.request(command, SET_PACKETS[command], write=True) == b""
    assert sim.requests == [command]
    trace = sim.transactions[0]
    assert trace.command == command and trace.control == 1
    if command != 152:
        assert trace.params == SET_PACKETS[command]


@pytest.mark.parametrize("command", sorted(QUEUEABLE))
def test_queueable_commands_ack_uint64_without_changing_get_state(command: int) -> None:
    sim = SimulatedTransport()
    before = sim.responses.copy()
    assert sim.request(command, SET_PACKETS[command], write=True, queued=True) == packed("Q", 1)
    assert sim.responses == before
    assert sim.transactions == [Frame(command, 3, SET_PACKETS[command])]
    assert index(sim) == 0 and sim.advance(100) == 0
    sim.request(240, write=True)
    assert index(sim) == 1


def test_queue_fifo_one_command_per_index_read_and_pause_resume() -> None:
    sim = SimulatedTransport()
    original = pose(sim)
    for mode, x in [(2, 100), (7, 5), (8, 6)]:
        sim.request(84, packed("B4f", mode, x, 0, 0, 0), write=True, queued=True)
    assert index(sim) == 0 and pose(sim) == original
    sim.request(240, write=True)
    assert pose(sim) == original  # Start alone executes nothing.
    assert index(sim) == 1 and pose(sim)[:4] == (100, 0, 0, 0)
    sim.request(241, write=True)
    assert index(sim) == 1 and sim.advance(10) == 0
    sim.request(240, write=True)
    assert index(sim) == 2 and pose(sim)[0] == 105
    assert index(sim) == 3 and pose(sim)[0] == 111
    assert index(sim) == 3


def test_manual_advance_force_stop_clear_and_monotonic_indices() -> None:
    sim = SimulatedTransport(auto_complete=False)
    first = sim.request(62, b"\x01\x01", write=True, queued=True)
    assert first == packed("Q", 1)
    sim.request(240, write=True)
    assert index(sim) == 0
    assert sim.advance() == 1 and sim.request(62) == b"\x01\x01"
    sim.request(62, bytes(2), write=True, queued=True)
    sim.request(242, write=True)
    assert sim.advance() == 0 and sim.request(62) == b"\x01\x01"
    sim.request(240, write=True)
    assert sim.advance(2) == 1 and sim.request(62) == bytes(2)
    sim.request(62, b"\x01\x01", write=True, queued=True)
    sim.request(245, write=True)
    assert sim.advance() == 0 and index(sim) == 2
    assert sim.request(62, bytes(2), write=True, queued=True) == packed("Q", 4)
    assert sim.advance() == 1 and index(sim) == 4


def test_full_uint64_indices_and_no_silent_wrap() -> None:
    sim = SimulatedTransport()
    sim._last_index = 2**64 - 2  # Boundary fixture avoids billions of submissions.
    assert sim.request(171, write=True, queued=True) == packed("Q", 2**64 - 1)
    snapshot = (sim.responses.copy(), sim.requests.copy(), sim.transactions.copy())
    with pytest.raises(ValueError, match="exhausted"):
        sim.request(171, write=True, queued=True)
    assert snapshot == (sim.responses, sim.requests, sim.transactions)
    sim.request(240, write=True)
    assert index(sim) == 2**64 - 1


@pytest.mark.parametrize("mode", range(10))
def test_all_ptp_modes_keep_independent_cartesian_and_joint_state(mode: int) -> None:
    sim = SimulatedTransport()
    before = pose(sim)
    target = (10, 20, 30, 40)
    sim.request(84, packed("B4f", mode, *target), write=True)
    after = pose(sim)
    if mode in {0, 1, 2, 9}:
        assert after == target + before[4:]
    elif mode in {3, 4, 5}:
        assert after == before[:4] + target
    elif mode == 6:
        assert after == before[:4] + tuple(a + b for a, b in zip(before[4:], target, strict=True))
    else:
        assert after == tuple(a + b for a, b in zip(before[:4], target, strict=True)) + before[4:]


def test_rail_home_arc_reset_pose_and_synchronized_output() -> None:
    sim = SimulatedTransport()
    sim.request(86, SET_PACKETS[86], write=True)
    assert pose(sim)[:4] == (10, 20, 30, 40)
    assert sim.request(13) == packed("f", 50)
    sim.request(30, SET_PACKETS[30], write=True, queued=True)
    sim.request(31, SET_PACKETS[31], write=True, queued=True)
    sim.request(240, write=True)
    assert index(sim) == 1 and pose(sim)[:4] == (10, 20, 30, 40)
    assert index(sim) == 2 and pose(sim)[:4] == (100, 20, 30, 40)
    sim.request(101, packed("8f", 0, 1, 2, 3, 4, 5, 6, 7), write=True)
    assert pose(sim)[:4] == (4, 5, 6, 7)
    sim.request(11, SET_PACKETS[11], write=True)
    assert pose(sim)[5:7] == (10, 20)
    sim.request(88, SET_PACKETS[88], write=True, queued=True)
    assert sim.request(131, b"\x02") == b"\x02\0"
    assert index(sim) == 3
    assert pose(sim)[:4] == (10, 20, 30, 40)
    assert sim.request(131, b"\x02") == b"\x02\x01"


@pytest.mark.parametrize("command", [91, 92])
def test_cp_relative_absolute_power_and_orientation(command: int) -> None:
    sim = SimulatedTransport()
    before = pose(sim)
    sim.request(command, packed("B4f", 0, 1, 2, 3, 95), write=True)
    assert pose(sim) == (201, 2, 53) + before[3:]
    sim.request(command, packed("B4f", 1, 10, 20, 30, 5), write=True)
    assert pose(sim) == (10, 20, 30) + before[3:]


@pytest.mark.parametrize(
    "command",
    [
        0,
        1,
        3,
        30,
        40,
        41,
        60,
        61,
        62,
        63,
        70,
        71,
        72,
        74,
        80,
        81,
        82,
        83,
        85,
        87,
        90,
        100,
        140,
        150,
        151,
        152,
        153,
        154,
        155,
        156,
    ],
)
def test_configuration_set_get_roundtrip(command: int) -> None:
    sim = SimulatedTransport()
    packet = SET_PACKETS[command]
    sim.request(command, packet, write=True)
    assert sim.request(command) == (packet[:1] if command == 3 else packet)


@pytest.mark.parametrize("command", [130, 131, 132])
def test_addressed_io_persists_per_address_only_after_execution(command: int) -> None:
    sim = SimulatedTransport()
    packet = SET_PACKETS[command]
    default = sim.request(command, b"\x02")
    sim.request(command, packet, write=True, queued=True)
    assert sim.request(command, b"\x02") == default
    sim.request(240, write=True)
    assert index(sim) == 1
    assert sim.request(command, b"\x02") == packet
    assert sim.request(command, b"\x03") == b"\x03" + default[1:]


@pytest.mark.parametrize("command", [133, 134])
def test_input_queries_echo_address_without_output_loopback(command: int) -> None:
    sim = SimulatedTransport()
    sim.request(131, b"\x07\x01", write=True)
    assert sim.request(command, b"\x07") == b"\x07" + bytes(2 if command == 134 else 1)


def test_sensor_wait_trigger_jog_and_motor_do_not_invent_physics() -> None:
    sim = SimulatedTransport()
    before = pose(sim)
    for command in (32, 73, 110, 120, 135, 137, 138, 170, 171):
        sim.request(command, SET_PACKETS[command], write=True)
    assert pose(sim) == before
    assert sim.request(32) == bytes(4)
    assert sim.request(137) == bytes(3)
    assert sim.request(138, b"\x02") == b"\0"
    assert sim.request(4) == bytes(4)  # Fixed device time, no wall clock.
    assert sim.request(157) == b"\0"  # WiFi settings do not establish a connection.


def test_fault_fixtures_and_password_redaction() -> None:
    sim = SimulatedTransport()
    sim.responses[0] = b"\xff" * 89 + b"\0"
    sim.responses[1] = b"\0"
    sim.responses[2] = b"bad-version"
    assert sim.request(0) == b"\xff" * 89 + b"\0"
    assert sim.request(1) == b"\0" and sim.request(2) == b"bad-version"
    sim.responses[20] = b"\x01" + bytes(15)
    sim.request(20, write=True)
    assert sim.request(20) == bytes(16)
    sim.request(152, b"very-private-password\0", write=True)
    assert sim.request(152) == b"very-private-password\0"
    assert b"very-private-password" not in repr(sim.transactions).encode()
    assert b"very-private-password" not in repr(sim).encode()
    assert sim.transactions[-2] == Frame(152, 1, b"<redacted>")


@pytest.mark.parametrize("command", [c for c in SET_PACKETS if c not in {0, 1, 151, 152}])
def test_invalid_set_lengths_never_mutate_or_record(command: int) -> None:
    sim = SimulatedTransport()
    snapshot = sim.responses.copy()
    packet = SET_PACKETS[command]
    invalid = [packet + b"\0"] + ([packet[:-1]] if packet else [])
    for data in invalid:
        with pytest.raises(ValueError):
            sim.request(command, data, write=True, queued=command in QUEUEABLE)
    assert sim.responses == snapshot and sim.requests == sim.transactions == []
    assert not sim.running and sim.advance(100) == 0


@pytest.mark.parametrize(
    ("command", "packet"),
    [
        (3, b"\x02\0"),
        (3, b"\0\x02"),
        (11, packed("Bff", 2, 0, 0)),
        (31, packed("I", 1)),
        (32, packed("Bf", 1, -1)),
        (40, b"\x02"),
        (61, b"\x01\x02"),
        (70, packed("8f", -1, *([0] * 7))),
        (72, packed("2f", 101, 0)),
        (73, b"\x01\x09"),
        (82, packed("2f", -1, 0)),
        (84, packed("B4f", 10, 0, 0, 0, 0)),
        (84, packed("B4f", 2, float("nan"), 0, 0, 0)),
        (86, packed("B5f", 2, 0, 0, 0, 0, float("inf"))),
        (88, SET_PACKETS[88][:-1] + b"\x02"),
        (90, packed("3fB", 1, 2, 3, 2)),
        (91, packed("B4f", 2, 0, 0, 0, 0)),
        (92, packed("B4f", 0, 0, 0, 0, 101)),
        (101, packed("8f", *([float("inf")] * 8))),
        (120, packed("BBBH", 1, 0, 2, 0)),
        (130, b"\0\0"),
        (130, b"\x01\x05"),
        (131, b"\x01\x02"),
        (132, packed("B2f", 1, 9, 0)),
        (135, packed("BBi", 2, 1, 0)),
        (137, b"\x01\0\x02"),
        (153, b"\x02" + bytes(4)),
        (170, packed("f", -1)),
        (0, b"not-terminated"),
        (1, b"\xff\0"),
        (151, b"bad\0tail\0"),
        (152, b"secret\0tail"),
    ],
)
def test_invalid_raw_values_never_mutate_state(command: int, packet: bytes) -> None:
    sim = SimulatedTransport()
    snapshot = sim.responses.copy()
    with pytest.raises(ValueError):
        sim.request(command, packet, write=True)
    assert sim.responses == snapshot and not sim.requests and not sim.transactions


@pytest.mark.parametrize("command", [89, 243, 255])
def test_explicitly_unsupported_packets(command: int) -> None:
    sim = SimulatedTransport()
    for write in (False, True):
        with pytest.raises(UnsupportedCommand):
            sim.request(command, write=write)
    assert sim.requests == sim.transactions == []


@pytest.mark.parametrize(
    "kwargs",
    [
        {"command": True},
        {"command": -1},
        {"command": 256},
        {"command": 2.0},
        {"command": 10, "queued": True},
        {"command": 10, "write": 1},
        {"command": 10, "queued": 0},
        {"command": 10, "params": bytearray()},
        {"command": 10, "params": b"\0"},
        {"command": 130, "params": b""},
        {"command": 130, "params": b"\x15"},
        {"command": 240, "write": True, "queued": True},
    ],
)
def test_request_boundary_validation(kwargs: dict) -> None:
    sim = SimulatedTransport()
    with pytest.raises((ValueError, TypeError, UnsupportedCommand)):
        sim.request(**kwargs)
    assert sim.requests == sim.transactions == []


def test_read_only_policy_and_closed_session_reject_before_io() -> None:
    sim = SimulatedTransport(allow_writes=False)
    for command in (0, 1, 2, 10, 20, 61, 62, 63, 246):
        sim.request(command)
    sim.requests.clear()
    sim.transactions.clear()
    for command, params, write in [(62, b"\x01\x01", True), (3, b"", False), (10, b"\0", False)]:
        with pytest.raises(UnsupportedCommand):
            sim.request(command, params, write=write)
    assert sim.requests == sim.transactions == []
    sim.close()
    sim.close()
    with pytest.raises(ConnectionError):
        sim.request(10)
    with pytest.raises(ConnectionError):
        sim.advance()


@pytest.mark.parametrize("value", [True, -1, 1.5])
def test_advance_invalid_count(value: object) -> None:
    sim = SimulatedTransport()
    with pytest.raises(ValueError):
        sim.advance(value)
    assert sim.requests == sim.transactions == []


@pytest.mark.parametrize("kwargs", [{"allow_writes": 1}, {"auto_complete": 1}])
def test_constructor_requires_actual_booleans(kwargs: dict) -> None:
    with pytest.raises(TypeError):
        SimulatedTransport(**kwargs)


def test_concurrent_submissions_have_unique_ordered_indices() -> None:
    sim = SimulatedTransport()
    with ThreadPoolExecutor(max_workers=4) as pool:
        acknowledgements = list(
            pool.map(lambda _: sim.request(171, write=True, queued=True), range(40))
        )
    assert sorted(struct.unpack("<Q", data)[0] for data in acknowledgements) == list(range(1, 41))
    sim.request(240, write=True)
    assert sim.advance(40) == 40 and index(sim) == 40


def test_relative_float32_overflow_does_not_partially_update_pose_or_rail() -> None:
    sim = SimulatedTransport()
    sim.request(86, packed("B5f", 2, 3e38, 0, 0, 0, 10), write=True)
    before = sim.responses.copy()
    sim.requests.clear()
    sim.transactions.clear()
    with pytest.raises(ValueError):
        sim.request(86, packed("B5f", 7, 3e38, 0, 0, 0, 20), write=True)
    assert sim.responses == before and sim.requests == sim.transactions == []


def test_canonical_registry_defaults_and_set_contract_after_integration() -> None:
    try:
        registry = importlib.import_module("dobot_usb.commands").COMMANDS
    except ModuleNotFoundError as exc:
        if exc.name != "dobot_usb.commands":
            raise
        pytest.skip("commands.py belongs to a separate clone until root integration")
    sim = SimulatedTransport()
    readable = {spec.command for spec in registry.values() if spec.get_supported}
    writable = {spec.command for spec in registry.values() if spec.set_supported}
    assert readable == set(sim.responses)
    assert writable == SET_PACKETS.keys()
    assert QUEUEABLE == {
        spec.command for spec in registry.values() if spec.set_supported and spec.queueable
    }
    for spec in registry.values():
        if spec.get_supported:
            params = b"\x01" if spec.command in ADDRESS_GETS | {138} else b""
            spec.decode(sim.request(spec.command, params))
        if spec.set_supported:
            sim.request(spec.command, SET_PACKETS[spec.command], write=True)


@pytest.mark.parametrize("bad_float", [float("nan"), float("inf"), -float("inf")])
def test_invalid_queued_float_does_not_consume_index_or_disturb_existing_work(
    bad_float: float,
) -> None:
    sim = SimulatedTransport()
    sim.request(62, b"\x01\x01", write=True, queued=True)
    before = (sim.responses.copy(), sim.requests.copy(), sim.transactions.copy())
    with pytest.raises(ValueError):
        sim.request(84, packed("B4f", 2, bad_float, 0, 0, 0), write=True, queued=True)
    assert before == (sim.responses, sim.requests, sim.transactions)
    assert sim.request(62, bytes(2), write=True, queued=True) == packed("Q", 2)
    sim.request(240, write=True)
    assert index(sim) == 1 and sim.request(62) == b"\x01\x01"
    assert index(sim) == 2 and sim.request(62) == bytes(2)


def test_close_does_not_execute_pending_work_or_add_lifecycle_requests() -> None:
    sim = SimulatedTransport()
    sim.request(62, b"\x01\x01", write=True, queued=True)
    sim.request(240, write=True)
    before = (sim.responses.copy(), sim.requests.copy(), sim.transactions.copy())
    sim.close()
    assert before == (sim.responses, sim.requests, sim.transactions)
    assert sim.responses[62] == bytes(2) and sim.responses[246] == bytes(8)
    assert sim.closed
