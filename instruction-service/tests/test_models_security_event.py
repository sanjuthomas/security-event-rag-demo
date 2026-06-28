from inst.models.api import Subject
from inst.models.enums import (
    LifecycleAction,
    SecurityEventOutcome,
    SecurityEventSeverity,
)
from inst.models.instruction import CashSettlementInstruction
from inst.models.security_event import SecurityEvent


def test_authorized_action(sample_subject: Subject, sample_instruction: CashSettlementInstruction) -> None:
    event = SecurityEvent.authorized_action(
        LifecycleAction.CREATE,
        sample_subject,
        sample_instruction,
        version_number=1,
        details={"authorization": {"summary": "allowed"}},
    )
    assert event.severity == SecurityEventSeverity.INFO
    assert event.event.outcome == SecurityEventOutcome.SUCCESS
    assert event.event.type == ["creation"]
    assert event.event.action == "CREATE"
    assert event.event.reason == "allowed"
    assert event.actor.user_id == "alice.ficc"
    assert event.resource.id == sample_instruction.instruction_id
    assert event.resource.version_number == 1
    assert event.instruction_snapshot is not None
    assert "Authorized CREATE" in event.message


def test_authorized_action_event_types() -> None:
    assert SecurityEvent._event_types_for_action(LifecycleAction.CREATE) == ["creation"]
    assert SecurityEvent._event_types_for_action(LifecycleAction.DELETE) == ["deletion"]
    assert SecurityEvent._event_types_for_action(LifecycleAction.VIEW) == ["access"]
    assert SecurityEvent._event_types_for_action(LifecycleAction.APPROVE) == ["change"]


def test_policy_denial(sample_subject: Subject, sample_instruction: CashSettlementInstruction) -> None:
    event = SecurityEvent.policy_denial(
        LifecycleAction.APPROVE,
        sample_subject,
        sample_instruction,
        reason="denied",
    )
    assert event.severity == SecurityEventSeverity.ALERT
    assert event.event.outcome == SecurityEventOutcome.FAILURE
    assert event.event.type == ["access", "denied"]
    assert event.details["policy_engine"] == "opa"
    assert "Policy denied APPROVE" in event.message


def test_policy_denial_preserves_is_alert_in_details(
    sample_subject: Subject,
    sample_instruction: CashSettlementInstruction,
) -> None:
    event = SecurityEvent.policy_denial(
        LifecycleAction.APPROVE,
        sample_subject,
        sample_instruction,
        reason="alert",
        details={"authorization": {"is_alert": True}},
    )
    assert event.severity == SecurityEventSeverity.ALERT
    assert event.details["authorization"]["is_alert"] is True


def test_obo_delegation_details(
    sample_subject: Subject,
    sample_instruction: CashSettlementInstruction,
) -> None:
    subject = sample_subject.model_copy(update={"delegated_by": "payment-service"})
    event = SecurityEvent.authorized_action(
        LifecycleAction.USE,
        subject,
        sample_instruction,
    )
    assert event.details["delegated_by"] == "payment-service"
    assert event.details["delegation"] == "on_behalf_of"
    assert "via payment-service" in event.message
    assert event.actor.delegated_by == "payment-service"
