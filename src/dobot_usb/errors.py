"""Exceptions shared by the client, transport and executor."""

from __future__ import annotations

from typing import Literal

OutcomeStage = Literal["not_sent", "sent_unconfirmed", "accepted", "completed", "unknown"]


class DobotError(Exception):
    """Base error with machine-readable action outcome, never an execution claim.

    ``stage`` describes the affected action, not whether a recovery action worked.
    ``outcome`` is an alias for consumers preferring that terminology.
    """

    def __init__(
        self,
        message: str,
        *,
        stage: OutcomeStage = "unknown",
        command: int | None = None,
        queue_index: int | None = None,
        session_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.stage = stage
        self.command = command
        self.queue_index = queue_index
        self.session_id = session_id

    @property
    def outcome(self) -> OutcomeStage:
        return self.stage


class ProtocolError(DobotError):
    """A response is malformed or cannot be matched to the request."""


class ConnectionError(DobotError):
    """The connection is closed or an operation failed."""


class RequestTimeout(DobotError):
    """No complete response arrived; the command was not retried."""


class UnsupportedCommand(DobotError):
    """The command is unavailable under the selected transport policy."""


class ExecutionTimeout(DobotError):
    """An accepted command did not reach its index before the wait deadline."""


class CommandInvalidated(DobotError):
    """The handle belongs to an invalidated queue/session or a cancelled wait."""


class RobotAlarmError(DobotError):
    """Controller alarm bits were active while waiting; no implicit clear/stop."""

    def __init__(
        self,
        message: str,
        *,
        alarms: tuple[int, ...],
        stage: OutcomeStage = "accepted",
        command: int | None = None,
        queue_index: int | None = None,
        session_id: str | None = None,
    ) -> None:
        super().__init__(
            message,
            stage=stage,
            command=command,
            queue_index=queue_index,
            session_id=session_id,
        )
        self.alarms = alarms


class OutcomeUnknown(DobotError):
    """An immediate action was accepted, but completion cannot be observed."""
