"""Inspect a sliced Magician print and open it in an installed printer host."""

import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from .printer import Printer as Printer
from .printer import PrinterError as PrinterError
from .printer import PrintResult as PrintResult
from .printer import Temperature as Temperature
from .printing_file import source_lines

_OPCODE = re.compile(rb"(?:N[0-9]+[ \t]*)?([GMT])0*([0-9]+(?:\.[0-9]+)?)", re.IGNORECASE)
_MOVE_WORD = re.compile(rb"([A-Z])[ \t]*[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)", re.IGNORECASE)


@dataclass(frozen=True)
class PrintJob:
    """Counts from the file. Extruder moves include retractions and priming."""

    path: Path
    commands: int
    motion_commands: int
    extruder_moves: int


def inspect_gcode(path: str | Path) -> PrintJob:
    """Read a plain-text .gcode file produced by a slicer, without connecting."""
    source = Path(path).expanduser().resolve(strict=True)
    commands = motions = extruder_moves = 0
    for line_number, code in source_lines(source):
        opcode = _OPCODE.match(code)
        if opcode is None:
            raise ValueError(f"Expected a G, M or T command on line {line_number}")
        commands += 1
        if opcode[1].upper() != b"G" or opcode[2] not in (b"0", b"1", b"2", b"3"):
            continue
        # A feed-only G1 sets a rate without moving any axis.
        params = code[opcode.end() :].split(b"*", 1)[0].upper()
        if _MOVE_WORD.sub(b"", params).strip():
            raise ValueError(f"Malformed move parameters on G-code line {line_number}")
        words = set(_MOVE_WORD.findall(params))
        if words.intersection((b"X", b"Y", b"Z", b"E", b"I", b"J", b"K", b"R")):
            motions += 1
            extruder_moves += int(b"E" in words)
    if not motions:
        raise ValueError("No G0/G1/G2/G3 moves found in the sliced G-code")
    return PrintJob(source, commands, motions, extruder_moves)


def open_print_job(path: str | Path, *, application: str | Path) -> PrintJob:
    """Inspect a file and ask the named local host to open it for preview/printing."""
    job = inspect_gcode(path)
    host = Path(application).expanduser().resolve(strict=True)
    if sys.platform == "darwin":
        if host.suffix.lower() != ".app" or not host.is_dir():
            raise ValueError("Choose the installed Cura .app bundle on macOS")
        subprocess.run(["/usr/bin/open", "-a", str(host), str(job.path)], check=True)
    else:
        if not host.is_file() or not os.access(host, os.X_OK):
            raise ValueError("Choose the installed printer host executable")
        if sys.platform == "win32" and host.suffix.lower() != ".exe":
            raise ValueError("Choose the installed printer host .exe on Windows")
        subprocess.Popen([str(host), str(job.path)])
    return job
