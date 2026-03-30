from fastapi import APIRouter
router = APIRouter()

@router.get("/failures")
def get_failures():
    return []
