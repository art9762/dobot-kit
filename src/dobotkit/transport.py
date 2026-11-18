"""Explicit-port serial transport; read-only unless deliberately opted in."""

import math
import os
import threading
import time
from typing import Protocol

import serial

from .errors import (
    ConnectionError,
    DobotError,
    OutcomeStage,
    ProtocolError,
    RequestTimeout,
    UnsupportedCommand,
)
from .protocol import Frame, FrameParser, encode

BOOTSTRAP_QUERIES: frozenset[int] = frozenset({0, 1, 2, 10, 20, 61, 62, 63, 246})
# Device ID, end-effector offsets and PTP joint, Cartesian, jump and common parameters.
READ_ONLY_COMMANDS: frozenset[int] = BOOTSTRAP_QUERIES | {5, 60, 80, 81, 82, 83}


class Transport(Protocol):
    """Bounded, single-attempt exchanges of response parameters.

    Implementations must close on failed exchanges and never replay writes.
    Command/parameter schemas belong to the backend, not this framing layer.
    """

    def request(
        self, command: int, params: bytes = b"", *, write: bool = False, queued: bool = False
    ) -> bytes: ...

    def close(self) -> None: ...


class SerialTransport:
    """One explicit port, with a deadline including contention and all IO.

    ``allow_writes=False`` permits only parameter-free diagnostic GETs.
    Opt-in enables uint8 IDs and parameterized GETs; the backend
    must validate their schemas. Opening/closing sends no protocol command.
    """

    def __init__(self, port: str, timeout: float = 1.0, *, allow_writes: bool = False) -> None:
        if not isinstance(port, str) or not port.strip():
            raise ValueError("port must be an explicit, nonempty serial port name")
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout)
            or timeout <= 0
        ):
            raise ValueError("timeout must be finite and greater than zero")
        if type(allow_writes) is not bool:
            raise TypeError("allow_writes must be bool")
        self._timeout = float(timeout)
        self._allow_writes = allow_writes
        self._lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._serial: serial.Serial | None = None
        options = {"exclusive": True} if os.name == "posix" else {}
        device: serial.Serial | None = None
        try:
            device = serial.Serial(
                port=None,
                baudrate=115200,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=min(self._timeout, 0.05),
                write_timeout=self._timeout,
                rtscts=False,
                dsrdtr=False,
                xonxoff=False,
                **options,
            )
            device.dtr = False
            device.rts = False
            device.port = port
            device.open()
            device.reset_input_buffer()
            self._serial = device
        except (OSError, serial.SerialException) as exc:
            if device is not None:
                self._close_device(device)
            raise ConnectionError(f"could not open serial port {port!r}: {exc}") from exc
        except BaseException:
            if device is not None:
                self._close_device(device)
            raise

    @staticmethod
    def _close_device(device: serial.Serial) -> None:
        try:
            device.close()
        except (OSError, serial.SerialException):
            pass

    @property
    def closed(self) -> bool:
        with self._state_lock:
            return self._serial is None

    def close(self) -> None:
        """Discard the handle, including during IO; no queue/physical action."""
        with self._state_lock:
            device, self._serial = self._serial, None
        if device is not None:
            self._close_device(device)

    def _packet(self, command: int, params: bytes, write: bool, queued: bool) -> bytes:
        if type(command) is not int or not 0 <= command <= 255:
            raise UnsupportedCommand("command must be a uint8 integer", stage="not_sent")
        if type(write) is not bool or type(queued) is not bool:
            raise TypeError("write and queued must be bool")
        if queued and not write:
            raise UnsupportedCommand("queued reads are invalid", stage="not_sent", command=command)
        if not isinstance(params, bytes):
            raise TypeError("params must be bytes")
        if not self._allow_writes and (write or command not in READ_ONLY_COMMANDS or params):
            raise UnsupportedCommand(
                "only allowlisted parameter-free diagnostic queries are enabled",
                stage="not_sent",
                command=command,
            )
        return encode(Frame(command, int(write) | (int(queued) << 1), params))

    def request(
        self, command: int, params: bytes = b"", *, write: bool = False, queued: bool = False
    ) -> bytes:
        """Exchange exactly once. SET errors retain their sending stage."""
        try:
            packet = self._packet(command, params, write, queued)
        except (TypeError, ValueError) as exc:
            exc.__dict__.update(stage="not_sent", command=command)
            raise
        deadline = time.monotonic() + self._timeout
        acquired = False
        attempted = False
        sent = 0
        try:
            acquired = self._lock.acquire(timeout=max(0.0, deadline - time.monotonic()))
            if not acquired:
                raise RequestTimeout("request lock timed out; no retry was sent")
            with self._state_lock:
                device = self._serial
            if device is None or not device.is_open:
                raise ConnectionError("serial connection is closed; open a new transport")
            parser = FrameParser()
            if device.in_waiting:
                raise ProtocolError("unsolicited serial data; reopen the connection")
            while sent < len(packet):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RequestTimeout(f"request {command} timed out; no retry was sent")
                device.write_timeout = remaining
                attempted = True
                count = device.write(packet[sent:])
                if count is None or count <= 0 or count > len(packet) - sent:
                    raise ConnectionError("serial write did not complete")
                sent += count
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RequestTimeout(f"request {command} timed out; no retry was sent")
                device.timeout = min(remaining, 0.05)
                frames = parser.feed(device.read(min(device.in_waiting or 1, 1024)))
                if not frames:
                    continue
                if len(frames) != 1 or parser.buffered_bytes:
                    raise ProtocolError("multiple or trailing response frames")
                frame = frames[0]
                # Vendor decoder uses c_rw, not a queued-bit echo. Queued SET
                # firmware may return control 1 or 3; the uint64 is mandatory.
                controls = {1, 3} if queued else {int(write)}
                if frame.command != command or frame.control not in controls:
                    raise ProtocolError("response does not match the current request")
                if write and len(frame.params) != (8 if queued else 0):
                    raise ProtocolError(
                        "SET response must be a uint64 index or empty immediate ACK"
                    )
                if time.monotonic() > deadline:
                    raise RequestTimeout(f"request {command} timed out; no retry was sent")
                if self.closed:
                    raise ConnectionError("serial connection closed during request")
                return frame.params
        except BaseException as exc:
            self.close()
            stage: OutcomeStage = (
                "sent_unconfirmed"
                if sent == len(packet)
                else "unknown"
                if attempted
                else "not_sent"
            )
            if isinstance(exc, serial.SerialTimeoutException):
                raise RequestTimeout(
                    f"request {command} timed out; no retry was sent", stage=stage, command=command
                ) from exc
            if isinstance(exc, (OSError, serial.SerialException)):
                raise ConnectionError(
                    f"serial transaction failed: {exc}", stage=stage, command=command
                ) from exc
            if isinstance(exc, DobotError):
                exc.stage = stage
                exc.command = command
            elif write:
                # Preserve interrupts while exposing that a SET may be in flight.
                exc.__dict__.update(stage=stage, command=command)
            raise
        finally:
            if acquired:
                self._lock.release()
