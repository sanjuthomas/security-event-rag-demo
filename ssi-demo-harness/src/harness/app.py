from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Callable

import httpx
import uvicorn
from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from harness import actions
from harness.admin import get_admin_subject
from harness.auth_routes import router as auth_router
from harness.config import settings
from harness.dependencies import get_admin_session
from harness.helpers import (
    _count_payment_security_events,
    _count_security_events,
    _fetch_api_instructions,
    _fetch_api_payments,
)
from harness.models import Subject
from harness.zitadel_auth import SessionCredentials

__version__ = "0.1.0"
logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(
    title="Security Event Test Harness",
    description="Generate instruction lifecycle test data for end-to-end ETL runs",
    version=__version__,
)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.include_router(auth_router)


class CountRequest(BaseModel):
    count: int = Field(ge=1, le=500)


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "UP"}


@app.get("/api/status")
async def status(
    _admin: Subject = Depends(get_admin_subject),
    admin_session: SessionCredentials = Depends(get_admin_session),
) -> dict:
    instruction_counts: dict[str, int] = {}
    total_instructions = 0

    try:
        all_instructions = await asyncio.to_thread(
            _fetch_api_instructions, settings, admin_session
        )
        total_instructions = len(all_instructions)
        for instruction in all_instructions:
            status_name = instruction.get("status", "UNKNOWN")
            instruction_counts[status_name] = instruction_counts.get(status_name, 0) + 1
    except httpx.HTTPError as exc:
        logger.warning("failed to fetch instructions from ILM: %s", exc)

    payment_counts: dict[str, int] = {}
    total_payments = 0
    try:
        all_payments = await asyncio.to_thread(
            _fetch_api_payments, settings, admin_session
        )
        total_payments = len(all_payments)
        for payment in all_payments:
            status_name = payment.get("status", "UNKNOWN")
            payment_counts[status_name] = payment_counts.get(status_name, 0) + 1
    except httpx.HTTPError as exc:
        logger.warning("failed to fetch payments from payment-service: %s", exc)

    security_events = -1
    try:
        security_events = await asyncio.to_thread(_count_security_events, settings)
    except Exception as exc:
        logger.warning("failed to count security events: %s", exc)

    payment_security_events = -1
    try:
        payment_security_events = await asyncio.to_thread(
            _count_payment_security_events, settings
        )
    except Exception as exc:
        logger.warning("failed to count payment security events: %s", exc)

    return {
        "ilm_url": settings.ilm_url,
        "payment_service_url": settings.payment_service_url,
        "zitadel_configured": bool(settings.zitadel_service_pat),
        "instruction_total": total_instructions,
        "instruction_counts": instruction_counts,
        "payment_total": total_payments,
        "payment_counts": payment_counts,
        "security_event_count": security_events,
        "payment_security_event_count": payment_security_events,
    }


_COUNT_ACTIONS: dict[str, Callable[..., object]] = {
    "create-instructions": actions.create_instructions,
    "submit-instructions": actions.submit_instructions,
    "approve-instructions": actions.approve_instructions,
    "reject-instructions": actions.reject_instructions,
    "suspend-instructions": actions.suspend_instructions,
    "reactivate-instructions": actions.reactivate_instructions,
    "create-payments": actions.create_payments,
    "submit-payments": actions.submit_payments,
    "approve-payments": actions.approve_payments,
    "reject-payments": actions.reject_payments,
}

_SCENARIO_ACTIONS: dict[str, Callable[..., object]] = {
    "run-policy-scenario": actions.run_policy_scenario,
    "run-payment-policy-scenario": actions.run_payment_policy_scenario,
}


async def _run_count_action(
    action_name: str,
    count: int,
    admin_session: SessionCredentials,
) -> dict:
    handler = _COUNT_ACTIONS.get(action_name)
    if handler is None:
        raise HTTPException(status_code=404, detail=f"unknown action: {action_name}")
    result = await asyncio.to_thread(handler, settings, count, admin_session)
    return result.to_dict()


async def _run_scenario_action(
    action_name: str,
    admin_session: SessionCredentials,
) -> dict:
    handler = _SCENARIO_ACTIONS.get(action_name)
    if handler is None:
        raise HTTPException(status_code=404, detail=f"unknown action: {action_name}")
    result = await asyncio.to_thread(handler, settings, admin_session)
    return result.to_dict()


def _count_route(action_name: str):
    async def route(
        request: CountRequest,
        _admin: Subject = Depends(get_admin_subject),
        admin_session: SessionCredentials = Depends(get_admin_session),
    ) -> dict:
        return await _run_count_action(action_name, request.count, admin_session)

    return route


def _scenario_route(action_name: str):
    async def route(
        _admin: Subject = Depends(get_admin_subject),
        admin_session: SessionCredentials = Depends(get_admin_session),
    ) -> dict:
        return await _run_scenario_action(action_name, admin_session)

    return route


app.post("/api/actions/create-instructions")(_count_route("create-instructions"))
app.post("/api/actions/submit-instructions")(_count_route("submit-instructions"))
app.post("/api/actions/approve-instructions")(_count_route("approve-instructions"))
app.post("/api/actions/reject-instructions")(_count_route("reject-instructions"))
app.post("/api/actions/suspend-instructions")(_count_route("suspend-instructions"))
app.post("/api/actions/reactivate-instructions")(_count_route("reactivate-instructions"))
app.post("/api/actions/create-payments")(_count_route("create-payments"))
app.post("/api/actions/submit-payments")(_count_route("submit-payments"))
app.post("/api/actions/approve-payments")(_count_route("approve-payments"))
app.post("/api/actions/reject-payments")(_count_route("reject-payments"))
app.post("/api/actions/run-policy-scenario")(_scenario_route("run-policy-scenario"))
app.post("/api/actions/run-payment-policy-scenario")(
    _scenario_route("run-payment-policy-scenario")
)


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
