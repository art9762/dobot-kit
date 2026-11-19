import struct
from collections import deque
from typing import Any

import pytest
import serial

from dobot_usb import Dobot
from dobot_usb.errors import DobotError, ProtocolError, RequestTimeout, RobotAlarmError
from dobot_usb.protocol import Frame, encode
from tests.test_transport import FakeSerial


class MagicianDouble:
    def __init__(self) -> None:
        self.device: FakeSerial | None = None
        self.requests: list[Frame] = []
        self.pose = (200.0, 5.0, 50.0, 30.0)
        self.destination = self.pose
        self.index = 0
        self.tail = 0
        self.running = False
        self.alarms = bytes(16)
        self.alarm_on_ack: int | None = None
        self.pose_reply: bytes | None = None
        self.move_samples: deque[tuple[float, float, float, float]] = deque()
        self.drop_ack: int | None = None

    def open(self, **options: Any) -> FakeSerial:
        self.device = FakeSerial(**options)
        self.device.reply = self.reply
        return self.device

    def reply(self, frame: Frame) -> bytes:
        self.requests.append(frame)
        if frame.command == self.drop_ack and frame.control == 3:
            assert self.device is not None
            self.device.read_error = serial.SerialTimeoutException("lost acknowledgement")
            return b""
        if frame.control == 3:
            self.tail += 1
            if frame.command == 84:
                self.destination = struct.unpack("<4f", frame.params[1:])
            if frame.command == self.alarm_on_ack:
                self.alarms = b"\x04" + bytes(15)
            return encode(Frame(frame.command, 3, struct.pack("<Q", self.tail)))
        if frame.control == 1:
            if frame.command in (240, 242):
                self.running = frame.command == 240
            if frame.command == 245:
                self.tail = self.index
                self.destination = self.pose
            return encode(Frame(frame.command, 1))

        if frame.command == 10:
            data = self.pose_reply
            if data is None:
                pose = (
                    self.move_samples.popleft() if self.index and self.move_samples else self.pose
                )
                data = struct.pack("<8f", *pose, 1, 2, 3, 4)
        elif frame.command == 20:
            data = self.alarms
        elif frame.command == 246:
            if self.running:
                self.index = self.tail
                self.pose = self.destination
            data = struct.pack("<Q", self.index)
        else:
            raise AssertionError(f"Unexpected command: {frame}")
        return encode(Frame(frame.command, 0, data))

    @property
    def writes(self) -> list[Frame]:
        return [frame for frame in self.requests if frame.control & 1]


@pytest.fixture
def magician(monkeypatch: pytest.MonkeyPatch) -> MagicianDouble:
    device = MagicianDouble()
    monkeypatch.setattr(serial, "Serial", device.open)
    return device


def test_read_position_and_disconnect_without_robot_writes(magician: MagicianDouble) -> None:
    with Dobot("test-port") as robot:
        assert robot.position() == (200.0, 5.0, 50.0, 30.0)

    assert magician.device is not None
    assert magician.device.port == "test-port"
    assert magician.device.close_count == 1
    assert magician.requests == [Frame(10, 0), Frame(10, 0)]


def test_failed_connection_check_closes_port(magician: MagicianDouble) -> None:
    magician.pose_reply = b"invalid"
    with pytest.raises(ProtocolError):
        Dobot("test-port")

    assert magician.device is not None
    assert magician.device.close_count == 1
    assert magician.writes == []


def test_move_keeps_current_angle_unless_given(magician: MagicianDouble) -> None:
    with Dobot("test-port") as robot:
        robot.move(210, 5, 60)
        assert magician.pose == (210.0, 5.0, 60.0, 30.0)
        robot.move(215, 0, 65, 0)
        assert magician.pose == (215.0, 0.0, 65.0, 0.0)

    assert magician.writes == [
        Frame(242, 1),
        Frame(245, 1),
        Frame(84, 3, struct.pack("<B4f", 2, 210, 5, 60, 30)),
        Frame(240, 1),
        Frame(84, 3, struct.pack("<B4f", 2, 215, 0, 65, 0)),
    ]


def test_move_waits_for_two_consecutive_matching_positions(magician: MagicianDouble) -> None:
    target = (210.0, 5.0, 60.0, 30.0)
    magician.move_samples.extend((target, magician.pose, target, target))

    with Dobot("test-port") as robot:
        robot.move(*target)

    assert not magician.move_samples
    assert [frame for frame in magician.writes if frame.command == 84] == [
        Frame(84, 3, struct.pack("<B4f", 2, *target))
    ]


def test_speed_home_tools_and_stop_use_the_expected_commands(magician: MagicianDouble) -> None:
    with Dobot("test-port") as robot:
        robot.speed(25)
        robot.home()
        robot.suction_on()
        robot.suction_off()
        robot.gripper_open()
        robot.gripper_close()
        robot.gripper_off()
        robot.stop()

    assert magician.writes == [
        Frame(242, 1),
        Frame(245, 1),
        Frame(83, 3, struct.pack("<2f", 25, 25)),
        Frame(240, 1),
        Frame(31, 3, bytes(4)),
        Frame(62, 3, b"\x01\x01"),
        Frame(62, 3, bytes(2)),
        Frame(63, 3, b"\x01\x00"),
        Frame(63, 3, b"\x01\x01"),
        Frame(63, 3, bytes(2)),
        Frame(242, 1),
        Frame(245, 1),
    ]


def test_stop_discards_existing_work_before_first_move(magician: MagicianDouble) -> None:
    magician.tail = 1
    magician.destination = (300, 20, 80, 0)
    with Dobot("test-port") as robot:
        robot.stop()
        robot.move(210, 10, 50)

    assert magician.pose == (210.0, 10.0, 50.0, 30.0)
    assert magician.writes == [
        Frame(242, 1),
        Frame(245, 1),
        Frame(84, 3, struct.pack("<B4f", 2, 210, 10, 50, 30)),
        Frame(240, 1),
    ]


def test_bad_arguments_send_no_commands(magician: MagicianDouble) -> None:
    with Dobot("test-port") as robot:
        with pytest.raises(ValueError, match="x"):
            robot.move(float("nan"), 10, 10)
        with pytest.raises(ValueError, match="angle"):
            robot.move(200, 10, 10, float("inf"))
        for bad_speed in (0, 101, True):
            with pytest.raises(ValueError):
                robot.speed(bad_speed)

    assert magician.requests == [Frame(10, 0)]


def test_lost_move_ack_neither_replays_move_nor_starts_queue(magician: MagicianDouble) -> None:
    magician.drop_ack = 84
    with Dobot("test-port") as robot, pytest.raises(RequestTimeout) as caught:
        robot.move(210, 10, 50)

    assert magician.writes == [
        Frame(242, 1),
        Frame(245, 1),
        Frame(84, 3, struct.pack("<B4f", 2, 210, 10, 50, 30)),
    ]
    assert caught.value.stage == "sent_unconfirmed"
    assert magician.device is not None
    assert magician.device.close_count == 1


def test_alarm_after_ack_requires_explicit_stop_before_another_action(
    magician: MagicianDouble,
) -> None:
    magician.alarm_on_ack = 84
    original_pose = magician.pose
    with Dobot("test-port") as robot:
        with pytest.raises(RobotAlarmError) as alarm:
            robot.move(210, 10, 50)
        assert alarm.value.stage == "accepted"

        magician.alarms = bytes(16)
        last_request = len(magician.requests)
        with pytest.raises(DobotError, match="stop") as blocked:
            robot.speed(20)
        assert blocked.value.stage == "not_sent"
        assert len(magician.requests) == last_request

        robot.stop()
        robot.speed(20)

    assert magician.pose == original_pose
    assert magician.writes[-4:] == [
        Frame(242, 1),
        Frame(245, 1),
        Frame(83, 3, struct.pack("<2f", 20, 20)),
        Frame(240, 1),
    ]
