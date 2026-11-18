"""Synchronous ASCII host for a compatible Magician 3D Printer Firmware session."""

import math
import os
import re
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryFile
from types import TracebackType
from typing import Self

import serial

from .errors import DobotError
from .printer_gcode import printer_commands
from .validation import options

_IDENTITY = re.compile(r"(?:^|\s)FIRMWARE_NAME:\s*(.+?)(?=\s+[A-Z_0-9]+:|$)", re.IGNORECASE)
_FAMILY = re.compile(
    r"^(?:Marlin|Sprinter|Repetier(?:Host)?|Dobot|Magician)(?:\b|_)", re.IGNORECASE
)
_PROTOCOL = re.compile(r"(?:^|\s)PROTOCOL_VERSION:1(?:\.0)?(?:\s|$)", re.IGNORECASE)
_ACK = re.compile(r"ok(?: [NPB][0-9]+)*", re.IGNORECASE)
_TEMP = re.compile(r"(?:^|\s)T(?:0)?:\s*(-?\d+(?:\.\d+)?)(?:\s*/\s*(-?\d+(?:\.\d+)?))?(?=\s|$)")


class PrinterError(DobotError):
    """Printer communication or job execution could not be confirmed."""


@dataclass(frozen=True)
class Temperature:
    current_c: float
    target_c: float | None


@dataclass(frozen=True)
class PrintResult:
    """File acknowledgements after the firmware also acknowledged the final M400."""

    path: Path
    acknowledged_commands: int


class Printer:
    """Explicit serial session for print firmware. Construct or call connect() to query it."""

    def __init__(self, port: str, *, timeout: float = 5.0) -> None:
        if not isinstance(port, str) or not port.strip():
            raise ValueError("provide an explicit printer serial port")
        options(True, timeout)
        self._timeout = timeout
        self._operation = threading.Lock()
        self._closed = threading.Event()
        self._serial = serial.Serial(
            port=None,
            baudrate=115200,
            timeout=timeout,
            write_timeout=timeout,
            exclusive=True if os.name == "posix" else None,
        )
        try:
            self._serial.dtr = False
            self._serial.rts = False
            self._serial.port = port
            self._serial.open()
            self._serial.reset_input_buffer()
            info = "\n".join(self._exchange(b"M115", timeout))
            match = _IDENTITY.search(info)
            if not match or not _FAMILY.match(match[1]) or not _PROTOCOL.search(info):
                raise PrinterError("M115 did not identify a supported RepRap ASCII firmware")
            self.firmware = match[1]
            self.temperature()
        except BaseException:
            self.close()
            raise

    @classmethod
    def connect(cls, port: str, *, timeout: float = 5.0) -> Self:
        """Open 115200 baud and require compatible M115 identity and M105 temperature."""
        return cls(port, timeout=timeout)

    def _check_open(self) -> None:
        if self._closed.is_set():
            raise PrinterError("printer session is closed")

    @contextmanager
    def _exclusive(self) -> Iterator[None]:
        if not self._operation.acquire(blocking=False):
            raise PrinterError("another operation is using the printer", stage="not_sent")
        try:
            self._check_open()
            yield
        finally:
            self._operation.release()

    def _check_idle_input(self) -> None:
        count = self._serial.in_waiting
        if not count:
            return
        if count > 4096:
            raise PrinterError("too much unsolicited data from printer")
        data = self._serial.read(count)
        if not data.endswith(b"\n") or any(
            line.strip() and line.strip() != b"wait" and not re.match(rb"^T(?:0)?:", line)
            for line in data.splitlines()
        ):
            raise PrinterError("unmatched printer response; open a fresh printer session")

    def _exchange(self, command: bytes, timeout: float) -> list[str]:
        """Send one unnumbered line exactly once. Any ambiguity closes the session."""
        self._check_open()
        deadline = time.monotonic() + timeout
        device = self._serial
        try:
            self._check_idle_input()
            packet = command + b"\n"
            device.write_timeout = timeout
            if device.write(packet) != len(packet):
                raise PrinterError("incomplete printer write; command outcome unknown")
            reports: list[str] = []
            line = bytearray()
            while True:
                self._check_open()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise PrinterError("printer acknowledgement timed out; command outcome unknown")
                device.timeout = min(remaining, 0.1)
                line.extend(device.read_until(b"\n", 4096 - len(line)))
                if len(line) >= 4096:
                    raise PrinterError("printer reply line exceeds 4095 bytes")
                if not line.endswith(b"\n"):
                    continue
                text = line.decode("ascii").strip()
                line.clear()
                if not text:
                    continue
                if time.monotonic() >= deadline:
                    raise PrinterError("printer acknowledgement arrived after deadline")
                inline_ack = text.lower().startswith("ok ")
                report = text[3:].strip() if inline_ack else text
                if (command == b"M115" and report.startswith("FIRMWARE_NAME:")) or (
                    command == b"M105" and _TEMP.search(report) and re.match(r"^T(?:0)?:", report)
                ):
                    reports.append(report)
                    if len(reports) > 16:
                        raise PrinterError("too many printer report lines before acknowledgement")
                    if not inline_ack:
                        continue
                elif not _ACK.fullmatch(text):
                    if not inline_ack and (
                        text in {"wait", "busy: processing", "echo:busy: processing"}
                        or (command == b"M115" and text.startswith("Cap:"))
                        or re.match(r"^T(?:0)?:", text)
                    ):
                        continue
                    raise PrinterError(f"unexpected printer response: {text[:250]}")
                self._check_idle_input()
                self._check_open()
                return reports
        except (OSError, UnicodeError) as exc:
            self.close()
            raise PrinterError(f"printer serial exchange failed: {exc}") from exc
        except BaseException:
            self.close()
            raise

    def temperature(self) -> Temperature:
        """Read the first hotend's current and optional target temperatures in Celsius."""
        with self._exclusive():
            for report in self._exchange(b"M105", self._timeout):
                if match := _TEMP.search(report):
                    current = float(match[1])
                    target = None if match[2] is None else float(match[2])
                    if math.isfinite(current) and (target is None or math.isfinite(target)):
                        return Temperature(current, target)
            self.close()
            raise PrinterError("M105 had no valid hotend temperature")

    def print_file(self, path: str | Path, *, wait_timeout: float = 300.0) -> PrintResult:
        """Validate the entire slice, send one line per ACK, then wait for the motion planner."""
        options(True, wait_timeout)
        source = Path(path).expanduser().resolve(strict=True)
        with self._exclusive(), TemporaryFile(mode="w+b") as prepared:
            for number, wire in printer_commands(source):
                prepared.write(str(number).encode("ascii") + b" " + wire + b"\n")
            prepared.seek(0)
            accepted = 0
            where = "initial M400 planner check"
            try:
                self._exchange(b"M400", wait_timeout)
                for record in prepared:
                    line, _, command = record.rstrip(b"\n").partition(b" ")
                    where = f"file line {line.decode('ascii')}"
                    opcode = command.split(b" ", 1)[0]
                    long_wait = opcode in {b"G4", b"M109", b"M190", b"M400"}
                    self._exchange(command, wait_timeout if long_wait else self._timeout)
                    accepted += 1
                where = "final M400 planner check"
                self._exchange(b"M400", wait_timeout)
                return PrintResult(source, accepted)
            except PrinterError as exc:
                self.close()
                raise PrinterError(
                    f"print interrupted at {where} after {accepted} acknowledged "
                    f"file commands: {exc}"
                ) from exc
            except BaseException:
                self.close()
                raise

    def close(self) -> None:
        """End host transmission. Accepted movement and heater targets can remain active."""
        self._closed.set()
        self._serial.close()

    def __enter__(self) -> Self:
        self._check_open()
        return self

    def __exit__(
        self,
        typ: type[BaseException] | None,
        value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()
