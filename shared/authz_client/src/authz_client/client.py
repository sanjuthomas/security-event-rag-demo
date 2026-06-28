from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from authz_client.errors import AuthzClientError, AuthzServiceUnavailable


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    allow_basis: list[str]
    violations: list[str]
    is_alert: bool


class AuthzClient:
    def __init__(self, base_url: str, *, timeout: float = 10.0) -> None:
        self._base = base_url.rstrip("/")
        self._timeout = timeout

    @staticmethod
    def _obo_headers(
        *,
        service_token: str | None,
        service_session_id: str | None,
        user_token: str | None,
        user_session_id: str | None,
    ) -> dict[str, str]:
        headers: dict[str, str] = {}
        if service_token:
            headers["Authorization"] = f"Bearer {service_token}"
        if service_session_id:
            headers["X-Session-Id"] = service_session_id
        if user_token:
            headers["X-On-Behalf-Of"] = user_token
        if user_session_id:
            headers["X-On-Behalf-Of-Session-Id"] = user_session_id
        return headers

    @staticmethod
    def _service_headers(
        *,
        service_token: str | None,
        service_session_id: str | None,
    ) -> dict[str, str]:
        headers: dict[str, str] = {}
        if service_token:
            headers["Authorization"] = f"Bearer {service_token}"
        if service_session_id:
            headers["X-Session-Id"] = service_session_id
        return headers

    async def _post(
        self,
        path: str,
        payload: dict[str, Any],
        *,
        headers: dict[str, str],
    ) -> PolicyDecision:
        url = f"{self._base}{path}"
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(url, json=payload, headers=headers)
        except httpx.HTTPError as exc:
            raise AuthzServiceUnavailable(
                f"authorization-service unreachable at {self._base}"
            ) from exc

        if response.status_code >= 500:
            raise AuthzServiceUnavailable(
                f"authorization-service error ({response.status_code}): {response.text}"
            )
        if response.status_code >= 400:
            raise AuthzClientError(
                f"authorization-service rejected request ({response.status_code}): {response.text}"
            )

        body = response.json()
        return PolicyDecision(
            allowed=bool(body.get("allowed")),
            allow_basis=list(body.get("allow_basis") or []),
            violations=list(body.get("violations") or []),
            is_alert=bool(body.get("is_alert")),
        )

    async def evaluate_instruction(
        self,
        *,
        action: str,
        instruction: dict[str, Any],
        account: dict[str, Any],
        service_token: str | None = None,
        service_session_id: str | None = None,
        user_token: str | None = None,
        user_session_id: str | None = None,
        subject: dict[str, Any] | None = None,
    ) -> PolicyDecision:
        payload: dict[str, Any] = {
            "action": action,
            "instruction": instruction,
            "account": account,
        }
        if user_token and service_token:
            headers = self._obo_headers(
                service_token=service_token,
                service_session_id=service_session_id,
                user_token=user_token,
                user_session_id=user_session_id,
            )
        else:
            if subject is None:
                raise AuthzClientError("subject is required when OBO tokens are not provided")
            payload["subject"] = subject
            headers = self._service_headers(
                service_token=service_token,
                service_session_id=service_session_id,
            )

        return await self._post(
            "/api/v1/authorization/instructions/evaluate",
            payload,
            headers=headers,
        )

    async def evaluate_payment(
        self,
        *,
        action: str,
        payment: dict[str, Any],
        instruction_end_date: str = "",
        instruction_status: str = "",
        service_token: str | None = None,
        service_session_id: str | None = None,
        user_token: str | None = None,
        user_session_id: str | None = None,
        subject: dict[str, Any] | None = None,
    ) -> PolicyDecision:
        payload: dict[str, Any] = {
            "action": action,
            "payment": payment,
            "instruction_end_date": instruction_end_date,
            "instruction_status": instruction_status,
        }
        if user_token and service_token:
            headers = self._obo_headers(
                service_token=service_token,
                service_session_id=service_session_id,
                user_token=user_token,
                user_session_id=user_session_id,
            )
        else:
            if subject is None:
                raise AuthzClientError("subject is required when OBO tokens are not provided")
            payload["subject"] = subject
            headers = self._service_headers(
                service_token=service_token,
                service_session_id=service_session_id,
            )

        return await self._post(
            "/api/v1/authorization/payments/evaluate",
            payload,
            headers=headers,
        )

    async def _post_json(
        self,
        path: str,
        payload: dict[str, Any],
        *,
        headers: dict[str, str],
    ) -> dict[str, Any]:
        url = f"{self._base}{path}"
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(url, json=payload, headers=headers)
        except httpx.HTTPError as exc:
            raise AuthzServiceUnavailable(
                f"authorization-service unreachable at {self._base}"
            ) from exc

        if response.status_code >= 500:
            raise AuthzServiceUnavailable(
                f"authorization-service error ({response.status_code}): {response.text}"
            )
        if response.status_code >= 400:
            raise AuthzClientError(
                f"authorization-service rejected request ({response.status_code}): {response.text}"
            )
        return response.json()

    async def eligible_payment_approvers(
        self,
        *,
        payment: dict[str, Any],
        instruction_status: str,
        instruction_end_date: str = "",
        service_token: str | None = None,
        service_session_id: str | None = None,
    ) -> dict[str, Any]:
        payload = {
            "payment": payment,
            "instruction_status": instruction_status,
            "instruction_end_date": instruction_end_date,
        }
        headers = self._service_headers(
            service_token=service_token,
            service_session_id=service_session_id,
        )
        return await self._post_json(
            "/api/v1/authorization/payments/eligible-approvers",
            payload,
            headers=headers,
        )

    async def eligible_instruction_approvers(
        self,
        *,
        instruction: dict[str, Any],
        service_token: str | None = None,
        service_session_id: str | None = None,
    ) -> dict[str, Any]:
        payload = {"instruction": instruction}
        headers = self._service_headers(
            service_token=service_token,
            service_session_id=service_session_id,
        )
        return await self._post_json(
            "/api/v1/authorization/instructions/eligible-approvers",
            payload,
            headers=headers,
        )
