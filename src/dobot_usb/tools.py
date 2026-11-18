"""Explicit tool control. Reported bits do not prove physical gripping or emission."""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from .errors import ConnectionError, DobotError, ProtocolError
from .models import ToolState

if TYPE_CHECKING:
    from .execution import CommandHandle
    from .interfaces import Backend

ToolName = Literal["pen", "suction", "gripper", "laser"]


def _number(name: str, value: object, *, minimum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite number")
    try:
        result = float(value)
    except OverflowError:
        raise ValueError(f"{name} must be a finite number") from None
    if not math.isfinite(result) or (minimum is not None and result < minimum):
        raise ValueError(
            f"{name} must be finite and >= {minimum}"
            if minimum is not None
            else f"{name} must be finite"
        )
    return result


def _integer(name: str, value: object, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ValueError(f"{name} must be an integer in {low}..{high}")
    return value


def _boolean(name: str, value: object) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{name} must be a bool")
    return value


def _result_bool(data: dict[str, object], name: str) -> bool:
    value = data.get(name)
    if not isinstance(value, bool):
        raise ProtocolError(f"Expected bool field {name}")
    return value


def _result_number(data: dict[str, object], name: str) -> float:
    try:
        return _number(name, data.get(name))
    except ValueError as exc:
        raise ProtocolError(f"Expected finite number field {name}") from exc


class _Group:
    def __init__(self, backend: Backend) -> None:
        self._backend = backend

    def _write(
        self,
        command_name: str,
        /,
        *,
        wait: bool = True,
        timeout: float = 30.0,
        queued: bool | None = None,
        **params: object,
    ) -> CommandHandle:
        _boolean("wait", wait)
        seconds = _number("timeout", timeout, minimum=0)
        if seconds == 0:
            raise ValueError("timeout must be positive")
        return self._backend.write(
            command_name, wait=wait, timeout=seconds, queued=queued, **params
        )


class _Output(_Group):
    _command: str

    def state(self) -> ToolState:
        data = self._backend.read(self._command)
        return ToolState(_result_bool(data, "enabled"), _result_bool(data, "active"))

    def _set(self, enabled: bool, active: bool, *, wait: bool, timeout: float) -> CommandHandle:
        return self._write(
            self._command, enabled=enabled, active=active, wait=wait, timeout=timeout
        )


class Suction(_Output):
    _command = "suction"

    def on(self, *, wait: bool = True, timeout: float = 30.0) -> CommandHandle:
        return self._set(True, True, wait=wait, timeout=timeout)

    def off(self, *, wait: bool = True, timeout: float = 30.0) -> CommandHandle:
        return self._set(False, False, wait=wait, timeout=timeout)


class Gripper(_Output):
    _command = "gripper"

    def open(self, *, wait: bool = True, timeout: float = 30.0) -> CommandHandle:
        return self._set(True, False, wait=wait, timeout=timeout)

    def close(self, *, wait: bool = True, timeout: float = 30.0) -> CommandHandle:
        return self._set(True, True, wait=wait, timeout=timeout)

    def release(self, *, wait: bool = True, timeout: float = 30.0) -> CommandHandle:
        """Disable control; physical behavior must be checked on the fitted gripper."""
        return self._set(False, False, wait=wait, timeout=timeout)


class LaserNotArmed(DobotError):
    """Laser activation requires explicit permission in this connection session."""


class Laser(_Output):
    """Local permission only. The owner MUST invalidate on every link/session loss.

    The callback detects an inactive session at API boundaries. A boolean cannot detect
    an unobserved disconnect/reconnect, so reconnect must call invalidate explicitly.
    No method implicitly switches the physical output off on permission loss.
    """

    _command = "laser"

    def __init__(self, backend: Backend, *, is_session_active: Callable[[], bool]) -> None:
        super().__init__(backend)
        self._is_session_active = is_session_active
        self._armed = False

    def _session_active(self) -> bool:
        try:
            active = self._is_session_active()
        except Exception:
            self.invalidate()
            raise
        if not active:
            self.invalidate()
        return active

    @property
    def armed(self) -> bool:
        return self._session_active() and self._armed

    def arm(self) -> None:
        if not self._session_active():
            raise ConnectionError("Cannot arm laser outside an active session")
        self._armed = True

    def invalidate(self) -> None:
        self._armed = False

    def disarm(self) -> None:
        """Revoke local permission; use off() explicitly to request output shutdown."""
        self.invalidate()

    def close(self) -> None:
        """Session hook only; performs no IO."""
        self.invalidate()

    def _laser_set(
        self, enabled: bool, active: bool, *, wait: bool, timeout: float
    ) -> CommandHandle:
        try:
            # Immediate commands cannot remain in a queue after local disarm.
            return self._write(
                "laser", enabled=enabled, active=active, queued=False, wait=wait, timeout=timeout
            )
        except Exception:
            self.invalidate()
            raise

    def on(self, *, wait: bool = True, timeout: float = 30.0) -> CommandHandle:
        if not self.armed:
            raise LaserNotArmed("Call laser.arm() in the current active session first")
        return self._laser_set(True, True, wait=wait, timeout=timeout)

    def off(self, *, wait: bool = True, timeout: float = 30.0) -> CommandHandle:
        # Always available, including when permission is absent or the link is lost.
        # A disconnected backend may reject this request; never claim physical success.
        return self._laser_set(False, False, wait=wait, timeout=timeout)

    def state(self) -> ToolState:
        self._session_active()
        try:
            return super().state()
        except Exception:
            self.invalidate()
            raise


@dataclass(frozen=True)
class ToolProfile:
    """Locally selected tool. No measured geometry or default TCP is implied."""

    name: ToolName


@dataclass(frozen=True)
class ToolOffset:
    x: float
    y: float
    z: float


class Tool(_Group):
    def __init__(self, backend: Backend) -> None:
        super().__init__(backend)
        self._current: ToolProfile | None = None

    @property
    def current(self) -> ToolProfile | None:
        return self._current

    def select(self, name: ToolName) -> ToolProfile:
        """Select local metadata; does not write TCP or activate any output."""
        if name not in ("pen", "suction", "gripper", "laser"):
            raise ValueError("Tool must be pen, suction, gripper or laser")
        self._current = ToolProfile(name)
        return self._current

    def offset(
        self, x: float, y: float, z: float, *, wait: bool = True, timeout: float = 30.0
    ) -> CommandHandle:
        """Set controller TCP bias in mm. No unmeasured defaults are substituted."""
        values: dict[str, object] = {
            f"{key}_bias": _number(key, value) for key, value in (("x", x), ("y", y), ("z", z))
        }
        return self._write("end_effector_params", wait=wait, timeout=timeout, queued=None, **values)

    def get_offset(self) -> ToolOffset:
        data = self._backend.read("end_effector_params")
        return ToolOffset(*(_result_number(data, f"{axis}_bias") for axis in ("x", "y", "z")))
