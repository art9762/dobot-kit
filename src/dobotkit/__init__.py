"""Small high-level interface for classic DOBOT Magician robots."""

from .client import Dobot
from .commands import CommandSpec
from .discovery import discover
from .errors import (
    CommandInvalidated,
    ConnectionError,
    DobotError,
    ExecutionTimeout,
    OutcomeUnknown,
    ProtocolError,
    RequestTimeout,
    RobotAlarmError,
    UnsupportedCommand,
)
from .execution import CommandHandle, ExecutionState
from .models import DeviceInfo, Point, PortInfo, Pose, Status, ToolState
from .motion import PTPMode
from .paths import Bounds, Path, Preview
from .pen import PenCheckpoint, PenReport, pen_path
from .tools import LaserNotArmed

__version__ = "0.2.0a2"
__all__ = [
    "Dobot",
    "discover",
    "DobotError",
    "ConnectionError",
    "ProtocolError",
    "RequestTimeout",
    "UnsupportedCommand",
    "DeviceInfo",
    "PortInfo",
    "Pose",
    "Status",
    "ToolState",
    "Point",
    "PTPMode",
    "Path",
    "Bounds",
    "Preview",
    "pen_path",
    "PenCheckpoint",
    "PenReport",
    "CommandSpec",
    "CommandHandle",
    "ExecutionState",
    "ExecutionTimeout",
    "CommandInvalidated",
    "RobotAlarmError",
    "OutcomeUnknown",
    "LaserNotArmed",
]
