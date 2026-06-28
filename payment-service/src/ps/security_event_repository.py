import logging
from typing import Any

from sequence_client import SequenceClient
from sequence_client.errors import SequenceClientError

from ps.config import settings
from ps.database import get_security_events_db
from ps.kafka_publisher import kafka_publisher
from ps.models.api import Subject
from ps.models.enums import PaymentAction, SecurityEventSeverity
from ps.models.payment import Payment
from ps.models.security_event import PaymentSecurityEvent

logger = logging.getLogger(__name__)


class SecurityEventRepository:
    """Write-only persistence for payment SIEM events (MongoDB + Kafka)."""

    def __init__(self, sequence_client: SequenceClient | None = None) -> None:
        self.sequence = sequence_client or SequenceClient(settings.sequence_service_url)

    @property
    def _col(self):
        return get_security_events_db()[settings.security_events_collection]

    async def allocate_event_id(self, resource_id: str) -> str:
        try:
            return await self.sequence.next_security_event_id(resource_id=resource_id)
        except SequenceClientError as exc:
            raise RuntimeError(f"security event sequence allocation failed: {exc}") from exc

    async def insert_document(self, document: dict[str, Any]) -> dict[str, Any]:
        await self._col.insert_one(document)
        return document

    async def publish(self, document: dict[str, Any]) -> None:
        await kafka_publisher.publish_security_event(document)

    async def insert(self, event: PaymentSecurityEvent) -> PaymentSecurityEvent:
        document = event.model_dump(mode="json")
        await self.insert_document(document)
        try:
            await self.publish(document)
        except Exception:
            logger.exception("failed to publish security event %s to Kafka", event.event_id)
        return event

    async def record_authorized_action(
        self,
        action: PaymentAction,
        subject: Subject,
        payment: Payment,
        *,
        details: dict[str, Any] | None = None,
    ) -> PaymentSecurityEvent:
        event_id = await self.allocate_event_id(payment.payment_id)
        event = PaymentSecurityEvent.authorized_action(
            action,
            subject,
            payment,
            event_id=event_id,
            details=details,
        )
        return await self.insert(event)

    async def record_policy_denial(
        self,
        action: PaymentAction,
        subject: Subject,
        payment: Payment,
        *,
        reason: str,
        details: dict[str, Any] | None = None,
        severity: SecurityEventSeverity | None = None,
    ) -> PaymentSecurityEvent:
        event_id = await self.allocate_event_id(payment.payment_id)
        event = PaymentSecurityEvent.policy_denial(
            action,
            subject,
            payment,
            event_id=event_id,
            reason=reason,
            details=details,
            severity=severity,
        )
        return await self.insert(event)

    async def ensure_indexes(self) -> None:
        await self._col.create_index("event_id", unique=True)
        await self._col.create_index([("timestamp", -1)])
        await self._col.create_index("event.action")
        await self._col.create_index("event.outcome")
        await self._col.create_index("severity")
        await self._col.create_index("actor.user_id")
        await self._col.create_index("resource.id")
        logger.info("payment security_events collection indexes ensured")
