"""Read a slicer-exported text document without interpreting its command dialect."""

import re
from collections.abc import Iterator
from pathlib import Path

_COMMENTS = re.compile(rb"\([^()]*\)")
_CONTROL = re.compile(rb"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def source_lines(source: Path) -> Iterator[tuple[int, bytes]]:
    if source.suffix.lower() != ".gcode" or not source.is_file():
        raise ValueError("Expected a regular sliced .gcode file")
    with source.open("rb") as stream:
        for line_number, line in enumerate(stream, 1):
            if _CONTROL.search(line) or b"\r" in line.rstrip(b"\r\n"):
                raise ValueError(f"Binary or control data on G-code line {line_number}")
            if line_number == 1:
                line = line.removeprefix(b"\xef\xbb\xbf")
            if line.lstrip().startswith((b"@", b";@")):
                raise ValueError(
                    f"Host directive outside ordinary printer G-code on line {line_number}"
                )
            code = _COMMENTS.sub(b" ", line.split(b";", 1)[0]).strip()
            if code and code != b"%":
                yield line_number, code
