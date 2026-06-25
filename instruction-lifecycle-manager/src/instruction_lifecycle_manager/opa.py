from typing import Any

import httpx

from instruction_lifecycle_manager.config import settings
from instruction_lifecycle_manager.models.enums import LifecycleAction
from instruction_lifecycle_manager.models.instruction import CashSettlementInstruction
from instruction_lifecycle_manager.models.api import Subject


class PolicyDeniedError(Exception):
    pass


class OpaClient:
    def __init__(self, base_url: str | None = None) -> None:
        self.base_url = (base_url or settings.opa_url).rstrip("/")

    async def is_allowed(
        self,
        action: LifecycleAction,
        subject: Subject,
        instruction: CashSettlementInstruction,
    ) -> bool:
        payload = {
            "input": {
                "action": action.value,
                "subject": subject.to_opa_subject(),
                "instruction": instruction.to_opa_instruction(),
                "account": instruction.to_opa_account(),
            }
        }
        url = f"{self.base_url}/v1/data/ssi/instruction_lifecycle/allow"

        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(url, json=payload)
            response.raise_for_status()
            body: dict[str, Any] = response.json()

        return bool(body.get("result"))

    async def authorize(
        self,
        action: LifecycleAction,
        subject: Subject,
        instruction: CashSettlementInstruction,
    ) -> None:
        if not await self.is_allowed(action, subject, instruction):
            raise PolicyDeniedError(
                f"OPA denied action {action.value} for instruction {instruction.instruction_id}"
            )
