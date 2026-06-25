from __future__ import annotations

from typing import Any

import httpx

from harness.config import Settings
from harness.zitadel_auth import SessionCredentials


class InstructionLifecycleClient:
    def __init__(self, settings: Settings) -> None:
        self.base_url = settings.ilm_url.rstrip("/")
        self.api_prefix = settings.ilm_api_prefix.rstrip("/")

    def _url(self, path: str) -> str:
        return f"{self.base_url}{self.api_prefix}{path}"

    def request(
        self,
        method: str,
        path: str,
        *,
        session: SessionCredentials,
        json_body: dict[str, Any] | None = None,
    ) -> httpx.Response:
        with httpx.Client(timeout=30.0) as client:
            return client.request(
                method,
                self._url(path),
                headers={
                    "Authorization": f"Bearer {session.session_token}",
                    "X-Session-Id": session.session_id,
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
                json=json_body,
            )

    def create_instruction(
        self, session: SessionCredentials, payload: dict[str, Any]
    ) -> httpx.Response:
        return self.request("POST", "/instructions", session=session, json_body=payload)

    def get_instruction(self, session: SessionCredentials, instruction_id: str) -> httpx.Response:
        return self.request("GET", f"/instructions/{instruction_id}", session=session)

    def list_instructions(self, session: SessionCredentials) -> httpx.Response:
        return self.request("GET", "/instructions", session=session)

    def submit_instruction(
        self, session: SessionCredentials, instruction_id: str
    ) -> httpx.Response:
        return self.request(
            "POST",
            f"/instructions/{instruction_id}/submit",
            session=session,
        )

    def approve_instruction(
        self, session: SessionCredentials, instruction_id: str
    ) -> httpx.Response:
        return self.request(
            "POST",
            f"/instructions/{instruction_id}/approve",
            session=session,
        )

    def reject_instruction(
        self,
        session: SessionCredentials,
        instruction_id: str,
        *,
        reason: str,
    ) -> httpx.Response:
        return self.request(
            "POST",
            f"/instructions/{instruction_id}/reject",
            session=session,
            json_body={"reason": reason},
        )

    def list_versions(self, session: SessionCredentials, instruction_id: str) -> httpx.Response:
        return self.request(
            "GET",
            f"/instructions/{instruction_id}/versions",
            session=session,
        )
