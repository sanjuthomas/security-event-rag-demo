from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from harness import actions
from harness.config import Settings
from harness.helpers import _count_security_events, _fetch_ui_instructions

__version__ = "0.1.0"
logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent / "static"
settings = Settings()

app = FastAPI(
    title="Security Event Test Harness",
    description="Generate instruction lifecycle test data for end-to-end ETL runs",
    version=__version__,
)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


class CountRequest(BaseModel):
    count: int = Field(ge=1, le=500)


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "UP"}


@app.get("/api/status")
async def status() -> dict:
    instruction_counts: dict[str, int] = {}
    total_instructions = 0

    try:
        all_instructions = await asyncio.to_thread(_fetch_ui_instructions, settings)
        total_instructions = len(all_instructions)
        for instruction in all_instructions:
            status_name = instruction.get("status", "UNKNOWN")
            instruction_counts[status_name] = instruction_counts.get(status_name, 0) + 1
    except httpx.HTTPError as exc:
        logger.warning("failed to fetch instructions from ILM: %s", exc)

    security_events = -1
    try:
        security_events = await asyncio.to_thread(_count_security_events, settings)
    except Exception as exc:
        logger.warning("failed to count security events: %s", exc)

    return {
        "ilm_url": settings.ilm_url,
        "zitadel_configured": bool(settings.zitadel_service_pat),
        "instruction_total": total_instructions,
        "instruction_counts": instruction_counts,
        "security_event_count": security_events,
    }


async def _run_action(action_name: str, count: int) -> dict:
    action_map = {
        "create-instructions": actions.create_instructions,
        "submit-instructions": actions.submit_instructions,
        "approve-instructions": actions.approve_instructions,
        "reject-instructions": actions.reject_instructions,
    }
    handler = action_map.get(action_name)
    if handler is None:
        raise HTTPException(status_code=404, detail=f"unknown action: {action_name}")

    result = await asyncio.to_thread(handler, settings, count)
    return result.to_dict()


@app.post("/api/actions/create-instructions")
async def create_instructions(request: CountRequest) -> dict:
    return await _run_action("create-instructions", request.count)


@app.post("/api/actions/submit-instructions")
async def submit_instructions(request: CountRequest) -> dict:
    return await _run_action("submit-instructions", request.count)


@app.post("/api/actions/approve-instructions")
async def approve_instructions(request: CountRequest) -> dict:
    return await _run_action("approve-instructions", request.count)


@app.post("/api/actions/reject-instructions")
async def reject_instructions(request: CountRequest) -> dict:
    return await _run_action("reject-instructions", request.count)


@app.post("/api/actions/run-policy-scenario")
async def run_policy_scenario() -> dict:
    result = await asyncio.to_thread(actions.run_policy_scenario, settings)
    return result.to_dict()


def run() -> None:
    logging.basicConfig(level=logging.INFO)
    uvicorn.run(
        "harness.app:app",
        host=settings.host,
        port=settings.port,
        reload=False,
    )


if __name__ == "__main__":
    run()
