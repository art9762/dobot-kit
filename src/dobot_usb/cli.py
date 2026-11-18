"""Read-only diagnostics for the classic Magician."""

import argparse
import json
from dataclasses import asdict

from .client import connect
from .commands import COMMANDS
from .discovery import ports
from .errors import DobotError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Dobot Kit diagnostics")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("ports", help="enumerate candidate ports without opening them")
    sub.add_parser("commands", help="list implemented protocol operations and known gaps")
    status = sub.add_parser("status", help="read device version, pose and alarms")
    port = status.add_mutually_exclusive_group(required=True)
    port.add_argument(
        "device", metavar="PORT", nargs="?", help="explicit port released by other applications"
    )
    port.add_argument("--port", help=argparse.SUPPRESS)
    status.add_argument("--timeout", type=float, default=1.0, help="per-query deadline in seconds")
    args = parser.parse_args(argv)
    try:
        if args.command == "commands":
            print(
                json.dumps(
                    [
                        {
                            "name": s.name,
                            "id": s.command,
                            "section": s.section,
                            "get": s.get_supported,
                            "set": s.set_supported,
                            "queueable": s.queueable,
                            "limitation": s.unsupported_reason,
                        }
                        for s in COMMANDS.values()
                    ],
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0
        if args.command == "ports":
            print(json.dumps([asdict(p) for p in ports()], ensure_ascii=False, indent=2))
            return 0
        device = args.port if args.device is None else args.device
        with connect(device, timeout=args.timeout, read_only=True) as bot:
            state = asdict(bot.status())
            state["device"]["firmware_raw"] = state["device"]["firmware_raw"].hex()
            print(json.dumps(state, ensure_ascii=False, indent=2))
    except (DobotError, ValueError) as exc:
        parser.exit(1, f"dobot: {exc}\n")
    return 0
