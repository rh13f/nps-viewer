from fastapi import APIRouter
router = APIRouter()

@router.get("/aps")
def get_aps():
    return []
