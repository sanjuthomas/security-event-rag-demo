from typing import Any

from instruction_lifecycle_manager.config import settings
from instruction_lifecycle_manager.database import get_security_events_database
from instruction_lifecycle_manager.models.api import Subject
from instruction_lifecycle_manager.models.enums import LifecycleAction
from instruction_lifecycle_manager.models.instruction import CashSettlementInstruction
from instruction_lifecycle_manager.kafka_publisher import kafka_publisher
from instruction_lifecycle_manager.models.security_event import SecurityEvent


class SecurityEventRepository:
    """Internal write-only persistence for SIEM events (no REST exposure)."""
    def __init__(self, collection_name: str | None = None) -> None:
        self.collection_name = collection_name or settings.security_events_collection

    @property
    def collection(self):
        return get_security_events_database()[self.collection_name]

    async def insert(self, event: SecurityEvent) -> SecurityEvent:
        document = event.model_dump(mode="json")
        await self.collection.insert_one(document)
        await kafka_publisher.publish(document)
        return event

    async def record_authorized_action(
        self,
        action: LifecycleAction,
        subject: Subject,
        instruction: CashSettlementInstruction,
        *,
        version_number: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> SecurityEvent:
        event = SecurityEvent.authorized_action(
            action,
            subject,
            instruction,
            version_number=version_number,
            details=details,
        )
        return await self.insert(event)

    async def record_policy_denial(
        self,
        action: LifecycleAction,
        subject: Subject,
        instruction: CashSettlementInstruction,
        *,
        reason: str,
        details: dict[str, Any] | None = None,
    ) -> SecurityEvent:
        event = SecurityEvent.policy_denial(
            action,
            subject,
            instruction,
            reason=reason,
            details=details,
        )
        return await self.insert(event)
