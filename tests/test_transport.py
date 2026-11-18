from collections.abc import Callable
from typing import Any

import pytest
import serial

from dobotkit import transport
from dobotkit.errors import ConnectionError, ProtocolError, RequestTimeout, UnsupportedCommand
from dobotkit.protocol import Frame, FrameParser, encode
from dobotkit.transport import READ_ONLY_COMMANDS, SerialTransport


class FakeSerial:
    """Replies are injected only after a complete query has been written."""

    def __init__(self, **options: Any) -> None:
        self.options = options
        self.port = options["port"]
        self.dtr = True
        self.rts = True
        self.is_open = False
        self.timeout = options["timeout"]
        self.write_timeout = options["write_timeout"]
        self.open_state: tuple[bool, bool, str] | None = None
        self.input = bytearray()
        self.writes: list[bytes] = []
        self.parser = FrameParser()
        self.max_write: int | None = None
        self.reply: Callable[[Frame], bytes] = lambda frame: encode(Frame(frame.command, 0, b"ok"))
        self.write_error: BaseException | None = None
        self.read_error: BaseException | None = None
        self.close_count = 0

    def open(self) -> None:
        self.is_open = True
        self.open_state = (self.dtr, self.rts, self.port)

    def reset_input_buffer(self) -> None:
        self.input.clear()

    @property
    def in_waiting(self) -> int:
        return len(self.input)

    def write(self, packet: bytes) -> int:
        if self.write_error:
            raise self.write_error
        count = min(len(packet), self.max_write) if self.max_write is not None else len(packet)
        chunk = packet[:count]
        self.writes.append(chunk)
        for frame in self.parser.feed(chunk):
            self.input.extend(self.reply(frame))
        return count

    def read(self, count: int) -> bytes:
        if self.read_error:
            raise self.read_error
        data = bytes(self.input[:count])
        del self.input[:count]
        return data

    def close(self) -> None:
        self.close_count += 1
        self.is_open = False


@pytest.fixture
def fake_serial(monkeypatch: pytest.MonkeyPatch) -> list[FakeSerial]:
    instances: list[FakeSerial] = []

    def factory(**options: Any) -> FakeSerial:
        instance = FakeSerial(**options)
        instances.append(instance)
        return instance

    monkeypatch.setattr(transport.serial, "Serial", factory)
    return instances


def test_open_is_get_free_and_sets_lines_before_open(fake_serial: list[FakeSerial]) -> None:
    link = SerialTransport("fake-port")
    device = fake_serial[0]
    assert device.open_state == (False, False, "fake-port")
    assert device.options["baudrate"] == 115200
    assert device.options["bytesize"] == serial.EIGHTBITS
    assert device.options["parity"] == serial.PARITY_NONE
    assert device.options["stopbits"] == serial.STOPBITS_ONE
    assert device.writes == []
    link.close()
    link.close()
    assert device.close_count == 1


@pytest.mark.parametrize("command", sorted(READ_ONLY_COMMANDS))
def test_queries_always_have_read_control(fake_serial: list[FakeSerial], command: int) -> None:
    link = SerialTransport("fake-port")
    assert link.request(command) == b"ok"
    assert fake_serial[0].writes == [encode(Frame(command, 0))]
    link.close()


@pytest.mark.parametrize("command", [84, 240, 242, -1, 256, True, 10.0, "10"])
def test_unsupported_or_bad_commands_never_write(
    fake_serial: list[FakeSerial], command: Any
) -> None:
    link = SerialTransport("fake-port")
    with pytest.raises(UnsupportedCommand):
        link.request(command)
    assert fake_serial[0].writes == []
    assert fake_serial[0].is_open
    link.close()


def test_query_parameters_rejected_before_io(fake_serial: list[FakeSerial]) -> None:
    link = SerialTransport("fake-port")
    with pytest.raises(UnsupportedCommand):
        link.request(10, b"\x01")
    with pytest.raises(TypeError):
        link.request(10, bytearray())  # type: ignore[arg-type]
    assert fake_serial[0].writes == []
    link.close()


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan"), True, "1"])
def test_invalid_timeout_never_opens(fake_serial: list[FakeSerial], timeout: Any) -> None:
    with pytest.raises(ValueError, match="timeout"):
        SerialTransport("fake-port", timeout=timeout)
    assert fake_serial == []


@pytest.mark.parametrize("port", ["", "   ", None, 123])
def test_explicit_port_required(fake_serial: list[FakeSerial], port: Any) -> None:
    with pytest.raises(ValueError, match="port"):
        SerialTransport(port)
    assert fake_serial == []


def test_partial_writes_are_finished(fake_serial: list[FakeSerial]) -> None:
    link = SerialTransport("fake-port")
    device = fake_serial[0]
    device.max_write = 2
    assert link.request(10) == b"ok"
    assert b"".join(device.writes) == encode(Frame(10, 0))
    assert len(device.writes) == 3
    link.close()


def test_no_progress_write_closes_connection(fake_serial: list[FakeSerial]) -> None:
    link = SerialTransport("fake-port")
    fake_serial[0].max_write = 0
    with pytest.raises(ConnectionError, match="write"):
        link.request(10)
    assert not fake_serial[0].is_open


def test_timeout_closes_and_never_retries(
    fake_serial: list[FakeSerial], monkeypatch: pytest.MonkeyPatch
) -> None:
    link = SerialTransport("fake-port", timeout=1)
    device = fake_serial[0]
    device.reply = lambda frame: b""
    ticks = iter([i / 10 for i in range(30)])
    monkeypatch.setattr(transport.time, "monotonic", lambda: next(ticks))
    with pytest.raises(RequestTimeout, match="no retry"):
        link.request(10)
    assert device.writes == [encode(Frame(10, 0))]
    assert device.close_count == 1
    # A late reply cannot be consumed by another request on this handle.
    device.input.extend(encode(Frame(10, 0, b"late")))
    with pytest.raises(ConnectionError, match="closed"):
        link.request(10)
    assert len(device.writes) == 1


@pytest.mark.parametrize(
    "reply",
    [
        encode(Frame(20, 0)),
        encode(Frame(10, 1)),
        encode(Frame(10, 2)),
        encode(Frame(10, 0))[:-1] + b"\x00",
        encode(Frame(10, 0)) + encode(Frame(10, 0)),
        encode(Frame(10, 0)) + b"\xaa",
    ],
)
def test_ambiguous_or_invalid_replies_poison_link(
    fake_serial: list[FakeSerial], reply: bytes
) -> None:
    link = SerialTransport("fake-port")
    device = fake_serial[0]
    device.reply = lambda frame: reply
    with pytest.raises(ProtocolError):
        link.request(10)
    assert device.close_count == 1
    with pytest.raises(ConnectionError):
        link.request(10)


def test_unsolicited_data_is_not_accepted_as_fresh_reply(fake_serial: list[FakeSerial]) -> None:
    link = SerialTransport("fake-port")
    device = fake_serial[0]
    device.input.extend(encode(Frame(10, 0, b"old")))
    with pytest.raises(ProtocolError, match="unsolicited"):
        link.request(10)
    assert device.writes == []
    assert device.close_count == 1


@pytest.mark.parametrize("operation", ["write_error", "read_error"])
def test_serial_failures_are_wrapped_and_close(
    fake_serial: list[FakeSerial], operation: str
) -> None:
    link = SerialTransport("fake-port")
    device = fake_serial[0]
    setattr(device, operation, OSError("device removed"))
    with pytest.raises(ConnectionError, match="device removed"):
        link.request(10)
    assert device.close_count == 1


def test_open_failure_is_wrapped(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(**kwargs: Any) -> None:
        raise serial.SerialException("busy")

    monkeypatch.setattr(transport.serial, "Serial", fail)
    with pytest.raises(ConnectionError, match="busy"):
        SerialTransport("fake-port")


def test_interrupted_transaction_closes_handle(fake_serial: list[FakeSerial]) -> None:
    link = SerialTransport("fake-port")
    device = fake_serial[0]
    device.read_error = KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        link.request(10)
    assert device.close_count == 1
    with pytest.raises(ConnectionError):
        link.request(10)


def test_driver_write_timeout_has_timeout_semantics(fake_serial: list[FakeSerial]) -> None:
    link = SerialTransport("fake-port")
    device = fake_serial[0]
    device.write_error = serial.SerialTimeoutException("write timeout")
    with pytest.raises(RequestTimeout, match="no retry"):
        link.request(10)
    assert device.close_count == 1


def test_complete_response_after_deadline_is_rejected(
    fake_serial: list[FakeSerial], monkeypatch: pytest.MonkeyPatch
) -> None:
    link = SerialTransport("fake-port", timeout=1)
    ticks = iter([0.0, 0.01, 0.02, 2.0])
    monkeypatch.setattr(transport.time, "monotonic", lambda: next(ticks))
    with pytest.raises(RequestTimeout):
        link.request(10)
    assert fake_serial[0].close_count == 1
    assert fake_serial[0].writes == [encode(Frame(10, 0))]


def test_interrupted_initialization_closes_opened_handle(
    fake_serial: list[FakeSerial], monkeypatch: pytest.MonkeyPatch
) -> None:
    def interrupt(self: FakeSerial) -> None:
        assert self.is_open
        raise KeyboardInterrupt()

    monkeypatch.setattr(FakeSerial, "reset_input_buffer", interrupt)
    with pytest.raises(KeyboardInterrupt):
        SerialTransport("fake-port")
    assert fake_serial[0].close_count == 1


@pytest.mark.parametrize("control", [1, 3])
def test_queued_set_accepts_vendor_rw_matching_with_uint64(
    fake_serial: list[FakeSerial], control: int
) -> None:
    link = SerialTransport("fake-port", allow_writes=True)
    device = fake_serial[0]
    index = (2**64 - 1).to_bytes(8, "little")
    device.reply = lambda frame: encode(Frame(frame.command, control, index))
    assert link.request(84, b"payload", write=True, queued=True) == index
    assert device.writes == [encode(Frame(84, 3, b"payload"))]
    link.close()


def test_immediate_set_is_empty_ack(fake_serial: list[FakeSerial]) -> None:
    link = SerialTransport("fake-port", allow_writes=True)
    fake_serial[0].reply = lambda frame: encode(Frame(frame.command, 1))
    assert link.request(240, write=True) == b""
    assert fake_serial[0].writes == [encode(Frame(240, 1))]
    link.close()


def test_full_mode_parameterized_get(fake_serial: list[FakeSerial]) -> None:
    link = SerialTransport("fake-port", allow_writes=True)
    assert link.request(133, b"\x04") == b"ok"
    assert fake_serial[0].writes == [encode(Frame(133, 0, b"\x04"))]
    link.close()


@pytest.mark.parametrize("allow", [False, True])
def test_queued_read_never_sends(fake_serial: list[FakeSerial], allow: bool) -> None:
    link = SerialTransport("fake-port", allow_writes=allow)
    with pytest.raises(UnsupportedCommand) as error:
        link.request(10, queued=True)
    assert error.value.stage == "not_sent"
    assert fake_serial[0].writes == []
    link.close()


@pytest.mark.parametrize("command", [60, 62, 80, 81, 82, 83])
def test_read_only_rejects_even_allowlisted_write(
    fake_serial: list[FakeSerial], command: int
) -> None:
    link = SerialTransport("fake-port")
    with pytest.raises(UnsupportedCommand) as error:
        link.request(command, b"\x01\x01", write=True)
    assert error.value.stage == "not_sent"
    assert fake_serial[0].is_open
    assert fake_serial[0].writes == []
    link.close()


@pytest.mark.parametrize(
    "queued,control,params",
    [(True, 0, b"12345678"), (True, 3, b""), (False, 3, b""), (False, 1, b"12345678")],
)
def test_invalid_set_reply_never_claims_not_executed(
    fake_serial: list[FakeSerial], queued: bool, control: int, params: bytes
) -> None:
    link = SerialTransport("fake-port", allow_writes=True)
    fake_serial[0].reply = lambda frame: encode(Frame(frame.command, control, params))
    with pytest.raises(ProtocolError) as error:
        link.request(84, write=True, queued=queued)
    assert error.value.stage == "sent_unconfirmed"
    assert error.value.command == 84
    assert link.closed
    assert len(fake_serial[0].writes) == 1


def test_lost_set_ack_records_sent_unconfirmed(fake_serial: list[FakeSerial]) -> None:
    link = SerialTransport("fake-port", timeout=0.01, allow_writes=True)
    fake_serial[0].reply = lambda frame: b""
    with pytest.raises(RequestTimeout) as error:
        link.request(84, write=True, queued=True)
    assert error.value.stage == "sent_unconfirmed"
    assert error.value.outcome == "sent_unconfirmed"
    assert len(fake_serial[0].writes) == 1
    assert link.closed


def test_set_driver_failure_has_unknown_partial_send(fake_serial: list[FakeSerial]) -> None:
    link = SerialTransport("fake-port", allow_writes=True)
    fake_serial[0].write_error = serial.SerialTimeoutException("partial write possible")
    with pytest.raises(RequestTimeout) as error:
        link.request(84, write=True)
    assert error.value.stage == "unknown"
    assert link.closed


def test_stale_data_prevents_set_before_send(fake_serial: list[FakeSerial]) -> None:
    link = SerialTransport("fake-port", allow_writes=True)
    fake_serial[0].input.extend(b"old")
    with pytest.raises(ProtocolError) as error:
        link.request(84, write=True)
    assert error.value.stage == "not_sent"
    assert fake_serial[0].writes == []


def test_set_interrupt_preserves_exception_and_outcome(fake_serial: list[FakeSerial]) -> None:
    link = SerialTransport("fake-port", allow_writes=True)
    fake_serial[0].read_error = KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt) as error:
        link.request(84, write=True)
    assert error.value.__dict__["stage"] == "sent_unconfirmed"
    assert link.closed


def test_transport_lock_timeout_is_bounded_and_closes(fake_serial: list[FakeSerial]) -> None:
    link = SerialTransport("fake-port", timeout=0.01, allow_writes=True)
    assert link._lock.acquire(timeout=0.01)
    try:
        with pytest.raises(RequestTimeout) as error:
            link.request(84, write=True)
        assert error.value.stage == "not_sent"
        assert link.closed
        assert fake_serial[0].writes == []
    finally:
        link._lock.release()


def test_invalid_set_payload_has_not_sent_stage(fake_serial: list[FakeSerial]) -> None:
    link = SerialTransport("fake-port", allow_writes=True)
    with pytest.raises(ValueError) as error:
        link.request(84, b"x" * 254, write=True)
    assert error.value.__dict__["stage"] == "not_sent"
    assert fake_serial[0].writes == [] and not link.closed
    link.close()
