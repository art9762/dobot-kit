import threading

import pytest
import serial

from dobot_usb import Dobot
from dobot_usb.errors import ProtocolError, RequestTimeout
from dobot_usb.protocol import Frame, encode
from tests.test_dobot import MagicianDouble


@pytest.fixture
def magician(monkeypatch: pytest.MonkeyPatch) -> MagicianDouble:
    device = MagicianDouble()
    monkeypatch.setattr("serial.Serial", device.open)
    return device


@pytest.mark.parametrize("port, wire_port", [("GP1", 0), ("GP2", 1), ("GP4", 2), ("GP5", 3)])
@pytest.mark.parametrize("version", [1, 2])
@pytest.mark.parametrize("enabled", [False, True])
def test_configuration_is_immediate_and_never_touches_queue(
    magician: MagicianDouble, port: str, wire_port: int, version: int, enabled: bool
) -> None:
    with Dobot("test-port") as robot:
        robot.color_sensor(enabled, port=port, version=version)
    assert magician.requests == [
        Frame(10, 0),
        Frame(137, 1, bytes((int(enabled), wire_port, version - 1))),
    ]


def test_official_sdk_derived_packet_fixtures() -> None:
    assert encode(Frame(137, 1, b"\x01\x01\x01")) == bytes.fromhex("AA AA 05 89 01 01 01 01 73")
    assert encode(Frame(137, 0)) == bytes.fromhex("AA AA 02 89 00 77")


@pytest.mark.parametrize(
    "rgb", [b"\x00\x00\x00", b"\x01\x00\x00", b"\x0c\x22\x38", b"\xff\xff\xff"]
)
def test_read_rgb_has_no_parameters_or_writes(magician: MagicianDouble, rgb: bytes) -> None:
    magician.color_reply = rgb
    with Dobot("test-port") as robot:
        assert robot.read_color() == tuple(rgb)
    assert magician.requests == [Frame(10, 0), Frame(137, 0)]
    assert magician.writes == []


@pytest.mark.parametrize("payload", [b"", b"\x00\x01", bytes(4), bytes(6)])
def test_bad_rgb_length_is_not_silently_truncated(magician: MagicianDouble, payload: bytes) -> None:
    magician.color_reply = payload
    with Dobot("test-port") as robot, pytest.raises(ProtocolError):
        robot.read_color()
    assert magician.writes == []


@pytest.mark.parametrize(
    ("enabled", "port", "version"),
    [
        (1, "GP2", 2),
        (True, 1, 2),
        (True, "GP3", 2),
        (True, "gp2", 2),
        (True, [], 2),
        (True, "GP2", True),
        (True, "GP2", 2.0),
        (True, "GP2", 0),
        (True, "GP2", 3),
    ],
)
def test_invalid_color_options_do_not_write(
    magician: MagicianDouble, enabled: object, port: object, version: object
) -> None:
    with Dobot("test-port") as robot:
        with pytest.raises(ValueError):
            robot.color_sensor(enabled, port=port, version=version)
    assert magician.writes == []


def test_timeout_does_not_retry_or_fallback(
    magician: MagicianDouble, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = magician.reply

    def lose_ack(frame: Frame) -> bytes:
        response = original(frame)
        if frame.command == 137:
            assert magician.device is not None
            magician.device.read_error = serial.SerialTimeoutException("lost sensor ACK")
            return b""
        return response

    monkeypatch.setattr(magician, "reply", lose_ack)
    with Dobot("test-port") as robot:
        with pytest.raises(RequestTimeout) as error:
            robot.color_sensor(port="GP2", version=2)
    assert error.value.stage == "sent_unconfirmed"
    assert magician.writes == [Frame(137, 1, b"\x01\x01\x01")]


def test_sensor_configuration_does_not_prepare_future_motion(magician: MagicianDouble) -> None:
    with Dobot("test-port") as robot:
        robot.color_sensor(port="GP2", version=2)
        assert magician.writes == [Frame(137, 1, b"\x01\x01\x01")]
        robot.move(200, 5, 50, 30)
    assert [f.command for f in magician.writes] == [137, 242, 245, 84, 240]


def test_watcher_calls_initial_and_changed_values_only(
    magician: MagicianDouble, monkeypatch: pytest.MonkeyPatch
) -> None:
    samples = iter([(1, 0, 0), (1, 0, 0), (0, 1, 0)])
    seen = []

    def handler(rgb: tuple[int, int, int]) -> bool:
        seen.append(rgb)
        return len(seen) < 2

    with Dobot("test-port") as robot:
        monkeypatch.setattr(robot, "read_color", lambda: next(samples))
        robot.watch_color(handler, interval=0.01)
    assert seen == [(1, 0, 0), (0, 1, 0)]
    assert magician.writes == []


def test_watcher_can_report_duplicates_and_stop_from_handler(magician: MagicianDouble) -> None:
    event = threading.Event()
    seen = []

    def handler(rgb: tuple[int, int, int]) -> None:
        seen.append(rgb)
        if len(seen) == 2:
            event.set()

    with Dobot("test-port") as robot:
        robot.watch_color(handler, interval=0.01, stop_event=event, changes_only=False)
    assert seen == [(12, 34, 56)] * 2
    assert magician.writes == []


def test_pre_stopped_watcher_does_not_read(magician: MagicianDouble) -> None:
    event = threading.Event()
    event.set()
    with Dobot("test-port") as robot:
        robot.watch_color(lambda rgb: None, stop_event=event)
    assert magician.requests == [Frame(10, 0)]


def test_watcher_propagates_handler_error(magician: MagicianDouble) -> None:
    def handler(rgb: tuple[int, int, int]) -> None:
        raise RuntimeError("handler failure")

    with Dobot("test-port") as robot:
        with pytest.raises(RuntimeError, match="handler failure"):
            robot.watch_color(handler)
    assert magician.requests == [Frame(10, 0), Frame(137, 0)]


@pytest.mark.parametrize(
    "options",
    [
        {"interval": 0},
        {"interval": float("nan")},
        {"interval": 61},
        {"changes_only": 1},
        {"stop_event": True},
    ],
)
def test_watcher_validates_before_reading(magician: MagicianDouble, options: dict) -> None:
    with Dobot("test-port") as robot:
        with pytest.raises(ValueError):
            robot.watch_color(lambda rgb: None, **options)
    assert magician.requests == [Frame(10, 0)]
