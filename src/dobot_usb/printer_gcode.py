"""Compile ordinary single-extruder slicer output to a validated ASCII command stream."""

import math
import re
from collections.abc import Iterator
from pathlib import Path

from .printing_file import source_lines

# A subset of the RepRap (Marlin/Sprinter) flavor used by the classic printer profile.
# An allowlist of parameter letters also prevents secondary opcodes on the same line.
_PARAMETERS = {
    "G0": "XYZEF",
    "G1": "XYZEF",
    "G2": "XYZEFIJKR",
    "G3": "XYZEFIJKR",
    "G4": "PS",
    "G21": "",
    "G90": "",
    "G91": "",
    "G92": "XYZE",
    "M82": "",
    "M83": "",
    "M84": "",
    "M104": "ST",
    "M109": "SRT",
    "M106": "S",
    "M107": "",
    "M140": "S",
    "M190": "S",
    "M400": "",
    "T0": "",
}
_OPCODE = re.compile(rb"([GMT])0*([0-9]+)")
_WORD = re.compile(rb"[ \t]*([A-Z])[ \t]*([+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+))")


def _compile(code: bytes) -> tuple[bytes, bool]:
    upper = code.upper()
    opcode = _OPCODE.match(upper)
    if opcode is None:
        raise ValueError("expected an unnumbered G/M/T command")
    name = (opcode[1] + opcode[2]).decode("ascii")
    if name == "M117":
        label = code[opcode.end() :]
        if not re.fullmatch(rb"(?:[ \t]+[A-Za-z0-9 ._/%-]+)?", label) or re.search(
            rb"\b[GMT][0-9]", label, re.IGNORECASE
        ):
            raise ValueError("M117 expects one plain display message")
        return b"M117" + (b" " + label.strip() if label else b""), False
    if name not in _PARAMETERS:
        raise ValueError(f"unsupported printer command {name}")
    args: dict[str, float] = {}
    words = [name.encode("ascii")]
    pos = opcode.end()
    while pos < len(code):
        match = _WORD.match(upper, pos)
        if match is None:
            raise ValueError(f"malformed parameter in {name}")
        letter = match[1].decode("ascii")
        if letter not in _PARAMETERS[name] or letter in args:
            raise ValueError(f"unexpected or duplicate {letter} in {name}")
        value = float(match[2])
        if not math.isfinite(value) or abs(value) > 3.402823466e38:
            raise ValueError(f"{letter} is outside the finite float32 range")
        args[letter] = value
        words.append(match[1] + match[2])
        pos = match.end()
    if "F" in args and args["F"] <= 0:
        raise ValueError("feed rate must be positive")
    if name in {"M104", "M109"}:
        targets = [args[key] for key in ("S", "R") if key in args]
        if len(targets) != 1 or not 0 <= targets[0] <= 250 or args.get("T", 0) != 0:
            raise ValueError("expected one hotend target in 0..250 C and tool 0")
    if name in {"M140", "M190"} and args.get("S") != 0:
        raise ValueError("the stock Magician profile supports only a zero bed target")
    if name == "M106" and "S" in args and not (args["S"].is_integer() and 0 <= args["S"] <= 255):
        raise ValueError("fan setting must be a whole number in 0..255")
    if name == "G4" and (len(args) != 1 or min(args.values()) < 0):
        raise ValueError("G4 needs a single nonnegative S or P duration")
    if name == "G92" and not args:
        raise ValueError("G92 needs explicit axes")
    wire = b" ".join(words)
    moved = name in {"G0", "G1", "G2", "G3"} and bool(args.keys() - {"F"})
    return wire, moved


def printer_commands(source: Path) -> Iterator[tuple[int, bytes]]:
    moved = False
    for line_number, code in source_lines(source):
        try:
            wire, is_move = _compile(code)
            if len(wire) + 1 > 63:
                raise ValueError("command exceeds the 63-byte printer host line budget")
        except ValueError as exc:
            raise ValueError(f"Invalid G-code on line {line_number}: {exc}") from exc
        moved |= is_move
        yield line_number, wire
    if not moved:
        raise ValueError("No G0/G1/G2/G3 moves found in the sliced G-code")
