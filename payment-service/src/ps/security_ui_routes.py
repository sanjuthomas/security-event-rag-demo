from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse

from ps.admin import get_admin_subject
from ps.config import settings
from ps.security_event_ui_store import SecurityEventUiStore

SECURITY_EVENTS_STATIC_DIR = Path(__file__).resolve().parent / "static" / "security_events"
_UI_NO_CACHE_HEADERS = {"Cache-Control": "no-cache"}

router = APIRouter(tags=["security-events-ui"])
security_event_ui_store = SecurityEventUiStore()


@router.get("/ui/security-events")
@router.get("/ui/security-events/")
async def security_events_index() -> FileResponse:
    return FileResponse(SECURITY_EVENTS_STATIC_DIR / "index.html", headers=_UI_NO_CACHE_HEADERS)


@router.get("/ui/security-events/events/{event_id}")
async def security_event_detail(event_id: str) -> FileResponse:
    return FileResponse(SECURITY_EVENTS_STATIC_DIR / "event.html", headers=_UI_NO_CACHE_HEADERS)


@router.get("/api/ui/security-events")
async def list_security_events(
    limit: int = Query(default=settings.ui_initial_security_event_limit, ge=1, le=1000),
    _admin=Depends(get_admin_subject),
) -> dict:
    events = await security_event_ui_store.list_recent(limit=limit)
    return {"events": events, "count": len(events)}


@router.get("/api/ui/security-events/{event_id}")
async def get_security_event(event_id: str, _admin=Depends(get_admin_subject)) -> dict:
    event = await security_event_ui_store.get_by_event_id(event_id)
    if event is None:
        raise HTTPException(status_code=404, detail=f"security event not found: {event_id}")
    return {"event": event}
