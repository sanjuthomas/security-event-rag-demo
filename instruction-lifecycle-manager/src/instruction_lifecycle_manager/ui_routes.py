from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse

from instruction_lifecycle_manager.config import settings
from instruction_lifecycle_manager.repository import InstructionNotFoundError, InstructionRepository
from instruction_lifecycle_manager.service import _to_response
from instruction_lifecycle_manager.ui_broadcaster import InstructionBroadcaster

STATIC_DIR = Path(__file__).resolve().parent / "static"

router = APIRouter(tags=["ui"])
instruction_broadcaster = InstructionBroadcaster()


@router.get("/ui")
@router.get("/ui/")
async def ui_index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@router.get("/ui/instructions/{instruction_id}")
async def ui_instruction_detail(instruction_id: str) -> FileResponse:
    return FileResponse(STATIC_DIR / "instruction.html")


@router.get("/api/ui/instructions")
async def ui_list_instructions(
    owning_lob: str | None = Query(default=None),
    status: str | None = Query(default=None),
    limit: int = Query(default=settings.ui_initial_instruction_limit, ge=1, le=500),
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


@router.get("/api/ui/instructions/stream")
async def ui_stream_instructions() -> StreamingResponse:
    async def event_generator():
        yield "event: connected\ndata: {}\n\n"
        async for instruction in instruction_broadcaster.subscribe():
            yield instruction_broadcaster.sse_payload(instruction)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/api/ui/instructions/{instruction_id}")
async def ui_get_instruction(instruction_id: str) -> dict:
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
