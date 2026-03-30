from fastapi import APIRouter
router = APIRouter()

@router.get("/mac/{mac}")
def get_mac_sessions(mac: str):
    return []
