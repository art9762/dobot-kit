"""Exercise the actual facade, codecs, executor and simulator together, without USB."""

import struct

import pytest

from dobot_usb import (
    CommandInvalidated,
    Dobot,
    ExecutionState,
    OutcomeUnknown,
    Path,
    Point,
    ProtocolError,
    RobotAlarmError,
    UnsupportedCommand,
)
from dobot_usb.protocol import Frame, FrameParser, encode
from dobot_usb.tools import LaserNotArmed

from .simulation import SimulatedTransport


def test_constructor_and_close_have_no_hidden_actions() -> None:
    transport = SimulatedTransport()
    robot = Dobot(transport)
    assert transport.requests == []
    robot.laser.arm()
    robot.close()
    assert not robot.laser.armed
    assert transport.requests == []


def test_pick_and_place_through_real_command_layers() -> None:
    with Dobot(SimulatedTransport()) as robot:
        robot.speed(mm_s=30, mm_s2=100)
        h = robot.move_to(200, 0, 60, r=5)
        assert h.state == ExecutionState.COMPLETED
        robot.suction.on()
        assert robot.suction.state().active
        robot.move_by(x=10, z=30)
        robot.move_to(180, 40, 90)  # wait=True preserves R after synchronization
        robot.suction.off()
        assert (robot.pose().x, robot.pose().y, robot.pose().z, robot.pose().r) == (180, 40, 90, 5)
        assert not robot.suction.state().enabled


def test_queue_targets_and_clear_invalidate_old_receipts() -> None:
    transport = SimulatedTransport(auto_complete=False)
    with Dobot(transport) as robot:
        first = robot.move_to(201, 0, 50, r=0, wait=False)
        second = robot.move_by(x=2, wait=False)
        assert first.state == second.state == ExecutionState.ACCEPTED
        robot.stop()
        assert first.state == second.state == ExecutionState.INVALIDATED
        with pytest.raises(CommandInvalidated):
            first.wait(0.05)
        third = robot.move_to(205, 0, 50, r=1, wait=False, timeout=0.1)
        transport.advance()
        third.wait(0.1)
        assert robot.pose().x == 205


def test_tools_configuration_and_advanced_set_name() -> None:
    with Dobot(SimulatedTransport()) as robot:
        robot.settings.set_name("table robot")
        assert robot.device_info().name == "table robot"
        robot.advanced.set("device_name", name="renamed")
        assert robot.advanced.get("device_name")["name"] == "renamed"
        robot.settings.set_serial_number("Lab-ä")
        assert robot.device_info().serial_number == "Lab-ä"
        robot.tool.offset(x=1, y=2, z=3)
        assert robot.tool.get_offset().z == 3
        robot.gripper.close()
        assert robot.gripper.state().active
        robot.gripper.open()
        assert not robot.gripper.state().active
        robot.gripper.release()
        assert not robot.gripper.state().enabled


def test_laser_needs_arm_and_clear_revokes_it() -> None:
    with Dobot(SimulatedTransport()) as robot:
        with pytest.raises(LaserNotArmed):
            robot.laser.on()
        robot.laser.arm()
        receipt = robot.laser.on()
        assert receipt.state == ExecutionState.ACCEPTED
        with pytest.raises(OutcomeUnknown):
            receipt.wait(0.05)
        robot.queue.clear()
        assert not robot.laser.armed
        robot.laser.off()  # an off action is always available
        assert not robot.laser.state().active


def test_named_codecs_arc_cp_io_and_rail_compose() -> None:
    with Dobot(SimulatedTransport()) as robot:
        robot.motion.arc(Point(205, 5, 50, 0), Point(210, 0, 50, 0))
        assert robot.pose().x == 210
        robot.motion.line_by(z=5)
        assert robot.pose().z == 55
        robot.io.write(1, True)
        assert robot.io.output(1)
        robot.rail.configure(enabled=True, version=1)
        robot.rail.move_to(10, x=200, y=0, z=60, r=0)
        assert robot.rail.position() == 10


def test_alarm_prevents_motion_before_any_write() -> None:
    transport = SimulatedTransport()
    transport.responses[20] = b"\1" + bytes(15)
    with Dobot(transport) as robot:
        with pytest.raises(RobotAlarmError) as error:
            robot.move_by(x=1)
        assert error.value.stage == "not_sent"
        assert not any(frame.control & 1 for frame in transport.transactions)


def test_read_only_policy_rejects_writes() -> None:
    with Dobot(SimulatedTransport(allow_writes=False)) as robot:
        assert robot.pose().x == 200
        with pytest.raises(UnsupportedCommand) as error:
            robot.suction.on()
        assert error.value.stage == "not_sent"


def test_full_path_validation_precedes_all_io() -> None:
    transport = SimulatedTransport()
    with Dobot(transport) as robot:
        route = Path([Point(200, 0, 60, 0), Point(210, 0, 60, 0)])
        handles = route.run(robot.motion)
        assert all(h.state == ExecutionState.COMPLETED for h in handles)
        assert robot.pose().x == 210


def test_conveyor_requires_measured_calibration() -> None:
    transport = SimulatedTransport()
    with Dobot(transport) as robot:
        with pytest.raises(ValueError):
            robot.conveyor.run(10)
        assert transport.requests == []
        robot.conveyor.configure(index=0, steps_per_mm=20)
        assert transport.requests == []
        robot.conveyor.run(-10)
        motor = [f for f in transport.transactions if f.command == 135][-1]
        assert motor.params == struct.pack("<BBi", 0, 1, -200)
        robot.conveyor.stop()


def test_bad_schema_response_cannot_be_used_as_motion_state() -> None:
    transport = SimulatedTransport()
    transport.responses[10] = bytes(31)
    with Dobot(transport) as robot:
        with pytest.raises(ProtocolError):
            robot.move_to(200, 0, 60)
        assert not any(frame.control & 1 for frame in transport.transactions)


def test_complete_client_over_binary_serial_frames(monkeypatch: pytest.MonkeyPatch) -> None:
    """Check real framing/transport/ACK handling without an operating-system port."""
    controller = SimulatedTransport()

    class WireSerial:
        def __init__(self, **kwargs: object) -> None:
            self.is_open = False
            self.buffer = bytearray()
            self.parser = FrameParser()

        def open(self) -> None:
            self.is_open = True

        def close(self) -> None:
            self.is_open = False

        def reset_input_buffer(self) -> None:
            self.buffer.clear()

        @property
        def in_waiting(self) -> int:
            return len(self.buffer)

        def write(self, packet: bytes) -> int:
            for request in self.parser.feed(packet):
                write, queued = bool(request.control & 1), bool(request.control & 2)
                params = controller.request(
                    request.command, request.params, write=write, queued=queued
                )
                # A queued reply with rw=1 but no queue-bit echo is accepted by
                # the documented compatibility policy and must retain its index.
                self.buffer.extend(encode(Frame(request.command, int(write), params)))
            return len(packet)

        def read(self, size: int) -> bytes:
            result = bytes(self.buffer[:size])
            del self.buffer[:size]
            return result

    monkeypatch.setattr("dobot_usb.transport.serial.Serial", WireSerial)
    with Dobot.connect("mock-binary-port", timeout=0.1) as robot:
        assert not any(f.control & 1 for f in controller.transactions)
        robot.move_to(210, 5, 70, r=2, timeout=0.2)
        robot.suction.on(timeout=0.2)
        assert robot.pose().x == 210 and robot.suction.state().active
        assert any(f.command == 84 and f.control == 3 for f in controller.transactions)
