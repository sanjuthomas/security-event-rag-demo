from __future__ import annotations

from dataclasses import dataclass

import httpx


@dataclass(frozen=True)
class SessionCredentials:
    session_id: str
    session_token: str


class ZitadelAuthClient:
    """Authenticate human users via the ZITADEL Session API (username + password)."""

    def __init__(
        self,
        base_url: str,
        service_pat: str,
        *,
        host_header: str = "",
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.service_pat = service_pat
        self.host_header = host_header.strip()

    def login(self, login_name: str, password: str) -> SessionCredentials:
        candidates = [login_name]
        if "@" in login_name:
            candidates.append(login_name.split("@", 1)[0])

        last_error: Exception | None = None
        for candidate in candidates:
            try:
                return self._create_session(candidate, password)
            except httpx.HTTPStatusError as exc:
                last_error = exc
        if last_error is not None:
            raise last_error
        raise RuntimeError("login failed")

    def _request_headers(self) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.service_pat}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if self.host_header:
            headers["Host"] = self.host_header
        return headers

    def _create_session(self, login_name: str, password: str) -> SessionCredentials:
        with httpx.Client(timeout=30.0) as client:
            response = client.post(
                f"{self.base_url}/v2/sessions",
                headers=self._request_headers(),
                json={
                    "checks": {
                        "user": {"loginName": login_name},
                        "password": {"password": password},
                    }
                },
            )
            response.raise_for_status()
            body = response.json()

        session_id = body.get("sessionId") or body.get("session_id")
        session_token = body.get("sessionToken") or body.get("session_token")
        if not session_id or not session_token:
            raise RuntimeError(f"ZITADEL session response missing session fields: {body}")
        return SessionCredentials(session_id=session_id, session_token=session_token)
