from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

from ps import __version__
from ps.config import settings
from ps.models.api import Subject
from ps.models.enums import PaymentAction, SecurityEventOutcome, SecurityEventSeverity
from ps.models.payment import Payment


class SecurityEventActor(BaseModel):
    user_id: str
    given_name: str | None = None
    family_name: str | None = None
    title: str
    roles: list[str]
    groups: list[str] = Field(default_factory=list)
    covering_lobs: list[str] = Field(default_factory=list)
    lob: str | None = None
    supervisor_id: str | None = None


class SecurityEventResource(BaseModel):
    type: str = "cash_payment"
    id: str
    instruction_id: str
    owning_lob: str
    status: str
    amount: float
    currency: str


class SecurityEventContext(BaseModel):
    kind: str = "event"
    category: list[str] = Field(default_factory=lambda: ["iam"])
    type: list[str]
    action: str
    outcome: SecurityEventOutcome
    reason: str | None = None


class SecurityEventSource(BaseModel):
    application: str
    service: str
    version: str


class PaymentSecurityEvent(BaseModel):
    event_id: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    severity: SecurityEventSeverity
    message: str
    event: SecurityEventContext
    actor: SecurityEventActor
    resource: SecurityEventResource
    source: SecurityEventSource
    details: dict[str, Any] = Field(default_factory=dict)
    payment_snapshot: dict[str, Any] | None = None

    @classmethod
    def _actor(cls, subject: Subject) -> SecurityEventActor:
        return SecurityEventActor(
            user_id=subject.user_id,
            given_name=subject.given_name,
            family_name=subject.family_name,
            title=subject.title,
            roles=subject.roles,
            groups=subject.groups,
            covering_lobs=subject.covering_lobs,
            lob=subject.lob,
            supervisor_id=subject.supervisor_id,
        )

    @classmethod
    def _resource(cls, payment: Payment) -> SecurityEventResource:
        return SecurityEventResource(
            id=payment.payment_id,
            instruction_id=payment.instruction_id,
            owning_lob=payment.owning_lob,
            status=payment.status.value,
            amount=payment.amount,
            currency=payment.currency,
        )

    @classmethod
    def _source(cls) -> SecurityEventSource:
        return SecurityEventSource(
            application=settings.application_name,
            service=settings.application_name,
            version=__version__,
        )

    @classmethod
    def _event_types_for_action(cls, action: PaymentAction) -> list[str]:
        if action == PaymentAction.CREATE_PAYMENT:
            return ["creation"]
        return ["change"]

    @classmethod
    def authorized_action(
        cls,
        action: PaymentAction,
        subject: Subject,
        payment: Payment,
        *,
        event_id: str,
        details: dict[str, Any] | None = None,
    ) -> "PaymentSecurityEvent":
        event_details = dict(details or {})
        authorization = event_details.get("authorization") or {}
        reason = authorization.get("summary")
        return cls(
            event_id=event_id,
            severity=SecurityEventSeverity.INFO,
            message=f"Authorized {action.value} on payment {payment.payment_id} by {subject.user_id}",
            event=SecurityEventContext(
                type=cls._event_types_for_action(action),
                action=action.value,
                outcome=SecurityEventOutcome.SUCCESS,
                reason=reason,
            ),
            actor=cls._actor(subject),
            resource=cls._resource(payment),
            source=cls._source(),
            details=details or {},
            payment_snapshot=payment.to_mongo(),
        )

    @classmethod
    def policy_denial(
        cls,
        action: PaymentAction,
        subject: Subject,
        payment: Payment,
        *,
        event_id: str,
        reason: str,
        details: dict[str, Any] | None = None,
        severity: SecurityEventSeverity | None = None,
    ) -> "PaymentSecurityEvent":
        event_details = dict(details or {})
        event_details["policy_engine"] = "opa"
        return cls(
            event_id=event_id,
            severity=severity or SecurityEventSeverity.ALERT,
            message=f"Policy denied {action.value} on payment {payment.payment_id} by {subject.user_id}",
            event=SecurityEventContext(
                type=["access", "denied"],
                action=action.value,
                outcome=SecurityEventOutcome.FAILURE,
                reason=reason,
            ),
            actor=cls._actor(subject),
            resource=cls._resource(payment),
            source=cls._source(),
            details=event_details,
            payment_snapshot=payment.to_mongo(),
        )
