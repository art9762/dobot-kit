"""Read-only pose confirmation through the normal Motion facade."""

import struct
from unittest.mock import Mock

import pytest

from dobot_usb import Dobot, ExecutionTimeout, Point, Pose, RobotAlarmError

from .simulation import SimulatedTransport

TARGET = Point(200, 0, 35, 0)


class Clock:
    now = 0.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def packet(x: float = 200, y: float = 0, r: float = 0) -> bytes:
    return struct.pack("<8f", x, y, 35, r, 0, 20, 30, 0)


def test_confirmation_uses_consecutive_matching_stable_samples(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = Clock()
    monkeypatch.setattr("dobot_usb.motion.time", clock)
    transport = SimulatedTransport(allow_writes=False)
    # Euclidean distance, a single good sample, R mismatch, then a jump across
    # the tolerance band. Only the final pair matches both target and each other.
    xyzr = iter(
        [
            packet(200.25, 0.25),
            packet(),
            packet(r=1),
            packet(199.75),
            packet(200.25),
            packet(200.24),
        ]
    )
    get = transport.request

    def request(command: int, *args: object, **kwargs: object) -> bytes:
        if command == 10:
            transport.responses[10] = next(xyzr)
        return get(command, *args, **kwargs)

    monkeypatch.setattr(transport, "request", request)
    with Dobot(transport) as robot:
        result = robot.motion.wait_for_pose(TARGET)
    assert isinstance(result, Pose)
    assert result.x == pytest.approx(200.24)
    assert transport.requests == [10, 20] * 6
    assert clock.now == pytest.approx(0.2)


def test_alarm_blocks_confirmation_on_final_sample(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = Clock()
    monkeypatch.setattr("dobot_usb.motion.time", clock)
    transport = SimulatedTransport(allow_writes=False)
    transport.responses[10] = packet()

    def tick(seconds: float) -> None:
        clock.now += seconds
        transport.responses[20] = (1 << 69).to_bytes(16, "little")

    monkeypatch.setattr(clock, "sleep", tick)
    with Dobot(transport) as robot, pytest.raises(RobotAlarmError) as exc:
        robot.motion.wait_for_pose(TARGET)
    assert exc.value.alarms == (69,)
    assert transport.requests == [10, 20] * 2


def test_no_success_after_deadline_crossed_by_alarm_query(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = Clock()
    monkeypatch.setattr("dobot_usb.motion.time", clock)
    transport = SimulatedTransport(allow_writes=False)
    transport.responses[10] = packet()
    get = transport.request

    def slow_read(command: int, *args: object, **kwargs: object) -> bytes:
        if transport.requests == [10, 20, 10] and command == 20:
            clock.now += 1
        return get(command, *args, **kwargs)

    monkeypatch.setattr(transport, "request", slow_read)
    with Dobot(transport) as robot, pytest.raises(ExecutionTimeout) as exc:
        robot.motion.wait_for_pose(TARGET, timeout=0.2)
    assert exc.value.stage == "unknown"
    assert exc.value.command is None and exc.value.queue_index is None
    assert transport.requests == [10, 20] * 2


@pytest.mark.parametrize(
    "target,kwargs",
    [
        (Point(0, 0, float("nan"), 0), {}),
        (None, {}),
        (TARGET, {"timeout": 0}),
        (TARGET, {"linear_tolerance_mm": -1}),
        (TARGET, {"angular_tolerance_deg": True}),
    ],
)
def test_invalid_arguments_before_queries(target: object, kwargs: dict[str, object]) -> None:
    transport = Mock(spec=SimulatedTransport)
    with Dobot(transport) as robot, pytest.raises(ValueError):
        robot.motion.wait_for_pose(target, **kwargs)
    transport.request.assert_not_called()
