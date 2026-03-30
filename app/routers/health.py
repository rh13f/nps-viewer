from fastapi import APIRouter
from fastapi.responses import JSONResponse
from app.config import get_config
from app.opensearch_client import build_client, is_reachable

router = APIRouter()


@router.get("/health")
def health():
    cfg = get_config()
    client = build_client(cfg)
    if is_reachable(client):
        return {"status": "ok", "opensearch": "reachable"}
    return JSONResponse(
        status_code=503,
        content={"status": "error", "opensearch": "unreachable",
                 "detail": "Could not connect to OpenSearch. Check config.yaml."},
    )
