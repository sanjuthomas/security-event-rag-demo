from __future__ import annotations

import pytest
from ps.models.api import (
    CreatePaymentRequest,
    RejectPaymentRequest,
    Subject,
    UserReference,
)
from ps.models.enums import (
    PaymentAction,
    PaymentStatus,
    SecurityEventOutcome,
    SecurityEventSeverity,
)
from ps.models.payment import Payment
from ps.models.security_event import PaymentSecurityEvent


def test_subject_to_opa_subject_includes_optional_fields(subject: Subject) -> None:
    payload = subject.to_opa_subject()
    assert payload["user_id"] == "alice"
    assert payload["title"] == "VP Finance"
    assert payload["roles"] == ["PAYMENT_CREATOR"]
    assert payload["groups"] == ["MIDDLE_OFFICE"]
    assert payload["covering_lobs"] == ["CORP", "RETAIL"]
    assert payload["lob"] == "CORP"
    assert payload["supervisor_id"] == "boss1"


def test_subject_to_opa_subject_omits_none_optional_fields() -> None:
    subject = Subject(user_id="u1", title="Analyst", roles=["PAYMENT_CREATOR"])
    payload = subject.to_opa_subject()
    assert "lob" not in payload
    assert "supervisor_id" not in payload


def test_create_payment_request_validation() -> None:
    req = CreatePaymentRequest(instruction_id="i1", value_date="2026-07-01", amount=100.0)
    assert req.amount == 100.0


def test_create_payment_request_rejects_non_positive_amount() -> None:
    with pytest.raises(ValueError):
        CreatePaymentRequest(instruction_id="i1", value_date="2026-07-01", amount=0)


def test_reject_payment_request_requires_reason() -> None:
    with pytest.raises(ValueError):
        RejectPaymentRequest(reason="")


def test_payment_create_sets_lifecycle_event(subject: Subject) -> None:
    payment = Payment.create(
        payment_id="20260715-EMEA-P-1",
        instruction_id="instr-1",
        instruction_version=2,
        amount=500.0,
        currency="EUR",
        value_date="2026-07-15",
        owning_lob="EMEA",
        instruction_type="SINGLE_USE",
        subject=subject,
        event_id="evt-99",
    )
    assert payment.status == PaymentStatus.DRAFT
    assert payment.instruction_version == 2
    assert payment.created_by == UserReference(
        user_id="alice",
        given_name="Alice",
        family_name="Smith",
        title="VP Finance",
        lob="CORP",
        roles=["PAYMENT_CREATOR"],
        supervisor_id="boss1",
    )
    assert len(payment.lifecycle_events) == 1
    assert payment.lifecycle_events[0].action == "CREATE_PAYMENT"
    assert payment.lifecycle_events[0].event_id == "evt-99"


def test_payment_to_opa_payment(payment: Payment) -> None:
    payload = payment.to_opa_payment(
        instruction_end_date="2026-12-31",
        instruction_status="STANDING",
    )
    assert payload["payment_id"] == payment.payment_id
    assert payload["instruction_id"] == "instr-001"
    assert payload["amount"] == 1_000_000.0
    assert payload["instruction_status"] == "STANDING"
    assert payload["created_by"]["user_id"] == "alice"


def test_payment_to_mongo_and_from_mongo_roundtrip(payment: Payment) -> None:
    doc = payment.to_mongo()
    restored = Payment.from_mongo({**doc, "_id": "mongo-id"})
    assert restored.payment_id == payment.payment_id
    assert restored.amount == payment.amount


def test_payment_security_event_authorized_action(subject: Subject, payment: Payment) -> None:
    event = PaymentSecurityEvent.authorized_action(
        PaymentAction.CREATE_PAYMENT,
        subject,
        payment,
        event_id="20260628-FICC-P-1-SE-1",
        details={"authorization": {"summary": "allowed"}},
    )
    assert event.severity == SecurityEventSeverity.INFO
    assert event.event.action == "CREATE_PAYMENT"
    assert event.event.outcome == SecurityEventOutcome.SUCCESS
    assert event.event.type == ["creation"]
    assert event.actor.user_id == "alice"
    assert event.resource.id == payment.payment_id
    assert event.payment_snapshot is not None


def test_payment_security_event_authorized_change_action(
    subject: Subject,
    payment: Payment,
) -> None:
    event = PaymentSecurityEvent.authorized_action(
        PaymentAction.SUBMIT_PAYMENT,
        subject,
        payment,
        event_id="20260628-FICC-P-1-SE-1",
    )
    assert event.event.type == ["change"]


def test_payment_security_event_policy_denial(subject: Subject, payment: Payment) -> None:
    event = PaymentSecurityEvent.policy_denial(
        PaymentAction.APPROVE_PAYMENT,
        subject,
        payment,
        event_id="20260628-FICC-P-1-SE-2",
        reason="denied",
    )
    assert event.severity == SecurityEventSeverity.ALERT
    assert event.event.outcome == SecurityEventOutcome.FAILURE
    assert event.event.type == ["access", "denied"]
    assert event.details["policy_engine"] == "opa"


def test_payment_status_enum_values() -> None:
    assert PaymentStatus.DRAFT.value == "DRAFT"
    assert PaymentAction.CANCEL_PAYMENT.value == "CANCEL_PAYMENT"
