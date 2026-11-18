"""ASCII protocol tests use a scripted byte stream; no serial port is opened."""

import threading
from collections import deque

import pytest

from dobot_usb import Printer
from dobot_usb.printing import PrinterError, PrintResult, Temperature

INFO = b"FIRMWARE_NAME:Marlin V1 PROTOCOL_VERSION:1.0 MACHINE_TYPE:Generic\nCap:EEPROM:0\nok\n"
TEMP = b"ok T:199.25 /200.0 B:0.0 /0.0 @:0\n"


class ScriptedSerial:
    def __init__(self):
        self.replies = deque([(b"M115", INFO), (b"M105", TEMP)])
        self.buffer = bytearray()
        self.written = []
        self.closed = True
        self.opened = []
        self.chunk = 4095
        self.hook = None

    def open(self):
        self.closed = False
        self.opened.append((self.port, self.dtr, self.rts))

    def close(self):
        self.closed = True

    def reset_input_buffer(self):
        self.buffer.clear()

    @property
    def in_waiting(self):
        return len(self.buffer)

    def read(self, count):
        if self.closed:
            raise OSError("closed")
        piece = bytes(self.buffer[:count])
        del self.buffer[:count]
        return piece

    def read_until(self, delimiter, size):
        newline = self.buffer.find(delimiter)
        length = len(self.buffer) if newline < 0 else newline + 1
        return self.read(min(self.chunk, size, length))

    def write(self, packet):
        assert not self.closed
        command, end = packet[:-1], packet[-1:]
        assert end == b"\n"
        self.written.append(command)
        expected, reply = self.replies.popleft()
        assert command == expected
        if self.hook:
            self.hook(command)
        if isinstance(reply, BaseException):
            raise reply
        if isinstance(reply, int):
            return reply
        self.buffer.extend(reply)
        return len(packet)


@pytest.fixture
def wire(monkeypatch):
    script = ScriptedSerial()

    def construct(**kwargs):
        assert kwargs["port"] is None and kwargs["baudrate"] == 115200
        return script

    monkeypatch.setattr("dobot_usb.printer.serial.Serial", construct)
    return script


def test_cura_display_motor_release_and_slic3r_compact_move(wire, tmp_path):
    source = tmp_path / "stock-forms.gcode"
    source.write_text("G1E-3\nM117 Printing...\nG1 Z+0.5 E-5 X-20 Y-20 F1800\nM84\nG90\n")
    commands = [b"G1 E-3", b"M117 Printing...", b"G1 Z+0.5 E-5 X-20 Y-20 F1800", b"M84", b"G90"]
    wire.replies.extend((cmd, b"ok\n") for cmd in [b"M400", *commands, b"M400"])
    with Printer.connect("selected") as printer:
        assert printer.print_file(source).acknowledged_commands == len(commands)


def test_handshake_and_temperature_do_not_change_machine(wire):
    wire.chunk = 4
    wire.replies.append((b"M105", b"T0:21.75 B:19.0\nok\n"))
    with Printer.connect("COM7") as printer:
        assert printer.firmware == "Marlin V1"
        assert printer.temp() == Temperature(21.75, None)
        assert wire.opened == [("COM7", False, False)]
        assert wire.written == [b"M115", b"M105", b"M105"]
    assert wire.closed


@pytest.mark.parametrize(
    "reply",
    [
        b"ok\n",
        b"FIRMWARE_NAME:Other SOURCE_CODE_URL:Marlin PROTOCOL_VERSION:1.0\nok\n",
        b"FIRMWARE_NAME:Marlin V1 PROTOCOL_VERSION:2.0\nok\n",
        b"Resend: 1\nok\n",
        b"Error: bad\nok\n",
        b"\xaa\xaa\n",
    ],
)
def test_reject_incompatible_firmware_before_any_control(wire, reply):
    wire.replies[0] = (b"M115", reply)
    with pytest.raises(PrinterError):
        Printer.connect("selected")
    assert wire.written == [b"M115"] and wire.closed


def test_reject_firmware_without_valid_temperature(wire):
    wire.replies[1] = (b"M105", b"ok\n")
    with pytest.raises(PrinterError, match="no valid hotend"):
        Printer.connect("selected")
    assert wire.written == [b"M115", b"M105"] and wire.closed


def test_print_completed_via_confirmed_barrier_and_uses_prepared_bytes(wire, tmp_path):
    file = tmp_path / "part.gcode"
    file.write_bytes(
        b"; label\nM106\nm109s200\nG01x2.5e.3 (move)\nM104 S0\nG91\nG1 Z+3 E-1 F300\nG90\nM107\n"
    )
    lines = [
        b"M106",
        b"M109 S200",
        b"G1 X2.5 E.3",
        b"M104 S0",
        b"G91",
        b"G1 Z+3 E-1 F300",
        b"G90",
        b"M107",
    ]
    for cmd in [b"M400", *lines, b"M400"]:
        reply = b"echo:busy: processing\nT:150 /200\nok\n" if cmd.startswith(b"M109") else b"ok\n"
        wire.replies.append((cmd, reply))

    def change_after_preflight(command):
        if command == b"M400":
            file.write_bytes(b"M999\n")

    with Printer.connect("selected") as printer:
        wire.hook = change_after_preflight
        result = printer.print(file)
        assert result == PrintResult(file.resolve(), len(lines))
        assert wire.written == [b"M115", b"M105", b"M400", *lines, b"M400"]


@pytest.mark.parametrize(
    "bad",
    [
        b"G28",
        b"M104 S251",
        b"M109 S180 R180",
        b"M104 S200 T1",
        b"M140 S55",
        b"M106 S256",
        b"M84 X1",
        b"M117 text:M112",
        b"M117 G28",
        b"G1 X1 M112",
        b"G1 X1:X2",
        b"M104 S20 S200",
        b"G1 X1\rM112",
        b"N5 G1 X3*34",
        b"G1 X3*34",
        b";@pause",
        b"G1 X1.0.1",
        b"G1 F-2",
        b"G1 X" + b"0" * 80 + b"1",
    ],
)
def test_invalid_tail_fails_before_any_print_command(wire, tmp_path, bad):
    source = tmp_path / "bad.gcode"
    source.write_bytes(b"M104 S200\nG1 X1\n" + bad + b"\n")
    with Printer.connect("selected") as printer:
        with pytest.raises(ValueError, match="line 3"):
            printer.print_file(source)
        assert wire.written == [b"M115", b"M105"]
        assert not wire.closed


@pytest.mark.parametrize(
    ("reply", "message"),
    [
        (b"echo:Unknown command: G1\nok\n", "unexpected"),
        (b"Resend: 7\nok\n", "Resend"),
        (b"ok\nok\n", "unmatched"),
        (b"start\n", "unexpected"),
        (b"Error:thermal fault\nok\n", "unexpected"),
        (b"ok T:20 /0\n", "unexpected"),
        (b"\n", "timed out"),
        (1, "incomplete"),
        (OSError("unplugged"), "serial exchange failed"),
    ],
)
def test_unknown_outcome_never_replays_or_sends_next_line(wire, tmp_path, reply, message):
    source = tmp_path / "broken.gcode"
    source.write_bytes(b"G90\nG1 X1\nG1 X2\n")
    wire.replies.extend([(b"M400", b"ok\n"), (b"G90", b"ok\n"), (b"G1 X1", reply)])
    with Printer.connect("selected", timeout=0.01) as printer:
        with pytest.raises(PrinterError, match=message) as err:
            printer.print_file(source)
        assert "line 2 after 1 acknowledged" in str(err.value)
        assert wire.closed
        with pytest.raises(PrinterError, match="closed"):
            printer.temperature()
    assert wire.written == [b"M115", b"M105", b"M400", b"G90", b"G1 X1"]


@pytest.mark.parametrize("at_end", [False, True])
def test_unsupported_planner_sync_never_reports_completion(wire, tmp_path, at_end):
    source = tmp_path / "job.gcode"
    source.write_bytes(b"G1 X2\n")
    if at_end:
        wire.replies.extend([(b"M400", b"ok\n"), (b"G1 X2", b"ok\n")])
    wire.replies.append((b"M400", b'echo:Unknown command: "M400"\nok\n'))
    with Printer.connect("selected") as printer:
        with pytest.raises(PrinterError, match=("final" if at_end else "initial") + " M400"):
            printer.print_file(source)
        assert wire.closed
    assert (b"G1 X2" in wire.written) == at_end


def test_concurrent_requests_cannot_interleave_active_job_and_close_ends_stream(wire, tmp_path):
    file = tmp_path / "file.gcode"
    file.write_text("G1 X1\nG1 X2\n")
    wire.replies.extend([(b"M400", b"ok\n"), (b"G1 X1", b"ok\n")])
    started = threading.Event()
    release = threading.Event()
    errors = []

    def hook(command):
        if command == b"G1 X1":
            started.set()
            assert release.wait(2)

    with Printer.connect("selected") as printer:
        wire.hook = hook

        def print_job():
            try:
                printer.print_file(file)
            except PrinterError as exc:
                errors.append(exc)

        worker = threading.Thread(target=print_job)
        worker.start()
        try:
            assert started.wait(2)
            with pytest.raises(PrinterError, match="another operation"):
                printer.temperature()
            with pytest.raises(PrinterError, match="another operation"):
                printer.print_file(file)
            printer.close()
        finally:
            release.set()
            worker.join(2)
        assert len(errors) == 1 and not worker.is_alive()
        assert wire.written == [b"M115", b"M105", b"M400", b"G1 X1"]
