"""Small high-level interface for classic DOBOT Magician robots."""

from .client import Dobot, connect
from .commands import CommandSpec
from .discovery import discover, ports
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
from .printer import Printer
from .tools import LaserNotArmed

__version__ = "0.2.0a4"
__all__ = [
    "Dobot",
    "connect",
    "ports",
    "discover",
    "Printer",
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
