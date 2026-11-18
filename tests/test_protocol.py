import pytest

from dobot_usb.errors import ProtocolError
from dobot_usb.protocol import MAX_FRAME_SIZE, Frame, FrameParser, encode


def test_known_pose_query_packet() -> None:
    assert encode(Frame(10, 0)) == bytes.fromhex("aa aa 02 0a 00 f6")


def test_fragmented_frame_and_split_header_ignore_leading_noise() -> None:
    parser = FrameParser()
    packet = encode(Frame(10, 0, b"\x00\x01\xff\xaa"))
    assert parser.feed(b"noise\xaa") == []
    assert parser.feed(packet[1:4]) == []
    assert parser.feed(packet[4:-1]) == []
    assert parser.feed(packet[-1:]) == [Frame(10, 0, b"\x00\x01\xff\xaa")]
    assert parser.buffered_bytes == 0


def test_multiple_frames_in_one_chunk() -> None:
    frames = [Frame(2, 0, b"\x04\x00\x03"), Frame(20, 0, bytes(16))]
    assert FrameParser().feed(b"".join(map(encode, frames))) == frames


def test_maximum_length_and_noise_are_bounded() -> None:
    parser = FrameParser()
    assert parser.feed(b"noise" * 10000) == []
    assert parser.buffered_bytes == 0
    frame = Frame(0, 0, bytes(253))
    packet = encode(frame)
    assert len(packet) == MAX_FRAME_SIZE
    for value in packet[:-1]:
        assert parser.feed(bytes((value,))) == []
        assert parser.buffered_bytes < MAX_FRAME_SIZE
    assert parser.feed(packet[-1:]) == [frame]


def test_bad_checksum_resets_parser_and_is_explicit() -> None:
    parser = FrameParser()
    bad_packet = encode(Frame(10, 0))[:-1] + b"\x00"
    with pytest.raises(ProtocolError, match="checksum"):
        parser.feed(bad_packet)
    assert parser.buffered_bytes == 0
    assert parser.feed(encode(Frame(2, 0))) == [Frame(2, 0)]


@pytest.mark.parametrize("size", [0, 1])
def test_invalid_length_rejected(size: int) -> None:
    with pytest.raises(ProtocolError, match="length"):
        FrameParser().feed(b"\xaa\xaa" + bytes((size,)))


def test_reserved_control_bits_rejected() -> None:
    with pytest.raises(ProtocolError, match="reserved"):
        FrameParser().feed(bytes.fromhex("aa aa 02 0a 80 76"))


@pytest.mark.parametrize("command", [-1, 256, True, 1.5, "10"])
def test_invalid_command_rejected(command: object) -> None:
    with pytest.raises(ValueError, match="command"):
        Frame(command, 0)  # type: ignore[arg-type]


@pytest.mark.parametrize("control", [-1, 4, True, "0"])
def test_invalid_control_rejected(control: object) -> None:
    with pytest.raises(ValueError, match="control"):
        Frame(10, control)  # type: ignore[arg-type]


def test_mutable_or_oversized_parameters_rejected() -> None:
    with pytest.raises(TypeError, match="bytes"):
        Frame(10, 0, bytearray())  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="length"):
        Frame(10, 0, bytes(254))
    with pytest.raises(TypeError, match="bytes"):
        FrameParser().feed(bytearray())  # type: ignore[arg-type]
