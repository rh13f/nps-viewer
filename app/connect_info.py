from __future__ import annotations
import re
from dataclasses import dataclass
from typing import Optional


_PATTERN = re.compile(
    r"CONNECT\s+([\d.]+)\s+Mbps\s*/\s*([\w.]+)\s*/\s*RSSI:\s*(\d+)\s*/\s*Channel:\s*(\d+)",
    re.IGNORECASE,
)


@dataclass
class ConnectInfoFields:
    speed_mbps: Optional[float] = None
    standard: Optional[str] = None
    rssi: Optional[int] = None
    channel: Optional[int] = None


def parse_connect_info(value: Optional[str]) -> ConnectInfoFields:
    """Parse a RADIUS Connect-Info string into structured fields.

    Returns ConnectInfoFields with all None values if parsing fails.
    Never raises.
    """
    if not value:
        return ConnectInfoFields()
    m = _PATTERN.match(value.strip())
    if not m:
        return ConnectInfoFields()
    return ConnectInfoFields(
        speed_mbps=float(m.group(1)),
        standard=m.group(2),
        rssi=int(m.group(3)),
        channel=int(m.group(4)),
    )
