"""Session-bound, single-owner command queue lifecycle without action replay."""

from __future__ import annotations

import math
import struct
import threading
import time
import uuid
import weakref
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from enum import StrEnum

from .errors import (
    CommandInvalidated,
    ConnectionError,
    DobotError,
    ExecutionTimeout,
    OutcomeUnknown,
    ProtocolError,
    RobotAlarmError,
)
from .protocol import Frame
from .transport import Transport

_MODULUS = 1 << 64
_HALF = 1 << 63
_CONTROL_TIMEOUT = 30.0


class ExecutionState(StrEnum):
    """ACK is ACCEPTED; only an observed queue index can mean COMPLETED."""

    ACCEPTED = "accepted"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    INVALIDATED = "invalidated"


def _duration(value: float, name: str = "timeout") -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value <= 0
    ):
        raise ValueError(f"{name} must be finite and greater than zero")
    return float(value)


def _validate(command: int, params: bytes) -> None:
    # Frame validates uint8 ID, bytes and maximum payload before any IO.
    Frame(command, 0, params)


class CommandHandle:
    """Receipt for one accepted action, tied to an executor session.

    Immediate actions have no observable completion index. ``wait`` then raises
    OutcomeUnknown; the handle stays ACCEPTED. Cancelling only affects this
    handle's waiting, never the controller or other commands.
    """

    def __init__(
        self,
        executor: CommandExecutor,
        *,
        command: int,
        queue_index: int | None,
        baseline: int | None,
        session_id: str,
    ) -> None:
        self._executor = executor
        self.command = command
        self.queue_index = queue_index
        self.session_id = session_id
        self._baseline = baseline
        self._state = ExecutionState.ACCEPTED
        self._wake = threading.Event()

    def _independent_waiter(self) -> CommandHandle:
        """Track the same ACK independently of caller cancellation."""
        return CommandHandle(
            self._executor,
            command=self.command,
            queue_index=self.queue_index,
            baseline=self._baseline,
            session_id=self.session_id,
        )

    @property
    def state(self) -> ExecutionState:
        with self._executor._state_lock:
            return self._state

    def wait(self, timeout: float = 30.0) -> CommandHandle:
        try:
            return self._executor._wait(self, timeout)
        except (KeyboardInterrupt, SystemExit) as exc:
            self._executor.close()
            exc.__dict__.update(
                stage="accepted",
                command=self.command,
                queue_index=self.queue_index,
                session_id=self.session_id,
            )
            raise

    def cancel(self) -> None:
        with self._executor._state_lock:
            if self._state == ExecutionState.ACCEPTED:
                self._state = ExecutionState.CANCELLED
                self._wake.set()


class CommandExecutor:
    """Serialize submissions and lifecycle operations for one exclusive queue.

    Requires sole ownership of the controller queue (including other software).
    The transport must provide bounded IO. Lock acquisition and polling have
    finite deadlines; an in-flight transport request is bounded by its own
    deadline and cannot be preempted by the generic Transport interface.
    """

    def __init__(self, transport: Transport, *, poll_interval: float = 0.02) -> None:
        self._poll_interval = _duration(poll_interval, "poll_interval")
        self._transport = transport
        self._io_lock = threading.Lock()
        self._state_lock = threading.RLock()
        self._closed = False
        self._session_id = uuid.uuid4().hex
        self._handles: weakref.WeakSet[CommandHandle] = weakref.WeakSet()
        self._epoch = 0
        self._sequence_epoch: ContextVar[int | None] = ContextVar("queue_epoch", default=None)
        self._stopping = 0
        self._last_index: int | None = None
        self._tail: CommandHandle | None = None
        # Under _io_lock. Track acknowledged lifecycle writes by this exclusive owner.
        # A standalone CLEAR removes queued commands but does not change run state.
        self._queue_started = False
        self._queue_cleared = False

    @property
    def closed(self) -> bool:
        with self._state_lock:
            return self._closed

    @property
    def session_id(self) -> str:
        with self._state_lock:
            return self._session_id

    def _action_epoch(self) -> int:
        # Called with the state lock. Nested operations inherit their sequence's epoch.
        captured = self._sequence_epoch.get()
        return self._epoch if captured is None else captured

    @contextmanager
    def sequence(self) -> Iterator[None]:
        """Bind a synchronous multi-step operation to the current queue generation."""
        with self._state_lock:
            epoch = self._action_epoch()
            self._check_epoch(epoch)
        token = self._sequence_epoch.set(epoch)
        try:
            yield
        finally:
            self._sequence_epoch.reset(token)

    def _ensure_open(self) -> None:
        if self._closed:
            raise ConnectionError("executor session is closed", stage="not_sent")

    def _invalidate(self) -> None:
        """Called with the short-lived state lock, never waiting for IO."""
        for handle in self._handles:
            handle._state = ExecutionState.INVALIDATED
            handle._wake.set()
        self._epoch += 1
        self._session_id = uuid.uuid4().hex
        self._last_index = self._tail = None

    def close(self) -> None:
        """Close the link only. Does not stop, clear, home or change outputs."""
        with self._state_lock:
            if self._closed:
                return
            self._closed = True
            self._invalidate()
        self._transport.close()

    @contextmanager
    def _locked(self, deadline: float) -> Iterator[None]:
        acquired = False
        try:
            acquired = self._io_lock.acquire(timeout=max(0.0, deadline - time.monotonic()))
            if not acquired:
                raise ExecutionTimeout("executor lock deadline expired", stage="not_sent")
            with self._state_lock:
                self._ensure_open()
            if time.monotonic() >= deadline:
                raise ExecutionTimeout("operation deadline expired", stage="not_sent")
            yield
        except (KeyboardInterrupt, SystemExit):
            self.close()
            raise
        finally:
            if acquired:
                self._io_lock.release()

    def _exchange(
        self, command: int, params: bytes = b"", *, write: bool = False, queued: bool = False
    ) -> bytes:
        try:
            data = self._transport.request(command, params, write=write, queued=queued)
            with self._state_lock:
                if self._closed:
                    raise ConnectionError(
                        "session closed while receiving the command acknowledgement",
                        stage="accepted"
                        if write and isinstance(data, bytes) and len(data) == (8 if queued else 0)
                        else "unknown",
                        command=command,
                    )
            if not isinstance(data, bytes):
                raise ProtocolError("transport response must be bytes")
            if write and len(data) != (8 if queued else 0):
                raise ProtocolError(
                    "SET reply has invalid length", stage="sent_unconfirmed", command=command
                )
            if write and not queued and command in (240, 241, 242):
                self._queue_started = command == 240
            if write and not queued and command == 245:
                self._queue_cleared = True
            return data
        except BaseException as exc:
            if write:
                if isinstance(exc, DobotError):
                    if exc.stage == "unknown" and isinstance(exc, ProtocolError):
                        exc.stage = "sent_unconfirmed"
                    exc.command = command
                elif "stage" not in exc.__dict__:
                    exc.__dict__.update(stage="unknown", command=command)
            self.close()
            raise

    def _index(self) -> int:
        data = self._exchange(246)
        if len(data) != 8:
            self.close()
            raise ProtocolError("queue index must be exactly uint64")
        index = int(struct.unpack("<Q", data)[0])
        if self._last_index is not None and (index - self._last_index) % _MODULUS >= _HALF:
            self.close()
            raise ProtocolError("queue index moved backwards or by an ambiguous half-range")
        self._last_index = index
        return index

    def current_index(self, *, timeout: float = _CONTROL_TIMEOUT) -> int:
        with self._locked(time.monotonic() + _duration(timeout)):
            return self._index()

    def read(self, command: int, params: bytes = b"") -> bytes:
        _validate(command, params)
        with self._locked(time.monotonic() + _CONTROL_TIMEOUT):
            epoch = self._sequence_epoch.get()
            if epoch is not None:
                self._check_epoch(epoch)
            data = self._exchange(command, params)
            if epoch is not None:
                self._check_epoch(epoch)
            return data

    def _check_epoch(self, epoch: int, handle: CommandHandle | None = None) -> None:
        with self._state_lock:
            if self._closed and handle is None:
                self._ensure_open()
            if self._closed or epoch != self._epoch or self._stopping:
                raise CommandInvalidated(
                    "submission interrupted by queue lifecycle operation",
                    stage="accepted" if handle else "not_sent",
                    command=handle.command if handle else None,
                    queue_index=handle.queue_index if handle else None,
                    session_id=handle.session_id if handle else self._session_id,
                )

    def submit(
        self,
        command: int,
        params: bytes = b"",
        *,
        queued: bool = True,
        wait: bool = True,
        timeout: float = 30.0,
    ) -> CommandHandle:
        _validate(command, params)
        duration = _duration(timeout)
        if type(queued) is not bool or type(wait) is not bool:
            raise TypeError("queued and wait must be bool")
        deadline = time.monotonic() + duration
        with self._state_lock:
            self._ensure_open()
            epoch = self._action_epoch()
            session_id = self._session_id
        handle: CommandHandle | None = None
        with self._locked(deadline):
            self._check_epoch(epoch)
            try:
                baseline = self._index() if queued else None
            except DobotError as exc:
                # Only preparation telemetry has been sent. The requested SET
                # has not crossed the transport boundary, whatever failed in GET.
                raise type(exc)(
                    f"command preparation failed: {exc}",
                    stage="not_sent",
                    command=command,
                    session_id=session_id,
                ) from exc
            self._check_epoch(epoch)
            if time.monotonic() >= deadline:
                raise ExecutionTimeout("submit deadline expired before SET", stage="not_sent")
            data = self._exchange(command, params, write=True, queued=queued)
            index = int(struct.unpack("<Q", data)[0]) if queued else None
            invalid_index = False
            with self._state_lock:
                handle = CommandHandle(
                    self,
                    command=command,
                    queue_index=index,
                    baseline=baseline,
                    session_id=session_id,
                )
                self._handles.add(handle)
                if session_id != self._session_id or self._closed:
                    handle._state = ExecutionState.INVALIDATED
                elif index is not None and baseline is not None:
                    tail_index = self._tail.queue_index if self._tail is not None else None
                    distance = (index - baseline) % _MODULUS
                    tail_distance = (index - tail_index) % _MODULUS if tail_index is not None else 1
                    invalid_index = not (0 < distance < _HALF and 0 < tail_distance < _HALF)
                    if not invalid_index:
                        # Register at ACK, before START or the caller's wait can fail.
                        self._tail = handle._independent_waiter()
                        self._handles.add(self._tail)
            if invalid_index:
                self.close()
                raise self._handle_error(
                    ProtocolError, handle, "accepted queue index is stale or ambiguous"
                )
            self._check_epoch(epoch, handle)
            if queued:
                try:
                    if time.monotonic() >= deadline:
                        raise ExecutionTimeout("accepted before submit deadline expired")
                    # Restarting a running 3.7.0 queue can advance the index
                    # without reaching the newly submitted MOVL target.
                    if not self._queue_started:
                        self._exchange(240, write=True)
                except DobotError as exc:
                    exc.stage = "accepted"
                    exc.command = command
                    exc.queue_index = index
                    exc.session_id = handle.session_id
                    raise
                self._check_epoch(epoch, handle)
        if wait:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise self._handle_error(ExecutionTimeout, handle, "submit deadline expired")
            return handle.wait(remaining)
        return handle

    @staticmethod
    def _handle_error(kind: type[DobotError], handle: CommandHandle, message: str) -> DobotError:
        return kind(
            message,
            stage="accepted",
            command=handle.command,
            queue_index=handle.queue_index,
            session_id=handle.session_id,
        )

    def _check_handle(self, handle: CommandHandle, epoch: int) -> None:
        with self._state_lock:
            if (
                self._closed
                or handle.session_id != self._session_id
                or handle._state in (ExecutionState.INVALIDATED, ExecutionState.CANCELLED)
                or epoch != self._epoch
            ):
                raise self._handle_error(CommandInvalidated, handle, "handle/wait is invalidated")

    def _wait(self, handle: CommandHandle, timeout: float) -> CommandHandle:
        deadline = time.monotonic() + _duration(timeout)
        with self._state_lock:
            epoch = self._action_epoch()
            self._check_handle(handle, epoch)
            if handle._state == ExecutionState.COMPLETED:
                return handle
            if handle.queue_index is None:
                raise self._handle_error(
                    OutcomeUnknown, handle, "immediate ACK cannot prove action completion"
                )
            handle._wake.clear()
        while True:
            self._check_handle(handle, epoch)
            if time.monotonic() >= deadline:
                raise self._handle_error(
                    ExecutionTimeout, handle, "execution wait timed out; no replay"
                )
            try:
                with self._locked(deadline):
                    self._check_handle(handle, epoch)
                    alarm_data = self._exchange(20)
                    if len(alarm_data) != 16:
                        self.close()
                        raise ProtocolError("alarm state must contain exactly 16 bytes")
                    alarms = tuple(
                        byte_index * 8 + bit
                        for byte_index, value in enumerate(alarm_data)
                        for bit in range(8)
                        if value & (1 << bit)
                    )
                    if alarms:
                        raise RobotAlarmError(
                            "controller alarms active while waiting",
                            alarms=alarms,
                            stage="accepted",
                            command=handle.command,
                            queue_index=handle.queue_index,
                            session_id=handle.session_id,
                        )
                    if time.monotonic() >= deadline:
                        raise self._handle_error(
                            ExecutionTimeout, handle, "execution deadline expired after alarm query"
                        )
                    self._check_handle(handle, epoch)
                    index = self._index()
                    self._check_handle(handle, epoch)
                    if time.monotonic() >= deadline:
                        raise self._handle_error(
                            ExecutionTimeout, handle, "execution deadline expired"
                        )
                    assert handle._baseline is not None and handle.queue_index is not None
                    progress = (index - handle._baseline) % _MODULUS
                    target = (handle.queue_index - handle._baseline) % _MODULUS
                    if progress < _HALF and progress >= target:
                        with self._state_lock:
                            self._check_handle(handle, epoch)
                            handle._state = ExecutionState.COMPLETED
                            if (
                                self._tail is not None
                                and self._tail.queue_index == handle.queue_index
                            ):
                                self._tail._state = ExecutionState.COMPLETED
                        return handle
            except DobotError as exc:
                exc.stage = "accepted"
                exc.command = handle.command
                exc.queue_index = handle.queue_index
                exc.session_id = handle.session_id
                raise
            remaining = deadline - time.monotonic()
            if remaining > 0:
                handle._wake.wait(min(self._poll_interval, remaining))

    def synchronize(self, timeout: float = _CONTROL_TIMEOUT) -> None:
        """Observe all queued actions acknowledged up to this call's snapshot."""
        deadline = time.monotonic() + _duration(timeout)
        with self.sequence():
            # Serialize with in-flight submissions so their ACK has been registered.
            with self._locked(deadline), self._state_lock:
                epoch = self._action_epoch()
                self._check_epoch(epoch)
                tail = self._tail
                if tail is None:
                    return
                pending = tuple(
                    h
                    for h in self._handles
                    if h.queue_index is not None and h._state == ExecutionState.ACCEPTED
                )
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise self._handle_error(ExecutionTimeout, tail, "queue synchronization timed out")
            tail.wait(remaining)
            with self._state_lock:
                self._check_handle(tail, epoch)
                for handle in pending:
                    if handle._state == ExecutionState.ACCEPTED:
                        handle._state = ExecutionState.COMPLETED
                        handle._wake.set()

    def prepare_queue(self) -> None:
        """Discard work left by an earlier connection before the first action."""
        with self._locked(time.monotonic() + _CONTROL_TIMEOUT):
            with self._state_lock:
                epoch = self._action_epoch()
                self._check_epoch(epoch)
            if not self._queue_cleared:
                self._exchange(242, write=True)
                self._exchange(245, write=True)
                self._last_index = None
            self._check_epoch(epoch)

    def start(self, *, timeout: float = _CONTROL_TIMEOUT) -> None:
        """Send an explicit START even if this session previously started the queue."""
        deadline = time.monotonic() + _duration(timeout)
        with self._state_lock:
            epoch = self._action_epoch()
        with self._locked(deadline):
            self._check_epoch(epoch)
            self._exchange(240, write=True)

    def _interrupt_waits(self) -> None:
        self._epoch += 1
        for handle in self._handles:
            handle._wake.set()

    def _lifecycle(
        self, command: int, *, clear: bool = False, timeout: float = _CONTROL_TIMEOUT
    ) -> None:
        deadline = time.monotonic() + _duration(timeout)
        with self._state_lock:
            self._ensure_open()
            self._stopping += 1
            self._interrupt_waits()
            if clear:
                self._invalidate()
        try:
            with self._locked(deadline):
                self._exchange(command, write=True)
                if clear and command != 245:
                    self._exchange(245, write=True)
                if clear:
                    with self._state_lock:
                        self._last_index = self._tail = None
        except BaseException:
            # A failed stop/clear cannot leave an apparently usable session.
            self.close()
            raise
        finally:
            with self._state_lock:
                self._stopping -= 1

    def pause(self, *, timeout: float = _CONTROL_TIMEOUT) -> None:
        """Queue stop (241); accepted handles can be waited again after start."""
        self._lifecycle(241, timeout=timeout)

    def clear(self, *, timeout: float = _CONTROL_TIMEOUT) -> None:
        """Explicit queue clear (245), invalidating all extant handles."""
        self._lifecycle(245, clear=True, timeout=timeout)

    def stop(self, *, clear: bool = True, timeout: float = _CONTROL_TIMEOUT) -> None:
        """Force-stop (242), optionally followed by explicit queue clear (245)."""
        if type(clear) is not bool:
            raise TypeError("clear must be bool")
        self._lifecycle(242, clear=clear, timeout=timeout)
