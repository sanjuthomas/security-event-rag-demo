from fastapi import APIRouter, Depends, Header, HTTPException, Query

from inst.dependencies import get_compliance_subject, get_subject
from inst.models.api import (
    CreateInstructionRequest,
    InstructionEligibleApproversResponse,
    InstructionResponse,
    RejectInstructionRequest,
    Subject,
    UseInstructionRequest,
)
from inst.repository import ConcurrentModificationError, InstructionNotFoundError
from inst.service import InstructionService, InvalidStateTransitionError

router = APIRouter(prefix="/instructions", tags=["instructions"])


def get_service() -> InstructionService:
    return InstructionService()


def _bearer_token(authorization: str | None) -> str | None:
    if authorization and authorization.lower().startswith("bearer "):
        return authorization.split(" ", 1)[1].strip()
    return None


@router.post("", response_model=InstructionResponse, status_code=201)
async def create_instruction(
    request: CreateInstructionRequest,
    subject: Subject = Depends(get_subject),
    service: InstructionService = Depends(get_service),
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_session_id: str | None = Header(default=None, alias="X-Session-Id"),
) -> InstructionResponse:
    try:
        return await service.create(
            request,
            subject,
            bearer_token=_bearer_token(authorization),
            session_id=x_session_id,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("", response_model=list[InstructionResponse])
async def list_instructions(
    owning_lob: str | None = Query(default=None),
    status: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    subject: Subject = Depends(get_subject),
    service: InstructionService = Depends(get_service),
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_session_id: str | None = Header(default=None, alias="X-Session-Id"),
) -> list[InstructionResponse]:
    return await service.list(
        subject,
        owning_lob=owning_lob,
        status=status,
        limit=limit,
        bearer_token=_bearer_token(authorization),
        session_id=x_session_id,
    )


@router.get("/{instruction_id}/versions", response_model=list[InstructionResponse])
async def list_instruction_versions(
    instruction_id: str,
    subject: Subject = Depends(get_subject),
    service: InstructionService = Depends(get_service),
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_session_id: str | None = Header(default=None, alias="X-Session-Id"),
) -> list[InstructionResponse]:
    try:
        return await service.list_versions(
            instruction_id,
            subject,
            bearer_token=_bearer_token(authorization),
            session_id=x_session_id,
        )
    except InstructionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="instruction not found") from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.get("/{instruction_id}", response_model=InstructionResponse)
async def get_instruction(
    instruction_id: str,
    subject: Subject = Depends(get_subject),
    service: InstructionService = Depends(get_service),
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_session_id: str | None = Header(default=None, alias="X-Session-Id"),
) -> InstructionResponse:
    try:
        return await service.get(
            instruction_id,
            subject,
            bearer_token=_bearer_token(authorization),
            session_id=x_session_id,
        )
    except InstructionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="instruction not found") from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.post("/{instruction_id}/eligible-approvers", response_model=InstructionEligibleApproversResponse)
async def instruction_eligible_approvers(
    instruction_id: str,
    _subject: Subject = Depends(get_compliance_subject),
    service: InstructionService = Depends(get_service),
) -> InstructionEligibleApproversResponse:
    try:
        data = await service.eligible_approvers(instruction_id)
        return InstructionEligibleApproversResponse.model_validate(data)
    except InstructionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="instruction not found") from exc


@router.post("/{instruction_id}/submit", response_model=InstructionResponse)
async def submit_instruction(
    instruction_id: str,
    subject: Subject = Depends(get_subject),
    service: InstructionService = Depends(get_service),
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_session_id: str | None = Header(default=None, alias="X-Session-Id"),
) -> InstructionResponse:
    return await _lifecycle_action(
        service.submit,
        instruction_id,
        subject,
        bearer_token=_bearer_token(authorization),
        session_id=x_session_id,
    )


@router.post("/{instruction_id}/approve", response_model=InstructionResponse)
async def approve_instruction(
    instruction_id: str,
    subject: Subject = Depends(get_subject),
    service: InstructionService = Depends(get_service),
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_session_id: str | None = Header(default=None, alias="X-Session-Id"),
) -> InstructionResponse:
    return await _lifecycle_action(
        service.approve,
        instruction_id,
        subject,
        bearer_token=_bearer_token(authorization),
        session_id=x_session_id,
    )


@router.post("/{instruction_id}/reject", response_model=InstructionResponse)
async def reject_instruction(
    instruction_id: str,
    request: RejectInstructionRequest,
    subject: Subject = Depends(get_subject),
    service: InstructionService = Depends(get_service),
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_session_id: str | None = Header(default=None, alias="X-Session-Id"),
) -> InstructionResponse:
    return await _lifecycle_action(
        service.reject,
        instruction_id,
        subject,
        request,
        bearer_token=_bearer_token(authorization),
        session_id=x_session_id,
    )


@router.post("/{instruction_id}/suspend", response_model=InstructionResponse)
async def suspend_instruction(
    instruction_id: str,
    subject: Subject = Depends(get_subject),
    service: InstructionService = Depends(get_service),
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_session_id: str | None = Header(default=None, alias="X-Session-Id"),
) -> InstructionResponse:
    return await _lifecycle_action(
        service.suspend,
        instruction_id,
        subject,
        bearer_token=_bearer_token(authorization),
        session_id=x_session_id,
    )


@router.post("/{instruction_id}/reactivate", response_model=InstructionResponse)
async def reactivate_instruction(
    instruction_id: str,
    subject: Subject = Depends(get_subject),
    service: InstructionService = Depends(get_service),
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_session_id: str | None = Header(default=None, alias="X-Session-Id"),
) -> InstructionResponse:
    return await _lifecycle_action(
        service.reactivate,
        instruction_id,
        subject,
        bearer_token=_bearer_token(authorization),
        session_id=x_session_id,
    )


@router.post("/{instruction_id}/use", response_model=InstructionResponse)
async def use_instruction(
    instruction_id: str,
    request: UseInstructionRequest,
    subject: Subject = Depends(get_subject),
    service: InstructionService = Depends(get_service),
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_session_id: str | None = Header(default=None, alias="X-Session-Id"),
) -> InstructionResponse:
    return await _lifecycle_action(
        service.use,
        instruction_id,
        subject,
        request,
        bearer_token=_bearer_token(authorization),
        session_id=x_session_id,
    )


async def _lifecycle_action(
    handler,
    instruction_id: str,
    subject: Subject,
    *args,
    bearer_token: str | None = None,
    session_id: str | None = None,
):
    try:
        return await handler(
            instruction_id,
            subject,
            *args,
            bearer_token=bearer_token,
            session_id=session_id,
        )
    except InstructionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="instruction not found") from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except InvalidStateTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ConcurrentModificationError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
