from __future__ import annotations
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, Query

from app.config import get_config
from app.opensearch_client import build_client, fetch_all
from app.grafana_response import table_response, failures_columns
from app.mac_utils import normalise_mac, InvalidMacError
from app.reason_codes import lookup as rc_lookup

router = APIRouter()


def _default_times(from_str, to_str):
    now = datetime.now(timezone.utc)
    from_dt = datetime.fromisoformat(from_str.replace("Z", "+00:00")) if from_str else now - timedelta(hours=24)
    to_dt = datetime.fromisoformat(to_str.replace("Z", "+00:00")) if to_str else now
    return from_dt, to_dt


@router.get("/failures")
def get_failures(
    user: Optional[str] = Query(None),
    from_: Optional[str] = Query(None, alias="from"),
    to: Optional[str] = Query(None),
    reason: Optional[int] = Query(None),
    limit: int = Query(500, ge=1, le=2000),
    offset: int = Query(0, ge=0),
):
    cfg = get_config()
    client = build_client(cfg)
    f = cfg.fields
    from_dt, to_dt = _default_times(from_, to)

    must: list = [
        {"range": {f.timestamp: {"gte": from_dt.isoformat(), "lte": to_dt.isoformat()}}},
        {"exists": {"field": f.prefixed("reason_code")}},
    ]
    must_not: list = [
        {"term": {f.prefixed("reason_code"): "0"}},
        {"term": {f.prefixed("reason_code"): 0}},
    ]

    if user:
        must.append({"wildcard": {f.prefixed("username"): {"value": f"*{user}*", "case_insensitive": True}}})
    if reason is not None:
        must.append({"term": {f.prefixed("reason_code"): str(reason)}})

    query = {"bool": {"must": must, "must_not": must_not}}
    sort = [{f.timestamp: "desc"}, {"_id": "asc"}]
    docs = fetch_all(client, cfg.opensearch.index, query, sort, max_hits=min(10 * (offset + limit), 10_000))

    p = f.prefix
    rows = []
    for doc in docs[offset: offset + limit]:
        src = doc.get("_source", {})
        raw_mac = src.get(p + f.calling_station_id)
        try:
            mac = normalise_mac(raw_mac) if raw_mac else None
        except InvalidMacError:
            mac = raw_mac

        reason_code_raw = src.get(p + f.reason_code)
        reason_code = int(reason_code_raw) if reason_code_raw is not None else None

        rows.append([
            src.get(f.timestamp),
            src.get(p + f.username),
            mac,
            src.get(p + f.nas_name),
            src.get(p + f.nas_ip),
            reason_code,
            rc_lookup(reason_code),
        ])

    return table_response(failures_columns(), rows)
