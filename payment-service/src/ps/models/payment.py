from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from pydantic import BaseModel, Field

from ps.models.api import LifecycleEvent, Subject, UserReference
from ps.models.enums import PaymentStatus


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _user_ref(subject: Subject) -> UserReference:
    return UserReference(
        user_id=subject.user_id,
        given_name=subject.given_name,
        family_name=subject.family_name,
        title=subject.title,
        lob=subject.lob,
        roles=subject.roles,
        supervisor_id=subject.supervisor_id,
    )


class Payment(BaseModel):
    payment_id: str = Field(default_factory=lambda: str(uuid4()))
    instruction_id: str
    instruction_version: int
    version_number: int = 1
    status: PaymentStatus = PaymentStatus.DRAFT
    amount: float
    currency: str
    value_date: str
    owning_lob: str
    instruction_type: str
    created_by: UserReference
    submitted_by: UserReference | None = None
    approved_by: UserReference | None = None
    rejected_by: UserReference | None = None
    cancelled_by: UserReference | None = None
    rejection_reason: str | None = None
    cancellation_reason: str | None = None
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)
    lifecycle_events: list[LifecycleEvent] = Field(default_factory=list)

    @classmethod
    def create(
        cls,
        *,
        payment_id: str,
        instruction_id: str,
        instruction_version: int,
        amount: float,
        currency: str,
        value_date: str,
        owning_lob: str,
        instruction_type: str,
        subject: Subject,
        event_id: str,
    ) -> "Payment":
        now = _now()
        p = cls(
            payment_id=payment_id,
            instruction_id=instruction_id,
            instruction_version=instruction_version,
            amount=amount,
            currency=currency,
            value_date=value_date,
            owning_lob=owning_lob,
            instruction_type=instruction_type,
            created_by=_user_ref(subject),
            created_at=now,
            updated_at=now,
        )
        p.lifecycle_events.append(
            LifecycleEvent(
                event_id=event_id,
                action="CREATE_PAYMENT",
                actor_user_id=subject.user_id,
                timestamp=now.isoformat(),
            )
        )
        return p

    def sync_version_number(self) -> None:
        """Align version_number with lifecycle events (one version per mutation)."""
        self.version_number = max(1, len(self.lifecycle_events))

    def to_opa_payment(self, *, instruction_end_date: str, instruction_status: str) -> dict:
        return {
            "payment_id": self.payment_id,
            "instruction_id": self.instruction_id,
            "instruction_version": self.instruction_version,
            "amount": self.amount,
            "currency": self.currency,
            "instruction_status": instruction_status,
            "instruction_end_date": instruction_end_date,
            "instruction_owning_lob": self.owning_lob,
            "created_by": {
                "user_id": self.created_by.user_id,
                "supervisor_id": self.created_by.supervisor_id,
            },
        }

    def to_mongo(self) -> dict:
        return self.model_dump(mode="json")

    @classmethod
    def from_mongo(cls, doc: dict) -> "Payment":
        doc.pop("_id", None)
        return cls.model_validate(doc)
