"""Read-only diagnostics for the classic Magician."""

import argparse
import json
from dataclasses import asdict

from .client import Dobot
from .commands import COMMANDS
from .discovery import discover
from .errors import DobotError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Dobot Kit diagnostics")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("ports", help="enumerate candidate ports without opening them")
    sub.add_parser("commands", help="list implemented protocol operations and known gaps")
    status = sub.add_parser("status", help="read device version, pose and alarms")
    status.add_argument(
        "--port", required=True, help="explicit port released by other applications"
    )
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
            print(json.dumps([asdict(p) for p in discover()], ensure_ascii=False, indent=2))
            return 0
        with Dobot.connect(args.port, timeout=args.timeout, read_only=True) as bot:
            state = asdict(bot.status())
            state["device"]["firmware_raw"] = state["device"]["firmware_raw"].hex()
            print(json.dumps(state, ensure_ascii=False, indent=2))
    except (DobotError, ValueError) as exc:
        parser.exit(1, f"dobot: {exc}\n")
    return 0
