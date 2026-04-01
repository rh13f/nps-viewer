from __future__ import annotations
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, Query

from app.config import get_config
from app.opensearch_client import build_client
from app.grafana_response import table_response, aps_columns

router = APIRouter()


@router.get("/aps")
def get_aps(
    from_: Optional[str] = Query(None, alias="from"),
    to: Optional[str] = Query(None),
):
    cfg = get_config()
    client = build_client(cfg)
    f = cfg.fields
    now = datetime.now(timezone.utc)
    from_dt = datetime.fromisoformat(from_.replace("Z", "+00:00")) if from_ else now - timedelta(hours=24)
    to_dt = datetime.fromisoformat(to.replace("Z", "+00:00")) if to else now

    body = {
        "query": {
            "range": {f.timestamp: {"gte": from_dt.isoformat(), "lte": to_dt.isoformat()}}
        },
        "size": 0,
        "aggs": {
            "per_ap": {
                "terms": {"field": f.prefixed("nas_name"), "size": 500},
                "aggs": {
                    "ap_ip": {"terms": {"field": f.prefixed("nas_ip"), "size": 1}},
                    "unique_sessions": {"cardinality": {"field": f.prefixed("session_id")}},
                    "unique_users": {"cardinality": {"field": f.prefixed("username")}},
                    "failures": {
                        "filter": {
                            "bool": {
                                "must": [{"exists": {"field": f.prefixed("reason_code")}}],
                                "must_not": [
                                    {"term": {f.prefixed("reason_code"): "0"}},
                                    {"term": {f.prefixed("reason_code"): 0}},
                                ],
                            }
                        }
                    }
                }
            }
        }
    }
    resp = client.search(index=cfg.opensearch.index, body=body)
    buckets = resp.get("aggregations", {}).get("per_ap", {}).get("buckets", [])

    rows = []
    for bucket in buckets:
        ap_name = bucket["key"]
        ap_ip_buckets = bucket.get("ap_ip", {}).get("buckets", [])
        ap_ip = ap_ip_buckets[0]["key"] if ap_ip_buckets else None
        total_sessions = bucket.get("unique_sessions", {}).get("value", 0)
        unique_users = bucket.get("unique_users", {}).get("value", 0)
        failure_count = bucket.get("failures", {}).get("doc_count", 0)
        total_events = bucket.get("doc_count", 0)
        failure_rate = round(failure_count / total_events * 100, 1) if total_events > 0 else 0.0
        rows.append([ap_name, ap_ip, total_sessions, unique_users, failure_count, failure_rate])

    rows.sort(key=lambda r: r[2], reverse=True)
    return table_response(aps_columns(), rows)
