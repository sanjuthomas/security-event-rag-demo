"""Fact event published to the ssi-payments Kafka topic.

Each successful payment mutation publishes the **full cumulative payment document**
so the indexer can replace its single Qdrant point with latest-and-greatest state.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from ps.models.api import Subject
from ps.models.enums import PaymentAction
from ps.models.fact_validation import validate_payment_document
from ps.models.payment import Payment


class PaymentFact(BaseModel):
    """Coordination metadata + validated full payment snapshot for Kafka."""

    fact_id: str = Field(default_factory=lambda: str(uuid4()))
    payment_id: str
    version_number: int
    action: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    actor_user_id: str
    actor_given_name: str | None = None
    actor_family_name: str | None = None
    actor_title: str | None = None
    actor_lob: str | None = None

    authorization: dict[str, Any] | None = Field(
        default=None,
        description="OPA decision context for this mutation",
    )
    payment_snapshot: dict[str, Any] = Field(
        description="Full serialized Payment after sync_version_number()",
    )

    @classmethod
    def from_payment(
        cls,
        action: PaymentAction,
        subject: Subject,
        payment: Payment,
        *,
        authorization: dict[str, Any] | None = None,
    ) -> "PaymentFact":
        payment.sync_version_number()
        snapshot = payment.model_dump(mode="json")
        validate_payment_document(snapshot, action=action.value)

        return cls(
            payment_id=payment.payment_id,
            version_number=payment.version_number,
            action=action.value,
            actor_user_id=subject.user_id,
            actor_given_name=subject.given_name,
            actor_family_name=subject.family_name,
            actor_title=subject.title,
            actor_lob=subject.lob,
            authorization=authorization,
            payment_snapshot=snapshot,
        )

    def to_kafka_value(self) -> dict[str, Any]:
        """Flat cumulative payment document for ssi-indexer (backward compatible)."""
        return dict(self.payment_snapshot)
