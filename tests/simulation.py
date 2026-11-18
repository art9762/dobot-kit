"""Deterministic protocol state machine; no IO, kinematics or physical timing."""

import math
import struct
import threading
from collections import deque

from dobot_usb.errors import ConnectionError, UnsupportedCommand
from dobot_usb.protocol import Frame
from dobot_usb.transport import READ_ONLY_COMMANDS

# Explicit SET wire layouts. Never infer these from GET sizes: several differ.
_SET_FORMATS = {
    3: "BB",
    11: "Bff",
    20: "",
    30: "4f",
    31: "I",
    32: "Bf",
    40: "B",
    41: "B",
    60: "3f",
    61: "BB",
    62: "BB",
    63: "BB",
    70: "8f",
    71: "8f",
    72: "2f",
    73: "BB",
    74: "2f",
    80: "8f",
    81: "4f",
    82: "2f",
    83: "2f",
    84: "B4f",
    85: "2f",
    86: "B5f",
    87: "3f",
    90: "3fB",
    91: "B4f",
    92: "B4f",
    100: "4f",
    101: "8f",
    110: "I",
    120: "BBBH",
    130: "BB",
    131: "BB",
    132: "B2f",
    135: "BBi",
    137: "BBB",
    138: "BBB",
    140: "2f",
    150: "B",
    153: "B4B",
    154: "4B",
    155: "4B",
    156: "4B",
    170: "f",
    171: "",
    240: "",
    241: "",
    242: "",
    244: "",
    245: "",
}
_TEXT_COMMANDS = frozenset({0, 1, 151, 152})
_QUEUEABLE = frozenset(
    {
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
)
_GET_COMMANDS = frozenset(
    {
        0,
        1,
        2,
        3,
        4,
        5,
        10,
        13,
        20,
        30,
        32,
        40,
        41,
        42,
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
        130,
        131,
        132,
        133,
        134,
        137,
        138,
        140,
        150,
        151,
        152,
        153,
        154,
        155,
        156,
        157,
        246,
    }
)
_ADDRESS_GETS = frozenset({130, 131, 132, 133, 134})
_UINT64_MAX = 2**64 - 1


def _unpack(format: str, data: bytes) -> tuple[int | float, ...]:
    if len(data) != struct.calcsize("<" + format):
        raise ValueError("invalid simulated request parameter length")
    values = struct.unpack("<" + format, data)
    if any(isinstance(value, float) and not math.isfinite(value) for value in values):
        raise ValueError("request floats must be finite")
    return values


def _range(value: int | float, low: int | float, high: int | float) -> None:
    if not low <= value <= high:
        raise ValueError("simulated request parameter outside command range")


def _validate_set(command: int, params: bytes) -> None:
    """Reject malformed raw requests before recording or accepting any action."""
    if command in _TEXT_COMMANDS:
        if not params.endswith(b"\0") or b"\0" in params[:-1]:
            raise ValueError("SET text must have exactly one trailing NUL")
        try:
            params[:-1].decode("latin-1" if command == 0 else "utf-8")
        except UnicodeError:
            raise ValueError("invalid SET text encoding") from None
        return
    if command == 88:
        if len(params) < 18 or len(params) != 18 + params[17] * 4 or params[17] > 58:
            raise ValueError("PTPPO count does not match packet length")
        _validate_set(84, params[:17])
        for offset in range(18, len(params), 4):
            ratio, address, level = _unpack("BHB", params[offset : offset + 4])
            _range(ratio, 0, 100)
            _range(address, 1, 20)
            _range(level, 0, 1)
        return
    if command not in _SET_FORMATS:
        raise UnsupportedCommand("SET command is unavailable in the classic simulator")
    values = _unpack(_SET_FORMATS[command], params)
    if command in {3, 11, 32, 40, 41, 61, 62, 63, 73, 137, 138, 150, 153}:
        _range(values[0], 0, 1)
    if command in {3, 61, 62, 63}:
        _range(values[1], 0, 1)
    if command in {70, 71, 74, 80, 81, 85, 87, 90, 100, 170}:
        # The last Jump2 member is signed z limit; CP ends in a bool.
        positive = values[:-1] if command in {87, 90} else values
        for value in positive:
            _range(value, 0, float("inf"))
    if command in {72, 83}:
        for value in values:
            _range(value, 0, 100)
    if command == 82:
        _range(values[0], 0, float("inf"))
    if command == 31:
        _range(values[0], 0, 0)
    if command == 32:
        _range(values[1], 0, float("inf"))
    if command == 73:
        _range(values[1], 0, 8)
    if command in {84, 86}:
        _range(values[0], 0, 9)
    if command == 90:
        _range(values[-1], 0, 1)
    if command in {91, 92}:
        _range(values[0], 0, 1)
        _range(values[4], 0, 100)  # Fourth float is power, never velocity.
    if command in {130, 131, 132, 120}:
        _range(values[0], 1, 20)
    if command == 120:
        _range(values[1], 0, 1)
        _range(values[2], 0, 1 if values[1] == 0 else 3)
        _range(values[3], 0, 1 if values[1] == 0 else 4095)
    if command == 130:
        _range(values[1], 0, 4)
    if command == 131:
        _range(values[1], 0, 1)
    if command == 132:
        _range(values[1], 10, 1_000_000)
        _range(values[2], 0, 100)
    if command == 135:
        _range(values[0], 0, 1)
        _range(values[1], 0, 1)
    if command in {137, 138}:
        _range(values[2], 0, 1)


class SimulatedTransport:
    """Offline controller model with explicit acceptance and execution.

    Construction sends nothing and starts paused. ``auto_complete`` advances
    one pending action on each running queue-index GET. ``advance`` is a manual
    alternative, also respecting pause. GET fixtures in ``responses`` may be
    replaced, including with malformed replies for decoder fault tests.
    """

    def __init__(self, allow_writes: bool = True, auto_complete: bool = True) -> None:
        if type(allow_writes) is not bool or type(auto_complete) is not bool:
            raise TypeError("allow_writes and auto_complete must be bool")
        self.closed = False
        self.requests: list[int] = []
        self.transactions: list[Frame] = []
        self.running = False
        self._allow_writes = allow_writes
        self._auto_complete = auto_complete
        self._lock = threading.RLock()
        self._last_index = 0
        self._pending: deque[tuple[int, Frame]] = deque()
        self._io: dict[tuple[int, int], bytes] = {}
        self.responses: dict[int, bytes] = {
            0: b"SIMULATED\0",
            1: b"Dobot Kit offline fixture\0",
            2: bytes([4, 0, 3]),
            3: b"\0",
            4: bytes(4),
            5: struct.pack("<3I", 1, 2, 3),
            10: struct.pack("<8f", 200, 0, 50, 0, 0, 30, 40, 0),
            13: bytes(4),
            20: bytes(16),
            30: struct.pack("<4f", 200, 0, 50, 0),
            32: bytes(4),
            40: b"\0",
            41: b"\0",
            42: b"\0",
            60: bytes(12),
            61: bytes(2),
            62: bytes(2),
            63: bytes(2),
            70: struct.pack("<8f", *([50] * 8)),
            71: struct.pack("<8f", *([50] * 8)),
            72: struct.pack("<2f", 100, 100),
            74: struct.pack("<2f", 50, 50),
            80: struct.pack("<8f", *([50] * 8)),
            81: struct.pack("<4f", 50, 50, 50, 50),
            82: struct.pack("<2f", 20, 100),
            83: struct.pack("<2f", 100, 100),
            85: struct.pack("<2f", 50, 50),
            87: struct.pack("<3f", 20, 20, 100),
            90: struct.pack("<3fB", 50, 50, 50, 0),
            100: struct.pack("<4f", 50, 50, 50, 50),
            130: b"\x01\0",
            131: b"\x01\0",
            132: struct.pack("<B2f", 1, 1000, 0),
            133: b"\x01\0",
            134: b"\x01\0\0",
            137: bytes(3),
            138: b"\0",
            140: bytes(8),
            150: b"\0",
            151: b"\0",
            152: b"\0",
            153: b"\x01\0\0\0\0",
            154: bytes(4),
            155: bytes(4),
            156: bytes(4),
            157: b"\0",
            246: bytes(8),
        }

    def request(
        self, command: int, params: bytes = b"", *, write: bool = False, queued: bool = False
    ) -> bytes:
        """Validate, record once and accept a command; never open a serial port."""
        if type(command) is not int or not 0 <= command <= 255:
            raise UnsupportedCommand("command must be a uint8 integer")
        if type(write) is not bool or type(queued) is not bool:
            raise TypeError("write and queued must be bool")
        if not isinstance(params, bytes):
            raise TypeError("params must be bytes")
        if queued and not write:
            raise UnsupportedCommand("queued reads are invalid")
        if not self._allow_writes and (write or command not in READ_ONLY_COMMANDS or params):
            raise UnsupportedCommand(
                "only allowlisted parameter-free diagnostic queries are enabled",
                stage="not_sent",
                command=command,
            )
        frame = Frame(command, int(write) | (int(queued) << 1), params)
        if write:
            _validate_set(command, params)
            if queued and command not in _QUEUEABLE:
                raise UnsupportedCommand("command cannot be queued")
        else:
            if command not in _GET_COMMANDS:
                raise UnsupportedCommand("GET command is unavailable in the classic simulator")
            if command in _ADDRESS_GETS or command == 138:
                address = _unpack("B", params)[0]
                _range(address, 1 if command in _ADDRESS_GETS else 0, 20 if command != 138 else 255)
            elif params:
                raise ValueError("GET command takes no parameters")
        with self._lock:
            self._check_open()
            if write and queued:
                if self._last_index == _UINT64_MAX:
                    raise ValueError("simulated queue uint64 index exhausted")
                self._last_index += 1
                self._pending.append((self._last_index, frame))
                result = struct.pack("<Q", self._last_index)
            elif write:
                self._apply(frame)
                result = b""
            else:
                if command == 246 and self._auto_complete:
                    self.advance()
                result = self._read(command, params)
            self.requests.append(command)
            # Passwords remain available only via the explicit GET fixture.
            self.transactions.append(
                Frame(command, frame.control, b"<redacted>") if command == 152 and write else frame
            )
            return result

    def _check_open(self) -> None:
        if self.closed:
            raise ConnectionError("Connection is closed", stage="not_sent")

    def _read(self, command: int, params: bytes) -> bytes:
        if command in _ADDRESS_GETS:
            address = params[0]
            return self._io.get((command, address), params + self.responses[command][1:])
        return self.responses[command]

    def advance(self, count: int = 1) -> int:
        """Execute at most count FIFO actions while running; return executed count.

        No clock, sensor contact, dwell, path interpolation or motor behavior is
        modeled. A paused queue never advances, even through this manual hook.
        """
        if type(count) is not int or count < 0:
            raise ValueError("count must be a nonnegative integer")
        with self._lock:
            self._check_open()
            completed = 0
            while self.running and self._pending and completed < count:
                index, frame = self._pending[0]
                self._apply(frame)
                self._pending.popleft()
                self.responses[246] = struct.pack("<Q", index)
                completed += 1
            return completed

    def _apply(self, frame: Frame) -> None:
        command, params = frame.command, frame.params
        if command == 240:
            self.running = True
        elif command in {241, 242}:
            self.running = False
        elif command == 245:
            # A clear never looks like completion of discarded commands.
            self._pending.clear()
        elif command == 20:
            self.responses[20] = bytes(16)
        elif command == 3:
            self.responses[3] = params[:1]
        elif command == 11:
            pose = list(_unpack("8f", self.responses[10]))
            _, rear, front = _unpack("Bff", params)
            pose[5:7] = [rear, front]
            self.responses[10] = struct.pack("<8f", *pose)
        elif command == 31:
            self._pose_update(self.responses[30])
        elif command in {84, 86, 88}:
            self._ptp(params[:17], params[17:21] if command == 86 else None)
            if command == 88:
                for offset in range(18, len(params), 4):
                    _, address, level = _unpack("BHB", params[offset : offset + 4])
                    packet = bytes((int(address), int(level)))
                    self._io[(131, int(address))] = packet
        elif command in {91, 92}:
            mode, x, y, z, _power = _unpack("B4f", params)
            pose = list(_unpack("8f", self.responses[10]))
            pose[:3] = [x, y, z] if mode == 1 else [pose[0] + x, pose[1] + y, pose[2] + z]
            self.responses[10] = self._pack_pose(pose)
        elif command == 101:
            self._pose_update(params[16:])
        elif command in {130, 131, 132}:
            self._io[(command, params[0])] = params
        elif command in {32, 73, 110, 120, 135, 137, 138, 170, 171, 244}:
            # Recorded, without invented sensor readings or elapsed time.
            pass
        else:
            self.responses[command] = params

    @staticmethod
    def _pack_pose(pose: list[int | float]) -> bytes:
        try:
            result = struct.pack("<8f", *pose)
        except (OverflowError, struct.error):
            raise ValueError("simulated target does not fit finite float32") from None
        _unpack("8f", result)
        return result

    def _pose_update(self, position: bytes) -> None:
        xyzr = _unpack("4f", position)
        pose = list(_unpack("8f", self.responses[10]))
        pose[:4] = xyzr
        self.responses[10] = self._pack_pose(pose)

    def _ptp(self, params: bytes, rail: bytes | None) -> None:
        mode, *target = _unpack("B4f", params)
        pose = list(_unpack("8f", self.responses[10]))
        if mode in {0, 1, 2, 9}:
            pose[:4] = target
        elif mode in {3, 4, 5}:
            pose[4:] = target
        elif mode == 6:
            pose[4:] = [a + b for a, b in zip(pose[4:], target, strict=True)]
        else:  # Relative Cartesian modes 7 and 8; no kinematics.
            pose[:4] = [a + b for a, b in zip(pose[:4], target, strict=True)]
        encoded = self._pack_pose(pose)
        if rail is not None:
            _unpack("f", rail)
        self.responses[10] = encoded
        if rail is not None:
            self.responses[13] = rail

    def close(self) -> None:
        """Close the offline session without silently executing/clearing work."""
        with self._lock:
            self.closed = True
