"""USB enumeration only: this module never opens a serial device."""

from serial.tools import list_ports

from .models import PortInfo


def discover() -> list[PortInfo]:
    """List candidate USB serial devices; VID/PID is not proof of robot identity.

    Include the locally observed CH343 pair and common CH340/CP210x bridges.
    An explicit port can be used for devices with other bridges.
    """
    candidates = {(0x1A86, 0x55D4), (0x1A86, 0x7523), (0x10C4, 0xEA60)}
    return [
        PortInfo(p.device, p.description, p.vid, p.pid, p.serial_number)
        for p in list_ports.comports()
        if (p.vid, p.pid) in candidates
    ]


ports = discover
