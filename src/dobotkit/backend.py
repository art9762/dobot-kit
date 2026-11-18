"""Connect pure command schemas to the session-aware executor."""

from collections.abc import Callable

from .commands import COMMANDS, CommandSpec
from .errors import ProtocolError, UnsupportedCommand
from .execution import CommandExecutor, CommandHandle
from .validation import options

QUEUE_LIFECYCLE = {"queue_start", "queue_stop", "queue_force_stop", "queue_clear"}


class CommandBackend:
    def __init__(self, executor: CommandExecutor, require_motion_ready: Callable[[], None]) -> None:
        self.executor = executor
        self._require_motion_ready = require_motion_ready

    @staticmethod
    def spec(name: str) -> CommandSpec:
        if not isinstance(name, str) or name not in COMMANDS:
            raise UnsupportedCommand(f"Unknown command name: {name!r}", stage="not_sent")
        return COMMANDS[name]

    def read(self, name: str, /, **params: object) -> dict[str, object]:
        spec = self.spec(name)
        packet = spec.encode_get(**params)
        data = self.executor.read(spec.command, packet)
        reply = spec.decode(data)
        # EIO GETs echo the port. A different port is an unrelated reply.
        if "address" in params and reply["address"] != params["address"]:
            self.executor.close()
            raise ProtocolError(
                f"{name} requested EIO {params['address']} but received EIO {reply['address']}",
                command=spec.command,
            )
        return reply

    def write(
        self,
        name: str,
        /,
        *,
        wait: bool = True,
        timeout: float = 30.0,
        queued: bool | None = None,
        **params: object,
    ) -> CommandHandle:
        options(wait, timeout)
        spec = self.spec(name)
        packet = spec.encode_set(**params)
        if queued is not None and type(queued) is not bool:
            raise ValueError("queued must be bool or None")
        queue = spec.default_queued if queued is None else queued
        if queue and not spec.queueable:
            raise UnsupportedCommand(f"{name} cannot be queued", stage="not_sent")
        if name in QUEUE_LIFECYCLE:
            raise UnsupportedCommand("Use robot.queue lifecycle methods", stage="not_sent")
        with self.executor.sequence():
            if name == "ptp_with_l":
                self._require_motion_ready()
            # An immediate SET has an ACK, no observable execution index. Do not turn
            # the normal convenience call into an invented completion claim.
            return self.executor.submit(
                spec.command, packet, queued=queue, wait=wait if queue else False, timeout=timeout
            )
