from fastapi import APIRouter, Depends, Header, HTTPException, Query

from ps.dependencies import get_compliance_subject, get_subject
from ps.ilm_client import InstructionNotFoundError
from ps.models.api import (
    CreatePaymentRequest,
    PaymentEligibleApproversResponse,
    PaymentResponse,
    RejectPaymentRequest,
    Subject,
)
from ps.models.payment import Payment
from ps.service import PaymentService

router = APIRouter(prefix="/payments", tags=["payments"])


def get_service() -> PaymentService:
    return PaymentService()


def _bearer_token(authorization: str | None) -> str | None:
    if authorization and authorization.lower().startswith("bearer "):
        return authorization.split(" ", 1)[1].strip()
    return None


def _to_response(p: Payment) -> PaymentResponse:
    return PaymentResponse(
        payment_id=p.payment_id,
        instruction_id=p.instruction_id,
        instruction_version=p.instruction_version,
        status=p.status.value,
        amount=p.amount,
        currency=p.currency,
        value_date=p.value_date,
        owning_lob=p.owning_lob,
        instruction_type=p.instruction_type,
        created_by=p.created_by,
        submitted_by=p.submitted_by,
        approved_by=p.approved_by,
        rejected_by=p.rejected_by,
        cancelled_by=p.cancelled_by,
        rejection_reason=p.rejection_reason,
        cancellation_reason=p.cancellation_reason,
        created_at=p.created_at.isoformat(),
        updated_at=p.updated_at.isoformat(),
        lifecycle_events=p.lifecycle_events,
    )


@router.post("", response_model=PaymentResponse, status_code=201)
async def create_payment(
    request: CreatePaymentRequest,
    subject: Subject = Depends(get_subject),
    service: PaymentService = Depends(get_service),
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_session_id: str | None = Header(default=None, alias="X-Session-Id"),
) -> PaymentResponse:
    try:
        payment = await service.create(
            instruction_id=request.instruction_id,
            value_date=request.value_date,
            amount=request.amount,
            subject=subject,
            bearer_token=_bearer_token(authorization),
            session_id=x_session_id,
        )
        return _to_response(payment)
    except InstructionNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("", response_model=list[PaymentResponse])
async def list_payments(
    instruction_id: str | None = Query(default=None),
    status: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    subject: Subject = Depends(get_subject),
    service: PaymentService = Depends(get_service),
) -> list[PaymentResponse]:
    payments = await service.list(
        subject,
        instruction_id=instruction_id,
        status=status,
        limit=limit,
    )
    return [_to_response(p) for p in payments]


@router.get("/{payment_id}", response_model=PaymentResponse)
async def get_payment(
    payment_id: str,
    subject: Subject = Depends(get_subject),
    service: PaymentService = Depends(get_service),
) -> PaymentResponse:
    try:
        return _to_response(await service.get(payment_id, subject))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.post("/{payment_id}/submit", response_model=PaymentResponse)
async def submit_payment(
    payment_id: str,
    subject: Subject = Depends(get_subject),
    service: PaymentService = Depends(get_service),
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_session_id: str | None = Header(default=None, alias="X-Session-Id"),
) -> PaymentResponse:
    try:
        return _to_response(
            await service.submit(
                payment_id,
                subject,
                bearer_token=_bearer_token(authorization),
                session_id=x_session_id,
            )
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{payment_id}/approve", response_model=PaymentResponse)
async def approve_payment(
    payment_id: str,
    subject: Subject = Depends(get_subject),
    service: PaymentService = Depends(get_service),
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_session_id: str | None = Header(default=None, alias="X-Session-Id"),
) -> PaymentResponse:
    try:
        return _to_response(
            await service.approve(
                payment_id,
                subject,
                bearer_token=_bearer_token(authorization),
                session_id=x_session_id,
            )
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{payment_id}/reject", response_model=PaymentResponse)
async def reject_payment(
    payment_id: str,
    request: RejectPaymentRequest,
    subject: Subject = Depends(get_subject),
    service: PaymentService = Depends(get_service),
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_session_id: str | None = Header(default=None, alias="X-Session-Id"),
) -> PaymentResponse:
    try:
        return _to_response(
            await service.reject(
                payment_id,
                subject,
                request,
                bearer_token=_bearer_token(authorization),
                session_id=x_session_id,
            )
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{payment_id}/eligible-approvers", response_model=PaymentEligibleApproversResponse)
async def payment_eligible_approvers(
    payment_id: str,
    _subject: Subject = Depends(get_compliance_subject),
    service: PaymentService = Depends(get_service),
) -> PaymentEligibleApproversResponse:
    try:
        data = await service.eligible_approvers(payment_id)
        return PaymentEligibleApproversResponse.model_validate(data)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InstructionNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
