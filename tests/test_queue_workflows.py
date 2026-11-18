"""Public workflows with a deterministic Magician simulator and no serial access."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from dobotkit import CommandInvalidated, Dobot, ExecutionState, ExecutionTimeout, Path, Point

from .simulation import SimulatedTransport


def test_timeout_keeps_accepted_motion_visible_to_queue_and_implicit_r() -> None:
    sim = SimulatedTransport(auto_complete=False)
    with Dobot(sim) as robot:
        with pytest.raises(ExecutionTimeout) as first:
            robot.move_to(200, 0, 50, 45, timeout=0.025)
        assert (first.value.stage, first.value.queue_index) == ("accepted", 1)
        with pytest.raises(ExecutionTimeout):
            robot.queue.wait(timeout=0.025)
        with pytest.raises(ExecutionTimeout) as second:
            robot.move_to(201, 0, 50, timeout=0.025)
        assert second.value.queue_index == 1
        assert sim.requests.count(84) == 1
        assert sim.advance() == 1
        robot.queue.wait(timeout=0.5)
        with pytest.raises(ExecutionTimeout) as next_move:
            robot.move_to(201, 0, 50, timeout=0.025)
        assert next_move.value.queue_index == 2
        assert sim.advance() == 1
        robot.queue.wait(timeout=0.5)
        assert robot.pose().r == 45


def test_queue_wait_sees_another_call_still_waiting_for_its_accepted_command() -> None:
    polling = Event()

    class ObservedController(SimulatedTransport):
        def request(
            self, command: int, params: bytes = b"", *, write: bool = False, queued: bool = False
        ) -> bytes:
            reply = super().request(command, params, write=write, queued=queued)
            if command == 20 and 84 in self.requests:
                polling.set()
            return reply

    sim = ObservedController(auto_complete=False)
    with Dobot(sim) as robot, ThreadPoolExecutor(max_workers=1) as pool:
        motion = pool.submit(robot.move_by, x=5, timeout=3)
        assert polling.wait(2)
        try:
            with pytest.raises(ExecutionTimeout) as waiting:
                robot.queue.wait(timeout=0.025)
            assert waiting.value.queue_index == 1
        finally:
            sim.advance()
        assert motion.result(2).state is ExecutionState.COMPLETED
        robot.queue.wait(timeout=0.5)


def test_cancelling_public_handle_leaves_queue_fence_and_other_receipts_intact() -> None:
    sim = SimulatedTransport(auto_complete=False)
    with Dobot(sim) as robot:
        earlier = robot.move_by(x=1, wait=False)
        cancelled = robot.move_by(r=5, wait=False)
        cancelled.cancel()
        assert sim.advance() == 1
        with pytest.raises(ExecutionTimeout):
            robot.queue.wait(timeout=0.025)
        assert sim.advance() == 1
        robot.queue.wait(timeout=0.5)
        assert earlier.state is ExecutionState.COMPLETED
        assert cancelled.state is ExecutionState.CANCELLED
        with pytest.raises(CommandInvalidated):
            cancelled.wait(timeout=0.1)
        with pytest.raises(ExecutionTimeout) as next_move:
            robot.move_to(201, 0, 50, timeout=0.025)
        assert next_move.value.queue_index == 3
        assert sim.advance() == 1
        robot.queue.wait(timeout=0.5)
        assert robot.pose().r == 5


@pytest.mark.parametrize("interrupt", ["stop", "pause", "clear"])
@pytest.mark.parametrize("wait", [True, False])
def test_no_route_restart_after_interrupt_during_waypoint_preflight(
    interrupt: str, wait: bool
) -> None:
    paused, resume = Event(), Event()

    class GatedRobot(Dobot):
        reads = 0

        def alarms(self) -> tuple[int, ...]:
            alarms = super().alarms()
            self.reads += 1
            if self.reads == 2:
                paused.set()
                assert resume.wait(3)
            return alarms

    sim = SimulatedTransport()
    with GatedRobot(sim) as robot, ThreadPoolExecutor(max_workers=1) as pool:
        path = Path([Point(201, 0, 50, 0), Point(210, 0, 50, 0)])
        result = pool.submit(path.run, robot.motion, wait=wait)
        try:
            assert paused.wait(3)
            getattr(robot.queue, interrupt)()
            writes_at_stop = [f for f in sim.transactions if f.control & 1]
        finally:
            resume.set()
        with pytest.raises(CommandInvalidated) as exc:
            result.result(3)
        assert exc.value.stage == "not_sent"
        assert [f for f in sim.transactions if f.control & 1] == writes_at_stop
        assert sum(f.command == 84 for f in writes_at_stop) == 1
        assert robot.pose().x == (201 if wait else 200)
        # A new, separately requested action gets its own generation.
        robot.move_to(202, 0, 50, 0)
        assert robot.pose().x == 202


def test_nested_sequence_cannot_restart_an_invalidated_outer_scope() -> None:
    with Dobot(SimulatedTransport()) as robot:
        with robot.motion.sequence():
            robot.stop()
            with pytest.raises(CommandInvalidated):
                with robot.motion.sequence():
                    robot.move_by(x=1)
        robot.move_by(x=1)
        assert robot.pose().x == 201


def test_empty_route_remains_an_offline_noop_after_close() -> None:
    robot = Dobot(SimulatedTransport())
    robot.close()
    assert Path().run(robot.motion) == ()
