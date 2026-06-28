from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

from inst import __version__
from inst.config import settings
from inst.models.api import Subject
from inst.models.enums import (
    LifecycleAction,
    SecurityEventOutcome,
    SecurityEventSeverity,
)
from inst.models.instruction import CashSettlementInstruction


class SecurityEventActor(BaseModel):
    user_id: str
    given_name: str | None = None
    family_name: str | None = None
    title: str
    roles: list[str]
    groups: list[str] = Field(default_factory=list)
    lob: str | None = None
    supervisor_id: str | None = None
    # Set when the action was performed via an On-Behalf-Of service delegation.
    delegated_by: str | None = None


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

    event_id: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    severity: SecurityEventSeverity
    message: str
    event: SecurityEventContext
    actor: SecurityEventActor
    resource: SecurityEventResource
    source: SecurityEventSource
    details: dict[str, Any] = Field(default_factory=dict)
    instruction_snapshot: dict[str, Any] | None = Field(
        default=None,
        description="Full instruction state at the time of this event — carried in the Kafka fact event so ETL needs no API callback",
    )

    @classmethod
    def _actor_from_subject(cls, subject: Subject) -> SecurityEventActor:
        return SecurityEventActor(
            user_id=subject.user_id,
            given_name=subject.given_name,
            family_name=subject.family_name,
            title=subject.title,
            roles=subject.roles,
            groups=subject.groups,
            lob=subject.lob,
            supervisor_id=subject.supervisor_id,
            delegated_by=subject.delegated_by,
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
    def _delegation_details(cls, subject: Subject) -> dict[str, Any]:
        """Extra details to include when the call is an OBO delegation."""
        if not subject.delegated_by:
            return {}
        return {"delegated_by": subject.delegated_by, "delegation": "on_behalf_of"}

    @classmethod
    def authorized_action(
        cls,
        action: LifecycleAction,
        subject: Subject,
        instruction: CashSettlementInstruction,
        *,
        event_id: str,
        version_number: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> "SecurityEvent":
        actor = cls._actor_from_subject(subject)
        resource = cls._resource_from_instruction(
            instruction, version_number=version_number
        )
        event_details = {**cls._delegation_details(subject), **(details or {})}
        authorization = event_details.get("authorization") or {}
        reason = authorization.get("summary")
        return cls(
            event_id=event_id,
            severity=SecurityEventSeverity.INFO,
            message=(
                f"Authorized {action.value} on instruction "
                f"{instruction.instruction_id} by {subject.user_id}"
                + (f" via {subject.delegated_by}" if subject.delegated_by else "")
            ),
            event=SecurityEventContext(
                type=cls._event_types_for_action(action),
                action=action.value,
                outcome=SecurityEventOutcome.SUCCESS,
                reason=reason,
            ),
            actor=actor,
            resource=resource,
            source=cls._source(),
            details=event_details,
            instruction_snapshot=instruction.model_dump(mode="json"),
        )

    @classmethod
    def policy_denial(
        cls,
        action: LifecycleAction,
        subject: Subject,
        instruction: CashSettlementInstruction,
        *,
        event_id: str,
        reason: str,
        details: dict[str, Any] | None = None,
        severity: SecurityEventSeverity | None = None,
    ) -> "SecurityEvent":
        actor = cls._actor_from_subject(subject)
        resource = cls._resource_from_instruction(instruction)
        event_details = {
            **cls._delegation_details(subject),
            **(details or {}),
            "policy_engine": "opa",
        }
        return cls(
            event_id=event_id,
            severity=severity or SecurityEventSeverity.ALERT,
            message=(
                f"Policy denied {action.value} on instruction "
                f"{instruction.instruction_id} by {subject.user_id}"
                + (f" via {subject.delegated_by}" if subject.delegated_by else "")
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
            instruction_snapshot=instruction.model_dump(mode="json"),
        )
