from fastapi import APIRouter
router = APIRouter()

@router.get("/live")
def get_live():
    return []
