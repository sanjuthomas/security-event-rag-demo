from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse

from ps.admin import get_admin_subject
from ps.repository import PaymentNotFoundError, PaymentRepository

STATIC_DIR = Path(__file__).resolve().parent / "static"

router = APIRouter(tags=["ui"])


@router.get("/ui")
@router.get("/ui/")
async def ui_index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@router.get("/ui/payments/{payment_id}")
async def ui_payment_detail(payment_id: str) -> FileResponse:
    return FileResponse(STATIC_DIR / "payment.html")


@router.get("/api/ui/payments")
async def ui_list_payments(
    status: str | None = Query(default=None),
    owning_lob: str | None = Query(default=None),
    instruction_id: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=500),
    _admin=Depends(get_admin_subject),
) -> dict:
    repo = PaymentRepository()
    payments = await repo.list(
        status=status,
        instruction_id=instruction_id.strip() if instruction_id else None,
        limit=limit,
    )
    if owning_lob:
        payments = [p for p in payments if p.owning_lob == owning_lob]
    return {
        "payments": [p.to_mongo() for p in payments],
        "count": len(payments),
    }


@router.get("/api/ui/payments/{payment_id}")
async def ui_get_payment(payment_id: str, _admin=Depends(get_admin_subject)) -> dict:
    repo = PaymentRepository()
    try:
        payment = await repo.find_by_id(payment_id)
    except PaymentNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail=f"payment not found: {payment_id}",
        ) from exc
    return {"payment": payment.to_mongo()}
