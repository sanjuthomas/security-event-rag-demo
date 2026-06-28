"""Tests for cumulative payment Kafka facts."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from ps.models.api import LifecycleEvent
from ps.models.enums import PaymentAction, PaymentStatus
from ps.models.fact_validation import validate_payment_document
from ps.models.payment_fact import PaymentFact


def test_payment_fact_create_is_cumulative(payment, subject) -> None:
    fact = PaymentFact.from_payment(PaymentAction.CREATE_PAYMENT, subject, payment)
    doc = fact.to_kafka_value()
    assert doc["payment_id"] == payment.payment_id
    assert doc["created_by"]["user_id"] == subject.user_id
    assert len(doc["lifecycle_events"]) == 1
    assert doc["version_number"] == 1


def test_payment_fact_approve_retains_creator_and_adds_approver(
    payment, subject, approver_subject
) -> None:
    now = datetime.now(timezone.utc)
    payment.status = PaymentStatus.SUBMITTED
    payment.submitted_by = payment.created_by
    payment.lifecycle_events.append(
        LifecycleEvent(
            event_id="evt-submit",
            action="SUBMIT_PAYMENT",
            actor_user_id=subject.user_id,
            timestamp=now.isoformat(),
        )
    )
    payment.status = PaymentStatus.APPROVED
    payment.approved_by = payment.created_by.__class__(
        user_id=approver_subject.user_id,
        given_name=approver_subject.given_name,
        family_name=approver_subject.family_name,
        title=approver_subject.title,
        lob=approver_subject.lob,
        roles=approver_subject.roles,
        supervisor_id=approver_subject.supervisor_id,
    )
    payment.lifecycle_events.append(
        LifecycleEvent(
            event_id="evt-approve",
            action="APPROVE_PAYMENT",
            actor_user_id=approver_subject.user_id,
            timestamp=now.isoformat(),
        )
    )

    fact = PaymentFact.from_payment(
        PaymentAction.APPROVE_PAYMENT,
        approver_subject,
        payment,
    )
    doc = fact.to_kafka_value()
    assert doc["created_by"]["user_id"] == subject.user_id
    assert doc["approved_by"]["user_id"] == approver_subject.user_id
    assert doc["version_number"] == 3
    assert len(doc["lifecycle_events"]) == 3


def test_payment_fact_rejects_version_mismatch(payment, subject) -> None:
    doc = payment.model_dump(mode="json")
    doc["version_number"] = 99
    with pytest.raises(ValueError, match="version_number must equal"):
        validate_payment_document(doc, action=PaymentAction.CREATE_PAYMENT.value)


def test_validate_payment_document_requires_submitted_by_on_submit(
    payment, subject
) -> None:
    doc = payment.model_dump(mode="json")
    with pytest.raises(ValueError, match="submitted_by"):
        validate_payment_document(doc, action=PaymentAction.SUBMIT_PAYMENT.value)
