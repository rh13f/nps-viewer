from fastapi import APIRouter, Query
from app.config import get_config
from app.opensearch_client import build_client, fetch_all

router = APIRouter()


@router.get("/live")
def get_live(limit: int = Query(100, ge=1, le=500)):
    cfg = get_config()
    client = build_client(cfg)
    f = cfg.fields

    query = {"match_all": {}}
    sort = [{f.timestamp: "desc"}, {"_id": "asc"}]
    docs = fetch_all(client, cfg.opensearch.index, query, sort, max_hits=limit)

    results = []
    for doc in docs:
        src = doc.get("_source", {})
        p = f.prefix
        results.append({
            "timestamp": src.get(f.timestamp),
            "username": src.get(p + f.username),
            "mac": src.get(p + f.calling_station_id),
            "ap_name": src.get(p + f.nas_name),
            "ap_ip": src.get(p + f.nas_ip),
            "client_ip": src.get(p + f.framed_ip),
            "status_type": src.get(p + f.acct_status_type),
            "reason_code": src.get(p + f.reason_code),
            "connect_info": src.get(p + f.connect_info),
        })
    return results
