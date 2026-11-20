"""Validated, absolute tool paths. No hardware IO or workspace assumptions."""

from __future__ import annotations

import math
from collections.abc import Sequence

from .validation import number

Pose = tuple[float, float, float, float]


def coordinates(value: Sequence[float], name: str = "position") -> tuple[float, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValueError(f"{name} must contain three or four coordinates")
    if len(value) not in (3, 4):
        raise ValueError(f"{name} must contain three or four coordinates")
    return tuple(number(item, name) for item in value)


def positive(value: float, name: str) -> float:
    result = number(value, name)
    if result <= 0:
        raise ValueError(f"{name} must be greater than zero")
    return result


def planar_path(
    start: Sequence[float], offsets: Sequence[tuple[float, float]], plane: str
) -> list[tuple[float, ...]]:
    base = coordinates(start, "start")
    if plane not in ("xy", "xz", "yz"):
        raise ValueError("plane must be 'xy', 'xz' or 'yz'")
    first, second = (("xyz".index(axis)) for axis in plane)
    result = []
    for a, b in offsets:
        point = list(base)
        point[first] = number(base[first] + a, "path coordinate")
        point[second] = number(base[second] + b, "path coordinate")
        result.append(tuple(point))
    return result


def circle_path(
    start: Sequence[float], radius: float, segments: int, plane: str
) -> list[tuple[float, ...]]:
    radius = positive(radius, "radius")
    if type(segments) is not int or not 12 <= segments <= 720:
        raise ValueError("segments must be an integer from 12 to 720")
    # Start is on the circumference; centre is radius behind the first plane axis.
    offsets = [(0.0, 0.0)]
    offsets.extend(
        (
            radius * (math.cos(2 * math.pi * i / segments) - 1),
            radius * math.sin(2 * math.pi * i / segments),
        )
        for i in range(1, segments)
    )
    offsets.append((0.0, 0.0))
    return planar_path(start, offsets, plane)
