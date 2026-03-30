from fastapi import APIRouter
router = APIRouter()

@router.get("/reason-codes")
def get_reason_codes():
    return []
