from __future__ import annotations
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, Query, HTTPException

from app.config import get_config
from app.opensearch_client import build_client, fetch_all
from app.session_correlator import correlate_sessions
from app.mac_utils import normalise_mac, InvalidMacError
from app.grafana_response import table_response, sessions_columns
from app.routers.sessions import _session_row

router = APIRouter()


@router.get("/mac/{mac}")
def get_mac_sessions(
    mac: str,
    from_: Optional[str] = Query(None, alias="from"),
    to: Optional[str] = Query(None),
):
    try:
        canonical_mac = normalise_mac(mac)
    except InvalidMacError as e:
        raise HTTPException(status_code=400, detail=str(e))

    cfg = get_config()
    client = build_client(cfg)
    f = cfg.fields
    now = datetime.now(timezone.utc)
    from_dt = datetime.fromisoformat(from_.replace("Z", "+00:00")) if from_ else now - timedelta(days=30)
    to_dt = datetime.fromisoformat(to.replace("Z", "+00:00")) if to else now

    query = {
        "bool": {
            "must": [
                {"range": {f.timestamp: {"gte": from_dt.isoformat(), "lte": to_dt.isoformat()}}},
                {"term": {f.prefixed("calling_station_id"): canonical_mac}},
            ]
        }
    }
    sort = [{f.timestamp: "asc"}, {"_id": "asc"}]
    docs = fetch_all(client, cfg.opensearch.index, query, sort, max_hits=10_000)
    sessions = correlate_sessions(docs, f, cfg.session.active_threshold_minutes)
    rows = [_session_row(s) for s in sessions]
    return table_response(sessions_columns(), rows)
