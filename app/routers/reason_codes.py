from fastapi import APIRouter, Query
from datetime import datetime, timezone, timedelta
from typing import Optional

from app.config import get_config
from app.opensearch_client import build_client
from app.grafana_response import table_response, reason_codes_columns
from app.reason_codes import lookup as rc_lookup, REASON_CODES

router = APIRouter()


@router.get("/reason-codes")
def get_reason_codes(
    from_: Optional[str] = Query(None, alias="from"),
    to: Optional[str] = Query(None),
):
    """Return distinct reason codes present in OpenSearch + their descriptions."""
    cfg = get_config()
    client = build_client(cfg)
    f = cfg.fields
    now = datetime.now(timezone.utc)
    from_dt = datetime.fromisoformat(from_.replace("Z", "+00:00")) if from_ else now - timedelta(days=7)
    to_dt = datetime.fromisoformat(to.replace("Z", "+00:00")) if to else now

    body = {
        "query": {"range": {f.timestamp: {"gte": from_dt.isoformat(), "lte": to_dt.isoformat()}}},
        "size": 0,
        "aggs": {
            "distinct_codes": {
                "terms": {"field": f.prefixed("reason_code"), "size": 100}
            }
        }
    }
    resp = client.search(index=cfg.opensearch.index, body=body)
    buckets = resp.get("aggregations", {}).get("distinct_codes", {}).get("buckets", [])

    rows = []
    for bucket in buckets:
        code = bucket["key"]
        try:
            int_code = int(code)
        except (ValueError, TypeError):
            continue
        rows.append([int_code, rc_lookup(int_code)])

    rows.sort(key=lambda r: r[0])
    return table_response(reason_codes_columns(), rows)
