"""Typed query results. Cartesian coordinates are mm, angles are degrees."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Point:
    """A Cartesian target in millimetres, with tool rotation in degrees."""

    x: float
    y: float
    z: float
    r: float


@dataclass(frozen=True)
class Pose:
    x: float
    y: float
    z: float
    r: float
    joints: tuple[float, float, float, float]


@dataclass(frozen=True)
class DeviceInfo:
    firmware: str
    firmware_raw: bytes
    serial_number: str | None
    name: str | None


@dataclass(frozen=True)
class ToolState:
    enabled: bool
    active: bool


@dataclass(frozen=True)
class Status:
    """Sequential query results, not an atomic or continuously refreshed snapshot."""

    device: DeviceInfo
    pose: Pose
    alarms: tuple[int, ...]


@dataclass(frozen=True)
class PortInfo:
    device: str
    description: str
    vid: int | None
    pid: int | None
    serial_number: str | None
