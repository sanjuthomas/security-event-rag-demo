from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx

from security_event_qdrant_etl.config import settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SessionCredentials:
    session_id: str
    session_token: str


class InstructionClient:
    """Fetch current instructions from ILM via ZITADEL-authenticated API calls."""

    def __init__(self) -> None:
        self._http: httpx.AsyncClient | None = None
        self._session: SessionCredentials | None = None

    async def connect(self) -> None:
        self._http = httpx.AsyncClient(timeout=30.0)
        await self._login()
        logger.info(
            "instruction client connected ilm_url=%s reader=%s",
            settings.ilm_url,
            settings.etl_reader_login,
        )

    async def close(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None
        self._session = None

    def _zitadel_headers(self) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {settings.zitadel_service_pat}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if settings.zitadel_host_header:
            headers["Host"] = settings.zitadel_host_header
        return headers

    def _ilm_headers(self) -> dict[str, str]:
        if self._session is None:
            raise RuntimeError("instruction client not authenticated")
        return {
            "Authorization": f"Bearer {self._session.session_token}",
            "X-Session-Id": self._session.session_id,
            "Accept": "application/json",
        }

    async def _login(self) -> None:
        if self._http is None:
            raise RuntimeError("instruction client not connected")

        login_name = settings.etl_reader_login
        candidates = [login_name]
        if "@" in login_name:
            candidates.append(login_name.split("@", 1)[0])

        last_error: Exception | None = None
        for candidate in candidates:
            try:
                response = await self._http.post(
                    f"{settings.zitadel_url.rstrip('/')}/v2/sessions",
                    headers=self._zitadel_headers(),
                    json={
                        "checks": {
                            "user": {"loginName": candidate},
                            "password": {"password": settings.etl_reader_password},
                        }
                    },
                )
                response.raise_for_status()
                body = response.json()
                session_id = body.get("sessionId") or body.get("session_id")
                session_token = body.get("sessionToken") or body.get("session_token")
                if not session_id or not session_token:
                    raise RuntimeError(
                        f"ZITADEL session response missing session fields: {body}"
                    )
                self._session = SessionCredentials(
                    session_id=str(session_id),
                    session_token=str(session_token),
                )
                return
            except httpx.HTTPStatusError as exc:
                last_error = exc

        if last_error is not None:
            raise last_error
        raise RuntimeError("ETL reader login failed")

    async def fetch_instruction(self, instruction_id: str) -> dict[str, Any] | None:
        if self._http is None:
            raise RuntimeError("instruction client not connected")
        if not instruction_id:
            return None

        url = (
            f"{settings.ilm_url.rstrip('/')}"
            f"{settings.ilm_api_prefix.rstrip('/')}/instructions/{instruction_id}"
        )

        for attempt in range(2):
            response = await self._http.get(url, headers=self._ilm_headers())
            if response.status_code == 404:
                logger.warning("instruction not found via ILM API: %s", instruction_id)
                return None
            if response.status_code == 401 and attempt == 0:
                logger.info("ILM session expired; re-authenticating ETL reader")
                await self._login()
                continue
            if response.status_code == 403:
                logger.error(
                    "ILM denied VIEW for instruction_id=%s (check OPA / etl-reader roles)",
                    instruction_id,
                )
                return None
            response.raise_for_status()
            return response.json()

        return None
