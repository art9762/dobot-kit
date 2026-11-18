"""Contracts between the small public facade and command groups."""

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from .execution import CommandHandle


class Backend(Protocol):
    def read(self, name: str, /, **params: object) -> dict[str, object]: ...

    def write(
        self,
        name: str,
        /,
        *,
        wait: bool = True,
        timeout: float = 30.0,
        queued: bool | None = None,
        **params: object,
    ) -> "CommandHandle": ...
