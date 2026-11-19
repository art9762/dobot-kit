"""Public numeric validation before device IO."""

import math
import struct


def number(value: object, name: str, *, minimum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        raise ValueError(f"{name} must be a finite number")
    try:
        result = float(value)
        packed = struct.pack("<f", result)
    except (OverflowError, struct.error) as exc:
        raise ValueError(f"{name} is outside the float32 range") from exc
    if not math.isfinite(result) or not math.isfinite(struct.unpack("<f", packed)[0]):
        raise ValueError(f"{name} must be finite and fit float32")
    if minimum is not None and result < minimum:
        raise ValueError(f"{name} must be at least {minimum}")
    return result
