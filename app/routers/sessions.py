from fastapi import APIRouter
router = APIRouter()

@router.get("/sessions")
def get_sessions():
    return []

@router.get("/sessions/{session_id}/events")
def get_session_events(session_id: str):
    return []
