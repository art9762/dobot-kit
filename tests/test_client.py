import json
import struct
from collections.abc import Callable
from unittest.mock import patch

import pytest

from dobot_usb import ConnectionError, Dobot, ProtocolError, UnsupportedCommand, connect, ports
from dobot_usb.cli import main
from dobot_usb.discovery import discover

from .simulation import SimulatedTransport


def test_context_reads_only_and_closes() -> None:
    transport = SimulatedTransport()
    with Dobot(transport) as robot:
        state = robot.status()
        assert state.device.firmware == "4.0.3"
        assert state.pose.joints == (0, 30, 40, 0)
        assert state.alarms == ()
    assert transport.requests == [2, 0, 1, 10, 20]
    assert transport.closed
    with pytest.raises(ConnectionError):
        robot.pose()


@pytest.mark.parametrize(
    "version", [bytes([1, 2, 3]), bytes.fromhex("0400030200000000000004000300")]
)
def test_documented_and_observed_version_formats_preserved(version: bytes) -> None:
    transport = SimulatedTransport()
    transport.responses[2] = version
    info = Dobot(transport).device_info()
    assert info.firmware_raw == version
    assert info.firmware == ".".join(str(v) for v in version[:3])


@pytest.mark.parametrize("data", [b"", bytes(2), bytes(4), bytes(13), bytes(15)])
def test_unknown_version_formats_rejected(data: bytes) -> None:
    transport = SimulatedTransport()
    transport.responses[2] = data
    with pytest.raises(ProtocolError):
        Dobot(transport).device_info()


@pytest.mark.parametrize(
    "serial,name,expected_sn",
    [
        (b"\xff" * 89 + b"\0", b"\xff" * 65 + b"\0", None),
        (b"SAMPLE1234567\0", b"\0", "SAMPLE1234567"),
    ],
)
def test_diagnostic_identity_apis_agree_on_unset_flash_fields(
    serial: bytes, name: bytes, expected_sn: str | None
) -> None:
    transport = SimulatedTransport(allow_writes=False)
    transport.responses.update({0: serial, 1: name})
    with Dobot(transport) as robot:
        info = robot.device_info()
        identification = robot.settings.identification()
        assert info.serial_number == identification["device_sn"]["serial_number"] == expected_sn
        assert info.name is identification["device_name"]["name"] is None
        assert identification["device_id"]["device_id"] == (1, 2, 3)
        assert all(frame.control == 0 and frame.params == b"" for frame in transport.transactions)


@pytest.mark.parametrize("data", [b"bad\nname\0", b"\xffbad\0", b"\xff\0junk"])
def test_device_text_rejects_malformed_responses(data: bytes) -> None:
    transport = SimulatedTransport()
    transport.responses[1] = data
    with pytest.raises(ProtocolError):
        Dobot(transport).device_info()


@pytest.mark.parametrize("pose", [b"", bytes(31), struct.pack("<8f", *([float("nan")] * 8))])
def test_invalid_pose_data_rejected(pose: bytes) -> None:
    transport = SimulatedTransport()
    transport.responses[10] = pose
    with pytest.raises(ProtocolError):
        Dobot(transport).pose()


def test_alarm_bit_numbering() -> None:
    transport = SimulatedTransport()
    transport.responses[20] = b"\x81\x01" + bytes(13) + b"\x80"
    assert Dobot(transport).alarms() == (0, 7, 8, 127)


def test_bad_alarm_length() -> None:
    transport = SimulatedTransport()
    transport.responses[20] = bytes(15)
    with pytest.raises(ProtocolError):
        Dobot(transport).alarms()


@pytest.mark.parametrize("tool,command", [("suction", 62), ("gripper", 63), ("laser", 61)])
def test_tool_query_does_not_enable_outputs(tool: str, command: int) -> None:
    transport = SimulatedTransport()
    transport.responses[command] = b"\x01\x00"
    state = Dobot(transport).tool_state(tool)
    assert state.enabled and not state.active
    assert transport.requests == [command]


@pytest.mark.parametrize("data", [b"", b"\0", b"\x02\0", b"\0\0\0"])
def test_tool_boolean_shape(data: bytes) -> None:
    transport = SimulatedTransport()
    transport.responses[62] = data
    with pytest.raises(ProtocolError):
        Dobot(transport).tool_state("suction")


def test_unknown_tool_rejected_before_query() -> None:
    transport = SimulatedTransport()
    with pytest.raises(ValueError):
        Dobot(transport).tool_state("unknown")
    assert not transport.requests


def test_full_width_queue_index() -> None:
    transport = SimulatedTransport()
    transport.responses[246] = struct.pack("<Q", 2**64 - 1)
    assert Dobot(transport).queue_index() == 2**64 - 1
    transport.responses[246] = bytes(7)
    with pytest.raises(ProtocolError):
        Dobot(transport).queue_index()


def test_read_only_inspection_of_tool_and_ptp_settings() -> None:
    transport = SimulatedTransport(allow_writes=False)
    with Dobot(transport) as robot:
        assert robot.tool.get_offset().z == 0
        assert robot.advanced.get("ptp_joint_params")["velocity"] == (50,) * 4
        assert robot.advanced.get("ptp_coordinate_params")["xyz_velocity"] == 50
        assert robot.advanced.get("ptp_jump_params")["jump_height"] == 20
        assert robot.settings.velocity_ratios().velocity_ratio == 100
        with pytest.raises(UnsupportedCommand):
            robot.advanced.read(60)
        assert transport.requests == [60, 80, 81, 82, 83]
        with pytest.raises(UnsupportedCommand):
            robot.tool.offset(x=0, y=0, z=10)
        assert all(frame.control == 0 and frame.params == b"" for frame in transport.transactions)


@pytest.mark.parametrize("command", [31, 84, 240, -1, 256, True])
def test_advanced_rejects_unenabled_commands_without_io(command: int) -> None:
    transport = SimulatedTransport()
    with pytest.raises(UnsupportedCommand):
        Dobot(transport).advanced.read(command)
    assert not transport.requests


def test_handshake_failure_releases_port() -> None:
    transport = SimulatedTransport()
    transport.responses[10] = b""
    with patch("dobot_usb.client.SerialTransport", return_value=transport):
        with pytest.raises(ProtocolError):
            Dobot.connect("mock-port")
    assert transport.closed


@pytest.mark.parametrize("open_robot", [Dobot.connect, connect])
def test_successful_handshake_uses_queries_only(open_robot: Callable[..., Dobot]) -> None:
    transport = SimulatedTransport()
    with patch("dobot_usb.client.SerialTransport", return_value=transport) as factory:
        with open_robot("mock-port", timeout=0.2):
            pass
    factory.assert_called_once_with("mock-port", timeout=0.2, allow_writes=True)
    assert transport.requests == [2, 0, 1, 10, 20]


@pytest.mark.parametrize("args", [["mock-port"], ["--port", "mock-port"]])
def test_cli_status_opens_the_named_port_read_only(
    capsys: pytest.CaptureFixture[str], args: list[str]
) -> None:
    transport = SimulatedTransport(allow_writes=False)
    with patch("dobot_usb.client.SerialTransport", return_value=transport) as factory:
        assert main(["status", *args, "--timeout", "0.2"]) == 0
    factory.assert_called_once_with("mock-port", timeout=0.2, allow_writes=False)
    output = json.loads(capsys.readouterr().out)
    assert set(output) == {"device", "pose", "alarms"}
    assert output["device"]["firmware_raw"] == "040003"
    assert output["pose"]["x"] == 200
    assert transport.closed
    assert all(frame.control == 0 for frame in transport.transactions)


@pytest.mark.parametrize(
    "argv",
    [
        ["status"],
        ["status", "--simulate"],
        ["status", "one-port", "--port", "other-port"],
        ["status", "--port", "mock-port", "--simulate"],
        ["demo"],
    ],
)
def test_cli_rejects_missing_port_and_unsupported_commands(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as error:
        main(argv)
    assert error.value.code == 2


def test_discovery_only_enumerates() -> None:
    from types import SimpleNamespace

    candidate = SimpleNamespace(
        device="mock-port", description="USB", vid=0x1A86, pid=0x55D4, serial_number=None
    )
    with (
        patch("dobot_usb.discovery.list_ports.comports", return_value=[candidate]),
        patch("dobot_usb.transport.serial.Serial", side_effect=AssertionError("USB opened")),
    ):
        assert ports() == discover()
        assert ports()[0].device == "mock-port"
