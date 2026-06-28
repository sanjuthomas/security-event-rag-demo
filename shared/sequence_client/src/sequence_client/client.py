from __future__ import annotations

from datetime import date
from typing import Literal

import httpx

from sequence_client.errors import SequenceClientError, SequenceServiceUnavailable

EntityType = Literal["INSTRUCTION", "PAYMENT"]


class SequenceClient:
    def __init__(self, base_url: str, *, timeout: float = 10.0) -> None:
        self._base = base_url.rstrip("/")
        self._timeout = timeout

    async def next_sequence_id(
        self,
        *,
        business_date: date,
        owning_lob: str,
        entity_type: EntityType,
    ) -> str:
        url = f"{self._base}/api/v1/sequences/next"
        payload = {
            "business_date": business_date.isoformat(),
            "owning_lob": owning_lob,
            "entity_type": entity_type,
        }

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(url, json=payload)
        except httpx.HTTPError as exc:
            raise SequenceServiceUnavailable(
                f"sequence-service unreachable at {self._base}"
            ) from exc

        if response.status_code >= 500:
            raise SequenceServiceUnavailable(
                f"sequence-service error ({response.status_code}): {response.text}"
            )
        if response.status_code >= 400:
            raise SequenceClientError(
                f"sequence-service rejected request ({response.status_code}): {response.text}"
            )

        body = response.json()
        sequence_id = body.get("sequence_id")
        if not sequence_id:
            raise SequenceClientError("sequence-service response missing sequence_id")
        return str(sequence_id)

    async def next_instruction_id(self, *, business_date: date, owning_lob: str) -> str:
        return await self.next_sequence_id(
            business_date=business_date,
            owning_lob=owning_lob,
            entity_type="INSTRUCTION",
        )

    async def next_payment_id(self, *, business_date: date, owning_lob: str) -> str:
        return await self.next_sequence_id(
            business_date=business_date,
            owning_lob=owning_lob,
            entity_type="PAYMENT",
        )

    async def next_security_event_id(self, *, resource_id: str) -> str:
        url = f"{self._base}/api/v1/sequences/security-events/next"
        payload = {"resource_id": resource_id}

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(url, json=payload)
        except httpx.HTTPError as exc:
            raise SequenceServiceUnavailable(
                f"sequence-service unreachable at {self._base}"
            ) from exc

        if response.status_code >= 500:
            raise SequenceServiceUnavailable(
                f"sequence-service error ({response.status_code}): {response.text}"
            )
        if response.status_code >= 400:
            raise SequenceClientError(
                f"sequence-service rejected request ({response.status_code}): {response.text}"
            )

        body = response.json()
        sequence_id = body.get("sequence_id")
        if not sequence_id:
            raise SequenceClientError("sequence-service response missing sequence_id")
        return str(sequence_id)
