from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse

from inst.admin import get_admin_subject
from inst.config import settings
from inst.repository import InstructionNotFoundError, InstructionRepository
from inst.service import _to_response

STATIC_DIR = Path(__file__).resolve().parent / "static"
_UI_NO_CACHE_HEADERS = {"Cache-Control": "no-cache"}

router = APIRouter(tags=["ui"])


@router.get("/ui")
@router.get("/ui/")
async def ui_index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html", headers=_UI_NO_CACHE_HEADERS)


@router.get("/ui/instructions/{instruction_id}")
async def ui_instruction_detail(instruction_id: str) -> FileResponse:
    return FileResponse(STATIC_DIR / "instruction.html", headers=_UI_NO_CACHE_HEADERS)


@router.get("/api/ui/instructions")
async def ui_list_instructions(
    owning_lob: str | None = Query(default=None),
    status: str | None = Query(default=None),
    limit: int = Query(default=settings.ui_initial_instruction_limit, ge=1, le=500),
    _admin=Depends(get_admin_subject),
) -> dict:
    repository = InstructionRepository()
    records = await repository.list_current(
        owning_lob=owning_lob,
        status=status,
        limit=limit,
    )
    instructions = [
        _to_response(item).model_dump(mode="json", by_alias=True)
        for item in records
    ]
    return {"instructions": instructions, "count": len(instructions)}


@router.get("/api/ui/instructions/{instruction_id}")
async def ui_get_instruction(instruction_id: str, _admin=Depends(get_admin_subject)) -> dict:
    repository = InstructionRepository()
    try:
        record = await repository.get_current(instruction_id)
    except InstructionNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail=f"instruction not found: {instruction_id}",
        ) from exc
    instruction = _to_response(record).model_dump(mode="json", by_alias=True)
    return {"instruction": instruction}
