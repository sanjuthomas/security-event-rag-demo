from __future__ import annotations

from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException
from platform_auth import LoginRequest, ZitadelLoginClient

from etl.config import settings

router = APIRouter(tags=["admin-auth"])


def _zitadel_base() -> str:
    base = settings.zitadel_internal_url or settings.oidc_internal_url or settings.oidc_issuer_url
    if not base:
        raise HTTPException(status_code=503, detail="ZITADEL is not configured")
    return base.rstrip("/")


def _host_header() -> str:
    if not settings.oidc_issuer_url:
        return ""
    return urlparse(settings.oidc_issuer_url).hostname or ""


@router.post("/api/auth/login")
async def admin_login(request: LoginRequest) -> dict[str, str]:
    if not settings.zitadel_service_pat:
        raise HTTPException(status_code=503, detail="ZITADEL service PAT not configured")
    client = ZitadelLoginClient(
        _zitadel_base(),
        settings.zitadel_service_pat,
        host_header=_host_header(),
    )
    try:
        session = client.login(request.user_id, request.password)
    except Exception as exc:
        detail = f"login failed: {exc}"
        if "404" in str(exc) and "sessions" in str(exc):
            detail = (
                "login failed: Zitadel session API returned 404 — "
                "check that zitadel-postgres and zitadel-api are healthy "
                "(docker compose ps zitadel-postgres zitadel-api)"
            )
        raise HTTPException(status_code=401, detail=detail) from exc
    return {
        "user_id": session.user_id,
        "session_id": session.session_id,
        "session_token": session.session_token,
    }
