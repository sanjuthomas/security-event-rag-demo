from __future__ import annotations

import logging
from typing import Any

import httpx

from chat_application.config import settings

logger = logging.getLogger(__name__)


class EligibilityClientError(Exception):
    pass


class EligibilityClient:
    def __init__(
        self,
        *,
        payment_service_url: str | None = None,
        instruction_service_url: str | None = None,
    ) -> None:
        self._payment_base = (payment_service_url or settings.payment_service_url).rstrip("/")
        self._instruction_base = (
            instruction_service_url or settings.instruction_service_url
        ).rstrip("/")

    async def eligible_approvers_for_payment(
        self,
        payment_id: str,
        *,
        bearer_token: str,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        url = f"{self._payment_base}/api/v1/payments/{payment_id}/eligible-approvers"
        headers = {
            "Authorization": f"Bearer {bearer_token}",
            "Accept": "application/json",
        }
        if session_id:
            headers["X-Session-Id"] = session_id

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, headers=headers)

        if response.status_code == 401:
            raise EligibilityClientError("authentication required — log in as a compliance analyst")
        if response.status_code == 403:
            raise EligibilityClientError("COMPLIANCE_ANALYST role required for this question")
        if response.status_code == 404:
            detail = response.json().get("detail", response.text)
            raise EligibilityClientError(str(detail))
        if not response.is_success:
            detail = response.json().get("detail", response.text)
            raise EligibilityClientError(f"payment service error: {detail}")

        return response.json()

    async def eligible_approvers_for_instruction(
        self,
        instruction_id: str,
        *,
        bearer_token: str,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        url = (
            f"{self._instruction_base}/api/v1/instructions/{instruction_id}/eligible-approvers"
        )
        headers = {
            "Authorization": f"Bearer {bearer_token}",
            "Accept": "application/json",
        }
        if session_id:
            headers["X-Session-Id"] = session_id

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, headers=headers)

        if response.status_code == 401:
            raise EligibilityClientError("authentication required — log in as a compliance analyst")
        if response.status_code == 403:
            raise EligibilityClientError("COMPLIANCE_ANALYST role required for this question")
        if response.status_code == 404:
            detail = response.json().get("detail", response.text)
            raise EligibilityClientError(str(detail))
        if not response.is_success:
            detail = response.json().get("detail", response.text)
            raise EligibilityClientError(f"instruction service error: {detail}")

        return response.json()


def format_eligible_approvers_answer(data: dict[str, Any]) -> str:
    payment_id = data.get("payment_id", "")
    status = data.get("payment_status", "")
    amount = data.get("amount")
    currency = data.get("currency", "")
    owning_lob = data.get("owning_lob", "")
    instruction_status = data.get("instruction_status", "")
    eligible = data.get("eligible") or []

    header = (
        f"Live OPA evaluation for payment {payment_id} "
        f"({status}, {currency} {amount:,.2f}, desk {owning_lob}, "
        f"instruction {instruction_status})."
    )

    if not eligible:
        return (
            f"{header}\n\n"
            "No users currently satisfy APPROVE_PAYMENT policy for this payment."
        )

    lines = [header, "", "Users who can approve this payment:"]
    for index, row in enumerate(eligible, start=1):
        name = row.get("display_name") or row.get("user_id")
        title = row.get("title") or ""
        basis = row.get("allow_basis") or []
        basis_text = f" — basis: {', '.join(basis)}" if basis else ""
        lines.append(f"{index}. {name} ({title}){basis_text}")

    evaluated = data.get("candidates_evaluated")
    if evaluated is not None:
        lines.append("")
        lines.append(f"Evaluated {evaluated} FUNDING_APPROVER candidate(s) from the user directory.")

    return "\n".join(lines)


def format_instruction_eligible_approvers_answer(data: dict[str, Any]) -> str:
    instruction_id = data.get("instruction_id", "")
    status = data.get("instruction_status", "")
    instruction_type = data.get("instruction_type", "")
    owning_lob = data.get("owning_lob", "")
    created_by = data.get("created_by_user_id", "")
    creator_title = data.get("created_by_title", "")
    eligible = data.get("eligible") or []

    header = (
        f"Live OPA evaluation for instruction {instruction_id} "
        f"({status}, {instruction_type}, desk {owning_lob}, "
        f"created by {created_by} / {creator_title})."
    )

    if not eligible:
        return (
            f"{header}\n\n"
            "No users currently satisfy APPROVE policy for this instruction."
        )

    lines = [header, "", "Users who can approve this instruction:"]
    for index, row in enumerate(eligible, start=1):
        name = row.get("display_name") or row.get("user_id")
        title = row.get("title") or ""
        basis = row.get("allow_basis") or []
        basis_text = f" — basis: {', '.join(basis)}" if basis else ""
        lines.append(f"{index}. {name} ({title}){basis_text}")

    evaluated = data.get("candidates_evaluated")
    if evaluated is not None:
        lines.append("")
        lines.append(
            f"Evaluated {evaluated} INSTRUCTION_APPROVER candidate(s) from the user directory."
        )

    return "\n".join(lines)
