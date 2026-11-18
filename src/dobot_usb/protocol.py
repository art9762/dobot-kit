"""Small binary codec for the classic Magician communication protocol.

The one-byte wire length counts ID, control, and parameters. Integers and
floating-point parameter values are decoded by the client, in little endian.
"""

from dataclasses import dataclass

from .errors import ProtocolError

HEADER = b"\xaa\xaa"
MAX_PAYLOAD_SIZE = 255
MAX_FRAME_SIZE = MAX_PAYLOAD_SIZE + 4


@dataclass(frozen=True)
class Frame:
    """A complete payload; control bit 0 is write and bit 1 is queued."""

    command: int
    control: int
    params: bytes = b""

    def __post_init__(self) -> None:
        if type(self.command) is not int or not 0 <= self.command <= 255:
            raise ValueError("command must be an integer from 0 to 255")
        if type(self.control) is not int or not 0 <= self.control <= 3:
            raise ValueError("control must contain only write/queued bits")
        if not isinstance(self.params, bytes):
            raise TypeError("params must be bytes")
        if len(self.params) > MAX_PAYLOAD_SIZE - 2:
            raise ValueError("params exceed the one-byte payload length")


def encode(frame: Frame) -> bytes:
    """Encode a payload and its two's-complement checksum."""
    payload = bytes((frame.command, frame.control)) + frame.params
    return HEADER + bytes((len(payload),)) + payload + bytes((-sum(payload) & 0xFF,))


class FrameParser:
    """Incremental parser with bounded memory and explicit corruption errors.

    Leading noise is ignored. A malformed length, control byte, or checksum
    raises ProtocolError and discards pending data; callers must treat the
    associated serial transaction as failed.
    """

    def __init__(self) -> None:
        self._buffer = bytearray()

    @property
    def buffered_bytes(self) -> int:
        """Number of bytes held for an incomplete frame (at most 258)."""
        return len(self._buffer)

    def feed(self, data: bytes) -> list[Frame]:
        """Consume arbitrary chunks, returning only complete valid frames."""
        if not isinstance(data, bytes):
            raise TypeError("data must be bytes")
        frames: list[Frame] = []
        # Consume one byte at a time so a large noise chunk never gets retained.
        for value in data:
            self._buffer.append(value)
            if len(self._buffer) == 1:
                if value != 0xAA:
                    self._buffer.clear()
                continue
            if len(self._buffer) == 2:
                if self._buffer != HEADER:
                    self._buffer.clear()
                continue
            size = self._buffer[2]
            if size < 2:
                self._buffer.clear()
                raise ProtocolError("payload length must include ID and control")
            if len(self._buffer) < size + 4:
                continue
            packet = bytes(self._buffer)
            self._buffer.clear()
            if sum(packet[3:]) & 0xFF:
                raise ProtocolError("response checksum is invalid")
            if packet[4] & ~3:
                raise ProtocolError("response uses reserved control bits")
            frames.append(Frame(packet[3], packet[4], packet[5:-1]))
        return frames
