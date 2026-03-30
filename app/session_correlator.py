from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Optional

from app.config import FieldsConfig
from app.connect_info import parse_connect_info
from app.mac_utils import normalise_mac, InvalidMacError

STATUS_LABELS = {1: "Start", 2: "Stop", 3: "Interim-Update"}


@dataclass
class RawEvent:
    timestamp: datetime
    status_type: int
    session_id: str
    username: Optional[str]
    mac: Optional[str]
    ap_ip: Optional[str]
    ap_name: Optional[str]
    client_ip: Optional[str]
    connect_info_raw: Optional[str]
    reason_code: Optional[int]
    input_octets: Optional[int]
    output_octets: Optional[int]
    session_time: Optional[int]


@dataclass
class Session:
    session_id: str
    username: Optional[str]
    mac: Optional[str]
    ap_name: Optional[str]
    ap_ip: Optional[str]
    client_ip: Optional[str]
    start_time: Optional[str]
    end_time: Optional[str]
    duration_seconds: Optional[int]
    data_in_bytes: Optional[int]
    data_out_bytes: Optional[int]
    connect_info_raw: Optional[str]
    speed_mbps: Optional[float]
    standard: Optional[str]
    rssi: Optional[int]
    channel: Optional[int]
    status: str


def _parse_int(val) -> Optional[int]:
    try:
        return int(val)
    except (TypeError, ValueError):
        return None


def _parse_dt(val: Optional[str]) -> Optional[datetime]:
    if not val:
        return None
    try:
        dt = datetime.fromisoformat(val.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, AttributeError):
        return None


def _extract_event(doc: dict, f: FieldsConfig) -> Optional[RawEvent]:
    src = doc.get("_source", {})
    p = f.prefix

    ts = _parse_dt(src.get(f.timestamp))
    if ts is None:
        return None

    status_type = _parse_int(src.get(p + f.acct_status_type))
    if status_type is None:
        return None

    session_id = src.get(p + f.session_id)
    if not session_id:
        return None

    raw_mac = src.get(p + f.calling_station_id)
    try:
        mac = normalise_mac(raw_mac) if raw_mac else None
    except InvalidMacError:
        mac = raw_mac  # keep as-is if normalisation fails

    return RawEvent(
        timestamp=ts,
        status_type=status_type,
        session_id=session_id,
        username=src.get(p + f.username),
        mac=mac,
        ap_ip=src.get(p + f.nas_ip),
        ap_name=src.get(p + f.nas_name),
        client_ip=src.get(p + f.framed_ip),
        connect_info_raw=src.get(p + f.connect_info),
        reason_code=_parse_int(src.get(p + f.reason_code)),
        input_octets=_parse_int(src.get(p + f.input_octets)),
        output_octets=_parse_int(src.get(p + f.output_octets)),
        session_time=_parse_int(src.get(p + f.session_time)),
    )


def correlate_sessions(
    docs: list[dict],
    fields: FieldsConfig,
    active_threshold_minutes: int = 30,
) -> list[Session]:
    """Group raw OpenSearch documents into correlated Session objects."""
    groups: dict[str, list[RawEvent]] = {}
    for doc in docs:
        event = _extract_event(doc, fields)
        if event is None:
            continue
        groups.setdefault(event.session_id, []).append(event)

    now = datetime.now(timezone.utc)
    threshold = timedelta(minutes=active_threshold_minutes)
    sessions = []

    for session_id, events in groups.items():
        events.sort(key=lambda e: e.timestamp)

        start_event = next((e for e in events if e.status_type == 1), None)
        stop_events = [e for e in events if e.status_type == 2]
        stop_event = stop_events[-1] if stop_events else None
        interim_events = [e for e in events if e.status_type == 3]
        last_interim = interim_events[-1] if interim_events else None

        representative = start_event or events[0]
        last_event = events[-1]

        # Determine status
        if stop_event:
            status = "closed"
        elif (now - last_event.timestamp) <= threshold:
            status = "active"
        else:
            status = "unknown"

        # Data counters: prefer stop, fall back to last interim
        data_source = stop_event or last_interim
        data_in = data_source.input_octets if data_source else None
        data_out = data_source.output_octets if data_source else None
        duration = data_source.session_time if data_source else None

        # Connect-Info parsing from representative event
        ci = parse_connect_info(representative.connect_info_raw)

        sessions.append(Session(
            session_id=session_id,
            username=representative.username,
            mac=representative.mac,
            ap_name=representative.ap_name,
            ap_ip=representative.ap_ip,
            client_ip=representative.client_ip,
            start_time=start_event.timestamp.isoformat() if start_event else None,
            end_time=stop_event.timestamp.isoformat() if stop_event else None,
            duration_seconds=duration,
            data_in_bytes=data_in,
            data_out_bytes=data_out,
            connect_info_raw=representative.connect_info_raw,
            speed_mbps=ci.speed_mbps,
            standard=ci.standard,
            rssi=ci.rssi,
            channel=ci.channel,
            status=status,
        ))

    return sessions
