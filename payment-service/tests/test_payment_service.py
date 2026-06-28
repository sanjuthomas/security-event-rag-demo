from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from ps.authorization import PolicyDecision
from ps.ilm_client import InstructionNotFoundError, InstructionStateError
from ps.models.api import RejectPaymentRequest, Subject
from ps.models.enums import PaymentStatus
from ps.models.payment import Payment
from ps.repository import PaymentNotFoundError
from ps.service import PaymentService


def _allow_decision(*, basis: list[str] | None = None) -> PolicyDecision:
    return PolicyDecision(
        allowed=True,
        allow_basis=basis or ["policy ok"],
        violations=[],
        is_alert=False,
    )


def _deny_decision(violation: str = "SELF_APPROVAL") -> PolicyDecision:
    return PolicyDecision(
        allowed=False,
        allow_basis=[],
        violations=[violation],
        is_alert=False,
    )


@pytest.fixture
def service() -> PaymentService:
    svc = PaymentService(sequence_client=AsyncMock())
    svc.sequence.next_payment_id = AsyncMock(return_value="20260701-CORP-P-1")
    svc.repo = AsyncMock()
    svc.event_repo = AsyncMock()
    svc.authz = AsyncMock()
    svc.ilm = AsyncMock()
    return svc


@pytest.mark.asyncio
async def test_create_standing_payment_success(
    service: PaymentService,
    subject: Subject,
    standing_instruction: dict,
) -> None:
    service.ilm.get_instruction.return_value = standing_instruction
    service.authz.evaluate_payment.return_value = _allow_decision()
    service.repo.insert.return_value = None
    service.event_repo.record_authorized_action = AsyncMock()

    with patch("ps.service.kafka_publisher.publish_payment", new_callable=AsyncMock):
        payment = await service.create(
            instruction_id="instr-001",
            value_date="2026-07-01",
            amount=500_000.0,
            subject=subject,
        )

    assert payment.status == PaymentStatus.DRAFT
    assert payment.amount == 500_000.0
    service.repo.insert.assert_awaited_once()
    service.event_repo.record_authorized_action.assert_awaited_once()
    service.ilm.mark_used.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_single_use_runs_saga(
    service: PaymentService,
    subject: Subject,
    standing_instruction: dict,
) -> None:
    standing_instruction["status"] = "SINGLE_USE"
    service.ilm.get_instruction.return_value = standing_instruction
    service.authz.evaluate_payment.return_value = _allow_decision()
    service.ilm.mark_used.return_value = {"status": "USED"}

    with patch("ps.service.kafka_publisher.publish_payment", new_callable=AsyncMock):
        payment = await service.create(
            instruction_id="instr-001",
            value_date="2026-07-01",
            amount=100.0,
            subject=subject,
        )

    service.ilm.mark_used.assert_awaited_once()
    assert payment.instruction_type == "SINGLE_USE"


@pytest.mark.asyncio
async def test_create_instruction_not_found(
    service: PaymentService,
    subject: Subject,
) -> None:
    service.ilm.get_instruction.side_effect = InstructionNotFoundError("missing")
    with pytest.raises(InstructionNotFoundError):
        await service.create(
            instruction_id="missing",
            value_date="2026-07-01",
            amount=100.0,
            subject=subject,
        )


@pytest.mark.asyncio
async def test_create_rejects_unapproved_instruction(
    service: PaymentService,
    subject: Subject,
    standing_instruction: dict,
) -> None:
    standing_instruction["status"] = "DRAFT"
    service.ilm.get_instruction.return_value = standing_instruction
    with pytest.raises(ValueError, match="not in an approved state"):
        await service.create(
            instruction_id="instr-001",
            value_date="2026-07-01",
            amount=100.0,
            subject=subject,
        )


@pytest.mark.asyncio
async def test_create_policy_denied(
    service: PaymentService,
    subject: Subject,
    standing_instruction: dict,
    payment: Payment,
) -> None:
    service.ilm.get_instruction.return_value = standing_instruction
    service.authz.evaluate_payment.return_value = _deny_decision()

    with pytest.raises(PermissionError):
        await service.create(
            instruction_id="instr-001",
            value_date="2026-07-01",
            amount=100.0,
            subject=subject,
        )

    service.event_repo.record_policy_denial.assert_awaited_once()
    service.repo.insert.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_single_use_mark_used_state_error(
    service: PaymentService,
    subject: Subject,
    standing_instruction: dict,
) -> None:
    standing_instruction["status"] = "SINGLE_USE"
    service.ilm.get_instruction.return_value = standing_instruction
    service.authz.evaluate_payment.return_value = _allow_decision()
    service.ilm.mark_used.side_effect = InstructionStateError("already used")

    with pytest.raises(ValueError, match="already used"):
        await service.create(
            instruction_id="instr-001",
            value_date="2026-07-01",
            amount=100.0,
            subject=subject,
        )

    service.event_repo.record_policy_denial.assert_awaited_once()


@pytest.mark.asyncio
async def test_create_single_use_mark_used_runtime_error(
    service: PaymentService,
    subject: Subject,
    standing_instruction: dict,
) -> None:
    standing_instruction["status"] = "SINGLE_USE"
    service.ilm.get_instruction.return_value = standing_instruction
    service.authz.evaluate_payment.return_value = _allow_decision()
    service.ilm.mark_used.side_effect = RuntimeError("network down")

    with pytest.raises(RuntimeError, match="Could not mark instruction"):
        await service.create(
            instruction_id="instr-001",
            value_date="2026-07-01",
            amount=100.0,
            subject=subject,
        )


@pytest.mark.asyncio
async def test_submit_success(
    service: PaymentService,
    subject: Subject,
    payment: Payment,
    standing_instruction: dict,
) -> None:
    service.repo.find_by_id.return_value = payment
    service.ilm.get_instruction.return_value = standing_instruction
    service.authz.evaluate_payment.return_value = _allow_decision()

    with patch("ps.service.kafka_publisher.publish_payment", new_callable=AsyncMock):
        result = await service.submit(payment.payment_id, subject)

    assert result.status == PaymentStatus.SUBMITTED
    assert result.submitted_by is not None
    service.repo.update.assert_awaited_once()


@pytest.mark.asyncio
async def test_submit_wrong_status(
    service: PaymentService,
    subject: Subject,
    submitted_payment: Payment,
) -> None:
    service.repo.find_by_id.return_value = submitted_payment
    with pytest.raises(ValueError, match="only DRAFT"):
        await service.submit(submitted_payment.payment_id, subject)


@pytest.mark.asyncio
async def test_submit_instruction_not_found(
    service: PaymentService,
    subject: Subject,
    payment: Payment,
) -> None:
    service.repo.find_by_id.return_value = payment
    service.ilm.get_instruction.side_effect = InstructionNotFoundError("missing")
    with pytest.raises(ValueError, match="backing instruction"):
        await service.submit(payment.payment_id, subject)


@pytest.mark.asyncio
async def test_submit_policy_denied(
    service: PaymentService,
    subject: Subject,
    payment: Payment,
    standing_instruction: dict,
) -> None:
    service.repo.find_by_id.return_value = payment
    service.ilm.get_instruction.return_value = standing_instruction
    service.authz.evaluate_payment.return_value = _deny_decision("NO_LIMIT_GROUP_ASSIGNED")

    with pytest.raises(PermissionError):
        await service.submit(payment.payment_id, subject)


@pytest.mark.asyncio
async def test_approve_success(
    service: PaymentService,
    approver_subject: Subject,
    submitted_payment: Payment,
    standing_instruction: dict,
) -> None:
    service.repo.find_by_id.return_value = submitted_payment
    service.ilm.get_instruction.return_value = standing_instruction
    service.authz.evaluate_payment.return_value = _allow_decision(basis=["approver authorized"])

    with patch("ps.service.kafka_publisher.publish_payment", new_callable=AsyncMock):
        result = await service.approve(submitted_payment.payment_id, approver_subject)

    assert result.status == PaymentStatus.APPROVED
    assert result.approved_by is not None


@pytest.mark.asyncio
async def test_approve_wrong_status(
    service: PaymentService,
    approver_subject: Subject,
    payment: Payment,
) -> None:
    service.repo.find_by_id.return_value = payment
    with pytest.raises(ValueError, match="only SUBMITTED"):
        await service.approve(payment.payment_id, approver_subject)


@pytest.mark.asyncio
async def test_approve_cancels_when_instruction_missing(
    service: PaymentService,
    approver_subject: Subject,
    submitted_payment: Payment,
) -> None:
    service.repo.find_by_id.return_value = submitted_payment
    service.ilm.get_instruction.side_effect = InstructionNotFoundError("gone")

    with patch("ps.service.kafka_publisher.publish_payment", new_callable=AsyncMock):
        result = await service.approve(submitted_payment.payment_id, approver_subject)

    assert result.status == PaymentStatus.CANCELLED
    assert result.cancellation_reason is not None
    assert "could not be found" in result.cancellation_reason


@pytest.mark.asyncio
async def test_approve_cancels_on_instruction_invalidity(
    service: PaymentService,
    approver_subject: Subject,
    submitted_payment: Payment,
    standing_instruction: dict,
) -> None:
    standing_instruction["version_number"] = 99
    service.repo.find_by_id.return_value = submitted_payment
    service.ilm.get_instruction.return_value = standing_instruction

    with patch("ps.service.kafka_publisher.publish_payment", new_callable=AsyncMock):
        result = await service.approve(submitted_payment.payment_id, approver_subject)

    assert result.status == PaymentStatus.CANCELLED
    assert "version" in (result.cancellation_reason or "").lower()


@pytest.mark.asyncio
async def test_approve_policy_denied(
    service: PaymentService,
    approver_subject: Subject,
    submitted_payment: Payment,
    standing_instruction: dict,
) -> None:
    service.repo.find_by_id.return_value = submitted_payment
    service.ilm.get_instruction.return_value = standing_instruction
    service.authz.evaluate_payment.return_value = _deny_decision("ALERT_SUBORDINATE_APPROVING_CREATOR")

    with pytest.raises(PermissionError):
        await service.approve(submitted_payment.payment_id, approver_subject)


@pytest.mark.asyncio
async def test_reject_success(
    service: PaymentService,
    approver_subject: Subject,
    submitted_payment: Payment,
    standing_instruction: dict,
) -> None:
    service.repo.find_by_id.return_value = submitted_payment
    service.ilm.get_instruction.return_value = standing_instruction
    service.authz.evaluate_payment.return_value = _allow_decision()
    request = RejectPaymentRequest(reason="Insufficient documentation")

    with patch("ps.service.kafka_publisher.publish_payment", new_callable=AsyncMock):
        result = await service.reject(submitted_payment.payment_id, approver_subject, request)

    assert result.status == PaymentStatus.REJECTED
    assert result.rejection_reason == "Insufficient documentation"


@pytest.mark.asyncio
async def test_reject_wrong_status(
    service: PaymentService,
    approver_subject: Subject,
    payment: Payment,
) -> None:
    service.repo.find_by_id.return_value = payment
    with pytest.raises(ValueError, match="only SUBMITTED"):
        await service.reject(
            payment.payment_id,
            approver_subject,
            RejectPaymentRequest(reason="n/a"),
        )


@pytest.mark.asyncio
async def test_reject_instruction_not_found(
    service: PaymentService,
    approver_subject: Subject,
    submitted_payment: Payment,
) -> None:
    service.repo.find_by_id.return_value = submitted_payment
    service.ilm.get_instruction.side_effect = InstructionNotFoundError("missing")
    with pytest.raises(ValueError, match="backing instruction"):
        await service.reject(
            submitted_payment.payment_id,
            approver_subject,
            RejectPaymentRequest(reason="n/a"),
        )


@pytest.mark.asyncio
async def test_reject_policy_denied(
    service: PaymentService,
    approver_subject: Subject,
    submitted_payment: Payment,
    standing_instruction: dict,
) -> None:
    service.repo.find_by_id.return_value = submitted_payment
    service.ilm.get_instruction.return_value = standing_instruction
    service.authz.evaluate_payment.return_value = _deny_decision()

    with pytest.raises(PermissionError):
        await service.reject(
            submitted_payment.payment_id,
            approver_subject,
            RejectPaymentRequest(reason="n/a"),
        )


@pytest.mark.asyncio
async def test_get_and_list(service: PaymentService, payment: Payment, subject: Subject) -> None:
    service.repo.find_by_id.return_value = payment
    service.repo.list.return_value = [payment]

    got = await service.get(payment.payment_id, subject)
    assert got.payment_id == payment.payment_id

    items = await service.list(
        subject,
        instruction_id="instr-001",
        status="DRAFT",
        limit=10,
    )
    assert len(items) == 1
    service.repo.list.assert_awaited_once_with(
        instruction_id="instr-001",
        status="DRAFT",
        limit=10,
    )


@pytest.mark.asyncio
async def test_get_denied_for_unrelated_subject(
    service: PaymentService,
    payment: Payment,
) -> None:
    service.repo.find_by_id.return_value = payment
    outsider = Subject(
        user_id="outsider",
        title="Analyst",
        lob="OTHER",
        roles=["PAYMENT_CREATOR"],
        covering_lobs=["OTHER"],
    )
    with pytest.raises(PermissionError, match="not authorized"):
        await service.get(payment.payment_id, outsider)


@pytest.mark.asyncio
async def test_list_filters_to_viewable_payments(
    service: PaymentService,
    payment: Payment,
    subject: Subject,
) -> None:
    hidden = payment.model_copy(deep=True)
    hidden.payment_id = "pay-hidden"
    hidden.owning_lob = "FX"
    hidden.created_by = hidden.created_by.model_copy(update={"user_id": "someone-else"})
    service.repo.list.return_value = [payment, hidden]

    items = await service.list(subject)
    assert [item.payment_id for item in items] == [payment.payment_id]


@pytest.mark.asyncio
async def test_get_not_found(service: PaymentService, subject: Subject) -> None:
    service.repo.find_by_id.side_effect = PaymentNotFoundError("pay-missing")
    with pytest.raises(LookupError, match="pay-missing"):
        await service.get("pay-missing", subject)


@pytest.mark.asyncio
async def test_publish_payment_fact_swallows_kafka_errors(
    service: PaymentService,
    payment: Payment,
) -> None:
    with patch(
        "ps.service.kafka_publisher.publish_payment",
        new_callable=AsyncMock,
        side_effect=RuntimeError("kafka down"),
    ):
        await service._publish_payment_fact(payment)
