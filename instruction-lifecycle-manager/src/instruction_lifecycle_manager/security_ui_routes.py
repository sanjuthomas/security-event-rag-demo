from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse

from instruction_lifecycle_manager.config import settings
from instruction_lifecycle_manager.security_event_broadcaster import SecurityEventBroadcaster
from instruction_lifecycle_manager.security_event_ui_store import SecurityEventUiStore

SECURITY_EVENTS_STATIC_DIR = Path(__file__).resolve().parent / "static" / "security_events"

router = APIRouter(tags=["security-events-ui"])
security_event_broadcaster = SecurityEventBroadcaster()
security_event_ui_store = SecurityEventUiStore()


@router.get("/ui/security-events")
@router.get("/ui/security-events/")
async def security_events_index() -> FileResponse:
    return FileResponse(SECURITY_EVENTS_STATIC_DIR / "index.html")


@router.get("/ui/security-events/events/{event_id}")
async def security_event_detail(event_id: str) -> FileResponse:
    return FileResponse(SECURITY_EVENTS_STATIC_DIR / "event.html")


@router.get("/api/ui/security-events")
async def list_security_events(
    limit: int = Query(default=settings.ui_initial_security_event_limit, ge=1, le=1000),
) -> dict:
    events = await security_event_ui_store.list_recent(limit=limit)
    return {"events": events, "count": len(events)}


@router.get("/api/ui/security-events/stream")
async def stream_security_events() -> StreamingResponse:
    async def event_generator():
        yield "event: connected\ndata: {}\n\n"
        async for event in security_event_broadcaster.subscribe():
            yield security_event_broadcaster.sse_payload(event)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/api/ui/security-events/{event_id}")
async def get_security_event(event_id: str) -> dict:
    event = await security_event_ui_store.get_by_event_id(event_id)
    if event is None:
        raise HTTPException(status_code=404, detail=f"security event not found: {event_id}")
    return {"event": event}
