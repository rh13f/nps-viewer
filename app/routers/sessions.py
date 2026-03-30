from __future__ import annotations
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, Query, HTTPException, Response

from app.config import get_config
from app.opensearch_client import build_client, fetch_all
from app.session_correlator import correlate_sessions, Session, STATUS_LABELS
from app.grafana_response import table_response, sessions_columns, session_events_columns
from app.reason_codes import lookup as rc_lookup
from app.connect_info import parse_connect_info

router = APIRouter()


def _build_time_range_query(
    from_dt: datetime,
    to_dt: datetime,
    timestamp_field: str,
    extra_must: Optional[list] = None,
) -> dict:
    must: list = [{"range": {timestamp_field: {"gte": from_dt.isoformat(), "lte": to_dt.isoformat()}}}]
    if extra_must:
        must.extend(extra_must)
    return {"bool": {"must": must}}


def _default_times(from_str, to_str):
    now = datetime.now(timezone.utc)
    from_dt = datetime.fromisoformat(from_str.replace("Z", "+00:00")) if from_str else now - timedelta(hours=24)
    to_dt = datetime.fromisoformat(to_str.replace("Z", "+00:00")) if to_str else now
    return from_dt, to_dt


def _session_row(s: Session) -> list:
    return [
        s.session_id, s.username, s.mac, s.ap_name, s.ap_ip, s.client_ip,
        s.start_time, s.end_time, s.duration_seconds,
        s.data_in_bytes, s.data_out_bytes,
        s.speed_mbps, s.standard, s.rssi, s.channel, s.status,
    ]


@router.get("/sessions")
def get_sessions(
    response: Response,
    user: Optional[str] = Query(None),
    from_: Optional[str] = Query(None, alias="from"),
    to: Optional[str] = Query(None),
    limit: int = Query(500, ge=1, le=2000),
    offset: int = Query(0, ge=0),
):
    cfg = get_config()
    client = build_client(cfg)
    f = cfg.fields
    from_dt, to_dt = _default_times(from_, to)

    must: list = [{"range": {f.timestamp: {"gte": from_dt.isoformat(), "lte": to_dt.isoformat()}}}]
    if user:
        must.append({"wildcard": {f.prefixed("username"): {"value": f"*{user}*", "case_insensitive": True}}})

    query = {"bool": {"must": must}}
    sort = [{f.timestamp: "asc"}, {"_id": "asc"}]

    raw_cap = min(10 * (offset + limit), 10_000)
    docs = fetch_all(client, cfg.opensearch.index, query, sort, max_hits=raw_cap)

    sessions = correlate_sessions(docs, f, cfg.session.active_threshold_minutes)
    total = len(sessions)
    page = sessions[offset: offset + limit]

    response.headers["X-NPS-Total"] = str(total)
    response.headers["X-NPS-Offset"] = str(offset)
    response.headers["X-NPS-Limit"] = str(limit)

    rows = [_session_row(s) for s in page]
    return table_response(sessions_columns(), rows)


@router.get("/sessions/{session_id}/events")
def get_session_events(session_id: str):
    cfg = get_config()
    client = build_client(cfg)
    f = cfg.fields

    query = {"term": {f.prefixed("session_id"): session_id}}
    sort = [{f.timestamp: "asc"}, {"_id": "asc"}]
    docs = fetch_all(client, cfg.opensearch.index, query, sort, max_hits=500)

    rows = []
    for doc in docs:
        src = doc.get("_source", {})
        p = f.prefix
        ts = src.get(f.timestamp)
        status_type_raw = src.get(p + f.acct_status_type)
        status_type = int(status_type_raw) if status_type_raw else None
        status_label = STATUS_LABELS.get(status_type, f"Type-{status_type}") if status_type else None

        ci = parse_connect_info(src.get(p + f.connect_info))
        reason_code_raw = src.get(p + f.reason_code)
        reason_code = int(reason_code_raw) if reason_code_raw is not None else None

        rows.append([
            ts,
            status_type,
            status_label,
            int(src.get(p + f.input_octets) or 0),
            int(src.get(p + f.output_octets) or 0),
            int(src.get(p + f.session_time) or 0),
            ci.rssi,
            ci.channel,
            ci.standard,
            ci.speed_mbps,
            reason_code,
            rc_lookup(reason_code),
        ])

    return table_response(session_events_columns(), rows)
