"""Offline queue behavior with a bounded transport double, never serial IO."""

import struct
import threading
import time
from collections.abc import Callable
from typing import Any

import pytest

from dobot_usb.errors import (
    CommandInvalidated,
    ConnectionError,
    ExecutionTimeout,
    OutcomeUnknown,
    ProtocolError,
    RequestTimeout,
    RobotAlarmError,
)
from dobot_usb.execution import CommandExecutor, ExecutionState

MASK = 2**64 - 1


@pytest.mark.parametrize("length", [0, 1, 15, 17])
def test_malformed_alarm_state_cannot_complete_command(length: int) -> None:
    queue = QueueDouble()
    queue.alarms = bytes(length)
    queue.auto_complete = True
    runner = CommandExecutor(queue)
    with pytest.raises(ProtocolError) as error:
        runner.submit(84)
    assert error.value.stage == "accepted"
    assert runner.closed and queue.closed


def test_interrupt_during_poll_sleep_invalidates_accepted_handle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queue = QueueDouble()
    runner = CommandExecutor(queue)
    handle = runner.submit(84, wait=False)

    def interrupted(timeout: float) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(handle._wake, "wait", interrupted)
    with pytest.raises(KeyboardInterrupt) as error:
        handle.wait(0.1)
    assert runner.closed and queue.closed
    assert handle.state == ExecutionState.INVALIDATED
    assert error.value.__dict__["stage"] == "accepted"
    assert error.value.__dict__["queue_index"] == handle.queue_index


def test_preparation_timeout_reports_action_not_sent() -> None:
    queue = QueueDouble()
    runner = CommandExecutor(queue)

    def timeout(command: int, params: bytes, write: bool, queued: bool) -> bytes | None:
        raise RequestTimeout("index query lost", stage="sent_unconfirmed", command=246)

    queue.reply = timeout
    with pytest.raises(RequestTimeout) as error:
        runner.submit(84, wait=False)
    assert error.value.stage == "not_sent" and error.value.command == 84
    assert isinstance(error.value.__cause__, RequestTimeout)
    assert error.value.__cause__.command == 246
    assert queue.calls == [(246, b"", False, False)]


def test_close_after_ack_does_not_report_unsent_action() -> None:
    queue = QueueDouble()
    runner = CommandExecutor(queue)

    def close_after_ack(command: int, params: bytes, write: bool, queued: bool) -> bytes | None:
        if write:
            runner.close()
            return b""
        return None

    queue.reply = close_after_ack
    with pytest.raises(ConnectionError) as error:
        runner.submit(62, queued=False, wait=False)
    assert error.value.stage == "accepted"
    assert error.value.command == 62


class QueueDouble:
    def __init__(self, index: int = 0) -> None:
        self.index = index
        self.tail = index
        self.closed = False
        self.running = False
        self.auto_complete = False
        self.alarms = bytes(16)
        self.calls: list[tuple[int, bytes, bool, bool]] = []
        self.reply: Callable[[int, bytes, bool, bool], bytes | None] = lambda *args: None

    def request(
        self, command: int, params: bytes = b"", *, write: bool = False, queued: bool = False
    ) -> bytes:
        assert not self.closed
        self.calls.append((command, params, write, queued))
        reply = self.reply(command, params, write, queued)
        if reply is not None:
            return reply
        if queued:
            assert write
            self.tail = (self.tail + 1) & MASK
            return struct.pack("<Q", self.tail)
        if write:
            if command == 240:
                self.running = True
            if command in (241, 242):
                self.running = False
            if command == 245:
                self.tail = self.index
            return b""
        if command == 246:
            if self.running and self.auto_complete:
                self.index = self.tail
            return struct.pack("<Q", self.index)
        if command == 20:
            return self.alarms
        return b"get"

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def queue() -> QueueDouble:
    return QueueDouble()


def test_construction_and_close_send_no_actions(queue: QueueDouble) -> None:
    runner = CommandExecutor(queue)
    session = runner.session_id
    assert not runner.closed
    runner.close()
    runner.close()
    assert runner.closed and queue.closed
    assert runner.session_id != session
    assert queue.calls == []


def test_submit_starts_explicitly_but_ack_is_not_completion(queue: QueueDouble) -> None:
    runner = CommandExecutor(queue)
    handle = runner.submit(84, b"payload", wait=False)
    assert handle.queue_index == 1
    assert handle.state == ExecutionState.ACCEPTED
    assert handle.state == "accepted"
    assert handle.session_id == runner.session_id
    assert queue.calls == [
        (246, b"", False, False),
        (84, b"payload", True, True),
        (240, b"", True, False),
    ]
    assert queue.running
    with pytest.raises(ExecutionTimeout) as error:
        handle.wait(0.01)
    assert error.value.stage == "accepted"
    assert error.value.queue_index == 1
    assert handle.state == "accepted"
    queue.index = 1
    assert handle.wait(0.01) is handle
    assert handle.state == "completed"


def test_wait_true_completes_using_index(queue: QueueDouble) -> None:
    queue.auto_complete = True
    runner = CommandExecutor(queue)
    assert runner.submit(84).state == ExecutionState.COMPLETED
    assert all(call[0] not in (241, 242, 245) for call in queue.calls)


def test_multiple_handles_do_not_replay(queue: QueueDouble) -> None:
    runner = CommandExecutor(queue)
    first = runner.submit(84, wait=False)
    second = runner.submit(84, wait=False)
    queue.index = 2
    assert first.wait(0.1).state == "completed"
    assert second.wait(0.1).state == "completed"
    assert sum(c[0] == 84 for c in queue.calls) == 2


def test_serial_submissions_share_one_start_and_keep_their_own_index(queue: QueueDouble) -> None:
    queue.auto_complete = True
    runner = CommandExecutor(queue)
    first = runner.submit(84, b"first")
    second = runner.submit(84, b"second")
    assert (first.state, second.state) == ("completed", "completed")
    assert (first.queue_index, second.queue_index) == (1, 2)
    assert [(cmd, data) for cmd, data, write, _ in queue.calls if write] == [
        (84, b"first"),
        (240, b""),
        (84, b"second"),
    ]


@pytest.mark.parametrize("lifecycle,expected", [("pause", [241]), ("stop", [242, 245])])
def test_next_submission_restarts_after_stopping(
    queue: QueueDouble, lifecycle: str, expected: list[int]
) -> None:
    runner = CommandExecutor(queue)
    runner.start()
    getattr(runner, lifecycle)()
    runner.submit(84, wait=False)
    assert [call[0] for call in queue.calls if call[2]] == [240, *expected, 84, 240]


def test_clear_alone_preserves_run_state_and_explicit_start_still_sends(queue: QueueDouble) -> None:
    runner = CommandExecutor(queue)
    runner.start()
    runner.submit(84, wait=False)
    runner.clear()
    runner.submit(84, wait=False)
    runner.start()
    assert [call[0] for call in queue.calls if call[2]] == [240, 84, 245, 84, 240]


def test_immediate_ack_does_not_prove_completion(queue: QueueDouble) -> None:
    runner = CommandExecutor(queue)
    handle = runner.submit(73, queued=False, wait=False)
    assert handle.queue_index is None
    assert handle.state == "accepted"
    with pytest.raises(OutcomeUnknown) as error:
        handle.wait(0.1)
    assert error.value.stage == "accepted"
    assert queue.calls == [(73, b"", True, False)]


def test_immediate_wait_true_reports_accepted_unknown(queue: QueueDouble) -> None:
    runner = CommandExecutor(queue)
    with pytest.raises(OutcomeUnknown):
        runner.submit(73, queued=False)
    assert queue.calls == [(73, b"", True, False)]


def test_cancel_only_affects_its_handle(queue: QueueDouble) -> None:
    runner = CommandExecutor(queue)
    first = runner.submit(84, wait=False)
    second = runner.submit(84, wait=False)
    before = list(queue.calls)
    first.cancel()
    first.cancel()
    assert first.state == "cancelled"
    assert queue.calls == before and queue.running
    with pytest.raises(CommandInvalidated):
        first.wait(0.1)
    queue.index = 2
    assert second.wait(0.1).state == "completed"


@pytest.mark.parametrize(
    "operation,expected", [("clear", [245]), ("stop", [242, 245]), ("close", [])]
)
def test_invalidation_rotates_session_even_for_completed_handle(
    queue: QueueDouble, operation: str, expected: list[int]
) -> None:
    runner = CommandExecutor(queue)
    queue.auto_complete = True
    handle = runner.submit(84)
    session = runner.session_id
    before = len(queue.calls)
    getattr(runner, operation)()
    assert runner.session_id != session
    assert handle.state == "invalidated"
    with pytest.raises(CommandInvalidated):
        handle.wait(0.1)
    assert [c[0] for c in queue.calls[before:]] == expected


def test_pause_and_stop_without_clear_retain_receipts(queue: QueueDouble) -> None:
    runner = CommandExecutor(queue)
    handle = runner.submit(84, wait=False)
    session = runner.session_id
    runner.pause()
    runner.stop(clear=False)
    assert runner.session_id == session
    assert handle.state == "accepted"
    runner.start()
    queue.index = 1
    assert handle.wait(0.1).state == "completed"
    assert [c[0] for c in queue.calls if c[2] and not c[3]] == [240, 241, 242, 240]


def test_alarm_is_reported_before_index_completion(queue: QueueDouble) -> None:
    runner = CommandExecutor(queue)
    handle = runner.submit(84, wait=False)
    queue.index = 1
    queue.alarms = b"\x02\x80" + bytes(14)
    with pytest.raises(RobotAlarmError) as error:
        handle.wait(0.1)
    assert error.value.alarms == (1, 15)
    assert error.value.stage == "accepted"
    assert handle.state == "accepted"
    assert not runner.closed
    assert not any(c[0] in (242, 245) for c in queue.calls)


@pytest.mark.parametrize(
    "baseline,target,observed", [(MASK - 1, MASK, MASK), (MASK, 0, 0), (MASK - 1, 0, 1)]
)
def test_uint64_wrap_completes_with_forward_evidence(
    baseline: int, target: int, observed: int
) -> None:
    queue = QueueDouble(baseline)
    queue.tail = (target - 1) & MASK
    runner = CommandExecutor(queue)
    handle = runner.submit(84, wait=False)
    assert handle.queue_index == target
    queue.index = observed
    assert handle.wait(0.1).state == "completed"


@pytest.mark.parametrize("target", [10, 9, 10 + 2**63])
def test_stale_or_half_range_ack_invalidates(queue: QueueDouble, target: int) -> None:
    queue.index = 10
    queue.reply = lambda c, p, w, q: struct.pack("<Q", target) if q else None
    runner = CommandExecutor(queue)
    with pytest.raises(ProtocolError) as error:
        runner.submit(84, wait=False)
    assert error.value.stage == "accepted"
    assert runner.closed
    assert not any(c[0] == 240 for c in queue.calls)


@pytest.mark.parametrize("index", [9, 10 + 2**63])
def test_backward_or_ambiguous_current_index_never_completes(index: int) -> None:
    queue = QueueDouble(10)
    runner = CommandExecutor(queue)
    handle = runner.submit(84, wait=False)
    queue.index = index
    with pytest.raises(ProtocolError) as error:
        handle.wait(0.1)
    assert error.value.stage == "accepted"
    assert handle.state == "invalidated"
    assert runner.closed


def test_duplicate_queue_ack_is_not_completion(queue: QueueDouble) -> None:
    runner = CommandExecutor(queue)
    runner.submit(84, wait=False)
    queue.reply = lambda c, p, w, q: struct.pack("<Q", 1) if q else None
    with pytest.raises(ProtocolError):
        runner.submit(84, wait=False)
    assert runner.closed


def test_connection_loss_invalidates_all_without_replay(queue: QueueDouble) -> None:
    runner = CommandExecutor(queue)
    handle = runner.submit(84, wait=False)
    old_session = runner.session_id

    def fail(c: int, p: bytes, w: bool, q: bool) -> None:
        raise ConnectionError("gone")

    queue.reply = fail
    with pytest.raises(ConnectionError) as error:
        handle.wait(0.1)
    assert error.value.stage == "accepted"
    assert error.value.session_id == old_session
    assert runner.closed and runner.session_id != old_session
    assert handle.state == "invalidated"
    with pytest.raises(ConnectionError):
        runner.submit(84, wait=False)
    assert sum(c[0] == 84 for c in queue.calls) == 1


def test_lost_ack_is_not_replayed(queue: QueueDouble) -> None:
    def fail(c: int, p: bytes, w: bool, q: bool) -> None:
        if q:
            raise RequestTimeout("ACK lost", stage="sent_unconfirmed", command=c)

    queue.reply = fail
    runner = CommandExecutor(queue)
    with pytest.raises(RequestTimeout) as error:
        runner.submit(84)
    assert error.value.stage == "sent_unconfirmed"
    assert runner.closed
    assert sum(c[0] == 84 for c in queue.calls) == 1


@pytest.mark.parametrize("command", [84, 240, 246])
def test_malformed_reply_closes_session(queue: QueueDouble, command: int) -> None:
    queue.reply = lambda c, p, w, q: b"bad" if c == command else None
    runner = CommandExecutor(queue)
    with pytest.raises(ProtocolError):
        runner.submit(84, wait=False)
    assert runner.closed


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan"), True, "1"])
def test_invalid_deadlines_before_any_io(queue: QueueDouble, timeout: Any) -> None:
    runner = CommandExecutor(queue)
    with pytest.raises(ValueError):
        runner.submit(84, timeout=timeout)
    assert queue.calls == []


def test_lock_contention_has_deadline(queue: QueueDouble) -> None:
    runner = CommandExecutor(queue)
    assert runner._io_lock.acquire(timeout=0.01)
    try:
        with pytest.raises(ExecutionTimeout) as error:
            runner.submit(84, timeout=0.01)
        assert error.value.stage == "not_sent"
        assert queue.calls == []
    finally:
        runner._io_lock.release()


def run_in_thread(action: Callable[[], object]) -> tuple[threading.Thread, list[BaseException]]:
    errors: list[BaseException] = []

    def run() -> None:
        try:
            action()
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=run)
    thread.start()
    return thread, errors


def test_stop_interrupts_poll_sleep_without_clearing_otherwise(queue: QueueDouble) -> None:
    runner = CommandExecutor(queue, poll_interval=10)
    handle = runner.submit(84, wait=False)
    polling = threading.Event()
    queue.reply = lambda c, p, w, q: (polling.set() or None) if c == 20 else None
    thread, errors = run_in_thread(lambda: handle.wait(10))
    assert polling.wait(1)
    runner.stop(clear=False)
    thread.join(1)
    assert not thread.is_alive()
    assert len(errors) == 1 and isinstance(errors[0], CommandInvalidated)
    assert handle.state == "accepted"
    assert not queue.running


def test_stop_during_submission_prevents_late_start(queue: QueueDouble) -> None:
    runner = CommandExecutor(queue)
    accepted = threading.Event()
    release = threading.Event()

    def block(c: int, p: bytes, w: bool, q: bool) -> None:
        if q:
            accepted.set()
            assert release.wait(1)

    queue.reply = block
    submitter, submit_errors = run_in_thread(lambda: runner.submit(84, wait=False))
    assert accepted.wait(1)
    stopper, stop_errors = run_in_thread(runner.stop)
    # Observe the synchronous stop barrier before releasing the in-flight ACK.
    end = time.monotonic() + 1
    while not runner._stopping and time.monotonic() < end:
        threading.Event().wait(0.001)
    assert runner._stopping
    release.set()
    submitter.join(1)
    stopper.join(1)
    assert not submitter.is_alive() and not stopper.is_alive()
    assert not stop_errors
    assert len(submit_errors) == 1 and isinstance(submit_errors[0], CommandInvalidated)
    assert [c[0] for c in queue.calls] == [246, 84, 242, 245]
    assert not queue.running


def test_start_failure_preserves_accepted_action_outcome(queue: QueueDouble) -> None:
    def fail(c: int, p: bytes, w: bool, q: bool) -> None:
        if c == 240:
            raise RequestTimeout("start ACK lost", stage="sent_unconfirmed")

    queue.reply = fail
    runner = CommandExecutor(queue)
    with pytest.raises(RequestTimeout) as error:
        runner.submit(84)
    assert error.value.stage == "accepted"
    assert error.value.command == 84 and error.value.queue_index == 1
    assert runner.closed


def test_interruption_closes_session(queue: QueueDouble) -> None:
    def interrupt(c: int, p: bytes, w: bool, q: bool) -> None:
        raise KeyboardInterrupt()

    queue.reply = interrupt
    runner = CommandExecutor(queue)
    with pytest.raises(KeyboardInterrupt):
        runner.submit(84)
    assert runner.closed and queue.closed


def test_stop_during_start_finishes_with_force_stop(queue: QueueDouble) -> None:
    runner = CommandExecutor(queue)
    starting = threading.Event()
    release = threading.Event()

    def block(c: int, p: bytes, w: bool, q: bool) -> None:
        if c == 240:
            starting.set()
            assert release.wait(1)

    queue.reply = block
    submitter, submit_errors = run_in_thread(lambda: runner.submit(84, wait=False))
    assert starting.wait(1)
    stopper, stop_errors = run_in_thread(runner.stop)
    end = time.monotonic() + 1
    while not runner._stopping and time.monotonic() < end:
        threading.Event().wait(0.001)
    assert runner._stopping
    release.set()
    submitter.join(1)
    stopper.join(1)
    assert not submitter.is_alive() and not stopper.is_alive()
    assert not stop_errors
    assert len(submit_errors) == 1 and isinstance(submit_errors[0], CommandInvalidated)
    assert submit_errors[0].stage == "accepted"
    assert [c[0] for c in queue.calls] == [246, 84, 240, 242, 245]
    assert not queue.running


def test_stop_failure_invalidates_and_never_retries(queue: QueueDouble) -> None:
    runner = CommandExecutor(queue)
    handle = runner.submit(84, wait=False)

    def fail(c: int, p: bytes, w: bool, q: bool) -> None:
        if c == 242:
            raise RequestTimeout("force stop ACK lost", stage="sent_unconfirmed")

    queue.reply = fail
    with pytest.raises(RequestTimeout) as error:
        runner.stop()
    assert error.value.stage == "sent_unconfirmed"
    assert runner.closed and handle.state == "invalidated"
    assert sum(c[0] == 242 for c in queue.calls) == 1
    assert not any(c[0] == 245 for c in queue.calls)


def test_wait_transport_timeout_is_accepted_outcome(queue: QueueDouble) -> None:
    runner = CommandExecutor(queue)
    handle = runner.submit(84, wait=False)

    def fail(c: int, p: bytes, w: bool, q: bool) -> None:
        raise RequestTimeout("read timeout", stage="not_sent")

    queue.reply = fail
    with pytest.raises(RequestTimeout) as error:
        handle.wait(0.1)
    assert error.value.stage == "accepted" and error.value.queue_index == 1
    assert runner.closed


def test_new_work_after_clear_has_fresh_session_and_baseline(queue: QueueDouble) -> None:
    runner = CommandExecutor(queue)
    first = runner.submit(84, wait=False)
    runner.clear()
    queue.auto_complete = True
    second = runner.submit(84)
    assert first.session_id != second.session_id
    assert first.state == "invalidated" and second.state == "completed"


def test_deadline_after_alarm_query_does_not_start_another_request(queue: QueueDouble) -> None:
    runner = CommandExecutor(queue)
    handle = runner.submit(84, wait=False)

    def delayed(c: int, p: bytes, w: bool, q: bool) -> None:
        if c == 20:
            threading.Event().wait(0.02)

    queue.reply = delayed
    before = len(queue.calls)
    with pytest.raises(ExecutionTimeout) as error:
        handle.wait(0.01)
    assert error.value.stage == "accepted"
    assert [c[0] for c in queue.calls[before:]] == [20]
    assert not runner.closed and handle.state == "accepted"
