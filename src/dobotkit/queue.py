"""Explicit queue controls. No lifecycle operation runs during connection."""

from collections.abc import Callable

from .backend import CommandBackend
from .execution import CommandHandle
from .validation import number, options


class Queue:
    def __init__(
        self, backend: CommandBackend, *, on_invalidate: Callable[[], None] = lambda: None
    ) -> None:
        self._backend = backend
        self._executor = backend.executor
        self._on_invalidate = on_invalidate

    def index(self, *, timeout: float = 30.0) -> int:
        return self._executor.current_index(timeout=timeout)

    def start(self, *, timeout: float = 30.0) -> None:
        self._executor.start(timeout=timeout)

    def pause(self, *, timeout: float = 30.0) -> None:
        self._executor.pause(timeout=timeout)

    def clear(self, *, timeout: float = 30.0) -> None:
        options(True, timeout)
        try:
            self._executor.clear(timeout=timeout)
        finally:
            self._on_invalidate()

    def stop(self, *, clear: bool = True, timeout: float = 30.0) -> None:
        options(True, timeout)
        if type(clear) is not bool:
            raise ValueError("clear must be bool")
        try:
            self._executor.stop(clear=clear, timeout=timeout)
        finally:
            self._on_invalidate()

    def wait(self, *, timeout: float = 30.0) -> None:
        """Wait for this client's pending receipts, not an external program."""
        self._executor.synchronize(timeout)

    def delay(self, seconds: float, *, wait: bool = True, timeout: float = 30.0) -> CommandHandle:
        options(wait, timeout)
        duration = number(seconds, "seconds", minimum=0)
        milliseconds = round(duration * 1000)
        if milliseconds > 2**32 - 1:
            raise ValueError("delay does not fit the controller uint32 millisecond field")
        return self._backend.write("wait", timeout_ms=milliseconds, wait=wait, timeout=timeout)

    def trigger(
        self, *, wait: bool = True, timeout: float = 30.0, **params: object
    ) -> CommandHandle:
        """Wait on controller input; params follow advanced.describe('trigger')."""
        return self._backend.write("trigger", wait=wait, timeout=timeout, queued=True, **params)
