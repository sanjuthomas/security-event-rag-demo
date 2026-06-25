from fastapi import APIRouter, Depends, HTTPException, Query

from instruction_lifecycle_manager.dependencies import get_subject
from instruction_lifecycle_manager.models.api import (
    CreateInstructionRequest,
    DeleteInstructionRequest,
    InstructionResponse,
    RejectInstructionRequest,
    Subject,
    UpdateInstructionRequest,
    UseInstructionRequest,
)
from instruction_lifecycle_manager.repository import InstructionNotFoundError
from instruction_lifecycle_manager.service import InstructionService, InvalidStateTransitionError

router = APIRouter(prefix="/instructions", tags=["instructions"])


def get_service() -> InstructionService:
    return InstructionService()


@router.post("", response_model=InstructionResponse, status_code=201)
async def create_instruction(
    request: CreateInstructionRequest,
    subject: Subject = Depends(get_subject),
    service: InstructionService = Depends(get_service),
) -> InstructionResponse:
    try:
        return await service.create(request, subject)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("", response_model=list[InstructionResponse])
async def list_instructions(
    owning_lob: str | None = Query(default=None),
    status: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    subject: Subject = Depends(get_subject),
    service: InstructionService = Depends(get_service),
) -> list[InstructionResponse]:
    return await service.list(subject, owning_lob=owning_lob, status=status, limit=limit)


@router.put("/{instruction_id}", response_model=InstructionResponse)
async def update_instruction(
    instruction_id: str,
    request: UpdateInstructionRequest,
    subject: Subject = Depends(get_subject),
    service: InstructionService = Depends(get_service),
) -> InstructionResponse:
    return await _lifecycle_action(service.update, instruction_id, subject, request)


@router.post("/{instruction_id}/delete", response_model=InstructionResponse)
async def delete_instruction(
    instruction_id: str,
    request: DeleteInstructionRequest | None = None,
    subject: Subject = Depends(get_subject),
    service: InstructionService = Depends(get_service),
) -> InstructionResponse:
    return await _lifecycle_action(service.delete, instruction_id, subject, request)


@router.get("/{instruction_id}/versions", response_model=list[InstructionResponse])
async def list_instruction_versions(
    instruction_id: str,
    subject: Subject = Depends(get_subject),
    service: InstructionService = Depends(get_service),
) -> list[InstructionResponse]:
    try:
        return await service.list_versions(instruction_id, subject)
    except InstructionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="instruction not found") from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.get("/{instruction_id}", response_model=InstructionResponse)
async def get_instruction(
    instruction_id: str,
    subject: Subject = Depends(get_subject),
    service: InstructionService = Depends(get_service),
) -> InstructionResponse:
    try:
        return await service.get(instruction_id, subject)
    except InstructionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="instruction not found") from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.post("/{instruction_id}/submit", response_model=InstructionResponse)
async def submit_instruction(
    instruction_id: str,
    subject: Subject = Depends(get_subject),
    service: InstructionService = Depends(get_service),
) -> InstructionResponse:
    return await _lifecycle_action(service.submit, instruction_id, subject)


@router.post("/{instruction_id}/approve", response_model=InstructionResponse)
async def approve_instruction(
    instruction_id: str,
    subject: Subject = Depends(get_subject),
    service: InstructionService = Depends(get_service),
) -> InstructionResponse:
    return await _lifecycle_action(service.approve, instruction_id, subject)


@router.post("/{instruction_id}/reject", response_model=InstructionResponse)
async def reject_instruction(
    instruction_id: str,
    request: RejectInstructionRequest,
    subject: Subject = Depends(get_subject),
    service: InstructionService = Depends(get_service),
) -> InstructionResponse:
    return await _lifecycle_action(service.reject, instruction_id, subject, request)


@router.post("/{instruction_id}/suspend", response_model=InstructionResponse)
async def suspend_instruction(
    instruction_id: str,
    subject: Subject = Depends(get_subject),
    service: InstructionService = Depends(get_service),
) -> InstructionResponse:
    return await _lifecycle_action(service.suspend, instruction_id, subject)


@router.post("/{instruction_id}/reactivate", response_model=InstructionResponse)
async def reactivate_instruction(
    instruction_id: str,
    subject: Subject = Depends(get_subject),
    service: InstructionService = Depends(get_service),
) -> InstructionResponse:
    return await _lifecycle_action(service.reactivate, instruction_id, subject)


@router.post("/{instruction_id}/use", response_model=InstructionResponse)
async def use_instruction(
    instruction_id: str,
    request: UseInstructionRequest,
    subject: Subject = Depends(get_subject),
    service: InstructionService = Depends(get_service),
) -> InstructionResponse:
    return await _lifecycle_action(service.use, instruction_id, subject, request)


async def _lifecycle_action(handler, instruction_id: str, subject: Subject, *args):
    try:
        return await handler(instruction_id, subject, *args)
    except InstructionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="instruction not found") from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except InvalidStateTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
