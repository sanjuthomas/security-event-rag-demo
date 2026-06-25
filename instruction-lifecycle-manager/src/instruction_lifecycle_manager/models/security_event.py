from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from instruction_lifecycle_manager import __version__
from instruction_lifecycle_manager.config import settings
from instruction_lifecycle_manager.models.api import Subject
from instruction_lifecycle_manager.models.enums import (
    LifecycleAction,
    SecurityEventOutcome,
    SecurityEventSeverity,
)
from instruction_lifecycle_manager.models.instruction import CashSettlementInstruction


class SecurityEventActor(BaseModel):
    user_id: str
    title: str
    roles: list[str]
    lob: str | None = None
    supervisor_id: str | None = None


class SecurityEventResource(BaseModel):
    type: str = "cash_settlement_instruction"
    id: str
    owning_lob: str
    status: str
    instruction_type: str | None = None
    version_number: int | None = None


class SecurityEventContext(BaseModel):
    """ECS-aligned event metadata for SIEM ingestion."""

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


class SecurityEvent(BaseModel):
    """Canonical security event stored in the security_events database."""

    event_id: str = Field(default_factory=lambda: str(uuid4()))
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    severity: SecurityEventSeverity
    message: str
    event: SecurityEventContext
    actor: SecurityEventActor
    resource: SecurityEventResource
    source: SecurityEventSource
    details: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def _actor_from_subject(cls, subject: Subject) -> SecurityEventActor:
        return SecurityEventActor(
            user_id=subject.user_id,
            title=subject.title,
            roles=subject.roles,
            lob=subject.lob,
            supervisor_id=subject.supervisor_id,
        )

    @classmethod
    def _resource_from_instruction(
        cls,
        instruction: CashSettlementInstruction,
        *,
        version_number: int | None = None,
    ) -> SecurityEventResource:
        return SecurityEventResource(
            id=instruction.instruction_id,
            owning_lob=instruction.owning_lob,
            status=instruction.status.value,
            instruction_type=instruction.instruction_type.value,
            version_number=version_number,
        )

    @classmethod
    def _event_types_for_action(cls, action: LifecycleAction) -> list[str]:
        if action == LifecycleAction.CREATE:
            return ["creation"]
        if action == LifecycleAction.DELETE:
            return ["deletion"]
        if action == LifecycleAction.VIEW:
            return ["access"]
        return ["change"]

    @classmethod
    def _source(cls) -> SecurityEventSource:
        return SecurityEventSource(
            application=settings.application_name,
            service=settings.application_name,
            version=__version__,
        )

    @classmethod
    def authorized_action(
        cls,
        action: LifecycleAction,
        subject: Subject,
        instruction: CashSettlementInstruction,
        *,
        version_number: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> "SecurityEvent":
        actor = cls._actor_from_subject(subject)
        resource = cls._resource_from_instruction(
            instruction, version_number=version_number
        )
        return cls(
            severity=SecurityEventSeverity.INFO,
            message=(
                f"Authorized {action.value} on instruction "
                f"{instruction.instruction_id} by {subject.user_id}"
            ),
            event=SecurityEventContext(
                type=cls._event_types_for_action(action),
                action=action.value,
                outcome=SecurityEventOutcome.SUCCESS,
            ),
            actor=actor,
            resource=resource,
            source=cls._source(),
            details=details or {},
        )

    @classmethod
    def policy_denial(
        cls,
        action: LifecycleAction,
        subject: Subject,
        instruction: CashSettlementInstruction,
        *,
        reason: str,
        details: dict[str, Any] | None = None,
    ) -> "SecurityEvent":
        actor = cls._actor_from_subject(subject)
        resource = cls._resource_from_instruction(instruction)
        event_details = dict(details or {})
        event_details["policy_engine"] = "opa"
        return cls(
            severity=SecurityEventSeverity.ALERT,
            message=(
                f"Policy denied {action.value} on instruction "
                f"{instruction.instruction_id} by {subject.user_id}"
            ),
            event=SecurityEventContext(
                type=["access", "denied"],
                action=action.value,
                outcome=SecurityEventOutcome.FAILURE,
                reason=reason,
            ),
            actor=actor,
            resource=resource,
            source=cls._source(),
            details=event_details,
        )
