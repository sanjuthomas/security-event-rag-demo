from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from inst.authorization import PolicyDecision
from inst.models.api import (
    CreateInstructionRequest,
    DeleteInstructionRequest,
    RejectInstructionRequest,
    Subject,
    UseInstructionRequest,
)
from inst.models.enums import InstructionStatus, InstructionType
from inst.models.instruction import CashSettlementInstruction
from inst.service import InstructionService, InvalidStateTransitionError
from inst.storage import VersionedInstruction


def _allowed_decision() -> PolicyDecision:
    return PolicyDecision(
        allowed=True,
        allow_basis=["policy ok"],
        violations=[],
        is_alert=False,
    )


def _denied_decision(*, is_alert: bool = False) -> PolicyDecision:
    return PolicyDecision(
        allowed=False,
        allow_basis=[],
        violations=["MISSING_ROLE_INSTRUCTION_CREATOR"],
        is_alert=is_alert,
    )


@pytest.fixture
def mock_repo() -> MagicMock:
    repo = MagicMock()
    repo.get_current = AsyncMock()
    repo.list_versions = AsyncMock(return_value=[])
    repo.list_current = AsyncMock(return_value=[])
    return repo


@pytest.fixture
def mock_authz() -> AsyncMock:
    authz = AsyncMock()
    authz.evaluate_instruction = AsyncMock(return_value=_allowed_decision())
    return authz


@pytest.fixture
def mock_security_events() -> MagicMock:
    events = MagicMock()
    events.allocate_event_id = AsyncMock(return_value="instr-001-SE-1")
    events.record_policy_denial = AsyncMock()
    events.record_authorized_action = AsyncMock()
    events.insert_document = AsyncMock()
    events.publish = AsyncMock()
    return events


@pytest.fixture
def service(
    mock_repo: MagicMock,
    mock_authz: AsyncMock,
    mock_security_events: MagicMock,
) -> InstructionService:
    sequence_client = AsyncMock()
    sequence_client.next_instruction_id = AsyncMock(return_value="instr-001")
    return InstructionService(
        repository=mock_repo,
        authz_client=mock_authz,
        security_events=mock_security_events,
        sequence_client=sequence_client,
    )


def _versioned(instruction: CashSettlementInstruction, version: int = 1) -> VersionedInstruction:
    return VersionedInstruction(
        instruction=instruction,
        version_number=version,
        valid_in=datetime.utcnow(),
        valid_out=None,
    )


@pytest.mark.asyncio
async def test_create_success(
    service: InstructionService,
    mock_repo: MagicMock,
    sample_create_request: CreateInstructionRequest,
    sample_subject: Subject,
    sample_instruction: CashSettlementInstruction,
) -> None:
    saved = _versioned(sample_instruction)
    mock_repo.insert_initial = AsyncMock(return_value=saved)

    with patch("inst.service.mongo_transaction") as mock_tx:
        mock_tx.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
        mock_tx.return_value.__aexit__ = AsyncMock(return_value=False)
        with patch("inst.service.kafka_publisher") as mock_kafka:
            mock_kafka.publish_instruction_fact = AsyncMock()
            response = await service.create(sample_create_request, sample_subject)

    assert response.instruction_id == "instr-001"
    mock_repo.insert_initial.assert_awaited_once()


@pytest.mark.asyncio
async def test_create_denied_records_policy_denial(
    service: InstructionService,
    mock_authz: AsyncMock,
    mock_security_events: MagicMock,
    sample_create_request: CreateInstructionRequest,
    sample_subject: Subject,
) -> None:
    mock_authz.evaluate_instruction = AsyncMock(return_value=_denied_decision())

    with pytest.raises(PermissionError):
        await service.create(sample_create_request, sample_subject)

    mock_security_events.record_policy_denial.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_success(
    service: InstructionService,
    mock_repo: MagicMock,
    sample_subject: Subject,
    sample_instruction: CashSettlementInstruction,
) -> None:
    mock_repo.get_current = AsyncMock(return_value=_versioned(sample_instruction))
    response = await service.get("instr-001", sample_subject)
    assert response.instruction_id == "instr-001"


@pytest.mark.asyncio
async def test_update_rejects_non_draft(
    service: InstructionService,
    mock_repo: MagicMock,
    sample_create_request: CreateInstructionRequest,
    sample_subject: Subject,
    sample_instruction: CashSettlementInstruction,
) -> None:
    pending = sample_instruction.model_copy(update={"status": InstructionStatus.PENDING})
    mock_repo.get_current = AsyncMock(return_value=_versioned(pending))

    with pytest.raises(InvalidStateTransitionError, match="DRAFT"):
        await service.update("instr-001", sample_create_request, sample_subject)


@pytest.mark.asyncio
async def test_delete_soft_deletes_draft(
    service: InstructionService,
    mock_repo: MagicMock,
    sample_subject: Subject,
    sample_instruction: CashSettlementInstruction,
) -> None:
    mock_repo.get_current = AsyncMock(return_value=_versioned(sample_instruction))
    deleted = sample_instruction.model_copy(update={"status": InstructionStatus.DELETED})
    mock_repo.append_version = AsyncMock(return_value=_versioned(deleted, version=2))

    with patch("inst.service.mongo_transaction") as mock_tx:
        mock_tx.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
        mock_tx.return_value.__aexit__ = AsyncMock(return_value=False)
        with patch("inst.service.kafka_publisher") as mock_kafka:
            mock_kafka.publish_instruction_fact = AsyncMock()
            response = await service.delete(
                "instr-001",
                sample_subject,
                DeleteInstructionRequest(reason="cleanup"),
            )

    assert response.status == "DELETED"


@pytest.mark.asyncio
async def test_submit_transitions_to_pending(
    service: InstructionService,
    mock_repo: MagicMock,
    sample_subject: Subject,
    sample_instruction: CashSettlementInstruction,
) -> None:
    mock_repo.get_current = AsyncMock(return_value=_versioned(sample_instruction))
    pending = sample_instruction.model_copy(update={"status": InstructionStatus.PENDING})
    mock_repo.append_version = AsyncMock(return_value=_versioned(pending, version=2))

    with patch("inst.service.mongo_transaction") as mock_tx:
        mock_tx.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
        mock_tx.return_value.__aexit__ = AsyncMock(return_value=False)
        with patch("inst.service.kafka_publisher") as mock_kafka:
            mock_kafka.publish_instruction_fact = AsyncMock()
            response = await service.submit("instr-001", sample_subject)

    assert response.status == "PENDING"


@pytest.mark.asyncio
async def test_approve_standing(
    service: InstructionService,
    mock_repo: MagicMock,
    sample_subject: Subject,
    sample_instruction: CashSettlementInstruction,
) -> None:
    pending = sample_instruction.model_copy(
        update={
            "status": InstructionStatus.PENDING,
            "instruction_type": InstructionType.STANDING,
        }
    )
    approved = pending.model_copy(update={"status": InstructionStatus.STANDING})
    mock_repo.get_current = AsyncMock(return_value=_versioned(pending))
    mock_repo.append_version = AsyncMock(return_value=_versioned(approved, version=2))

    with patch("inst.service.mongo_transaction") as mock_tx:
        mock_tx.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
        mock_tx.return_value.__aexit__ = AsyncMock(return_value=False)
        with patch("inst.service.kafka_publisher") as mock_kafka:
            mock_kafka.publish_instruction_fact = AsyncMock()
            response = await service.approve("instr-001", sample_subject)

    assert response.status == "STANDING"


@pytest.mark.asyncio
async def test_reject_pending(
    service: InstructionService,
    mock_repo: MagicMock,
    sample_subject: Subject,
    sample_instruction: CashSettlementInstruction,
) -> None:
    pending = sample_instruction.model_copy(update={"status": InstructionStatus.PENDING})
    rejected = pending.model_copy(
        update={
            "status": InstructionStatus.REJECTED,
            "rejection_reason": "bad data",
        }
    )
    mock_repo.get_current = AsyncMock(return_value=_versioned(pending))
    mock_repo.append_version = AsyncMock(return_value=_versioned(rejected, version=2))

    with patch("inst.service.mongo_transaction") as mock_tx:
        mock_tx.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
        mock_tx.return_value.__aexit__ = AsyncMock(return_value=False)
        with patch("inst.service.kafka_publisher") as mock_kafka:
            mock_kafka.publish_instruction_fact = AsyncMock()
            response = await service.reject(
                "instr-001",
                sample_subject,
                RejectInstructionRequest(reason="bad data"),
            )

    assert response.status == "REJECTED"
    assert response.rejection_reason == "bad data"


@pytest.mark.asyncio
async def test_suspend_active(
    service: InstructionService,
    mock_repo: MagicMock,
    sample_subject: Subject,
    sample_instruction: CashSettlementInstruction,
) -> None:
    active = sample_instruction.model_copy(update={"status": InstructionStatus.STANDING})
    suspended = active.model_copy(update={"status": InstructionStatus.SUSPENDED})
    mock_repo.get_current = AsyncMock(return_value=_versioned(active))
    mock_repo.append_version = AsyncMock(return_value=_versioned(suspended, version=2))

    with patch("inst.service.mongo_transaction") as mock_tx:
        mock_tx.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
        mock_tx.return_value.__aexit__ = AsyncMock(return_value=False)
        with patch("inst.service.kafka_publisher") as mock_kafka:
            mock_kafka.publish_instruction_fact = AsyncMock()
            response = await service.suspend("instr-001", sample_subject)

    assert response.status == "SUSPENDED"


@pytest.mark.asyncio
async def test_reactivate_suspended(
    service: InstructionService,
    mock_repo: MagicMock,
    sample_subject: Subject,
    sample_instruction: CashSettlementInstruction,
) -> None:
    suspended = sample_instruction.model_copy(update={"status": InstructionStatus.SUSPENDED})
    active = suspended.model_copy(update={"status": InstructionStatus.SINGLE_USE})
    mock_repo.get_current = AsyncMock(return_value=_versioned(suspended))
    mock_repo.append_version = AsyncMock(return_value=_versioned(active, version=2))

    with patch("inst.service.mongo_transaction") as mock_tx:
        mock_tx.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
        mock_tx.return_value.__aexit__ = AsyncMock(return_value=False)
        with patch("inst.service.kafka_publisher") as mock_kafka:
            mock_kafka.publish_instruction_fact = AsyncMock()
            response = await service.reactivate("instr-001", sample_subject)

    assert response.status == "SINGLE_USE"


@pytest.mark.asyncio
async def test_use_single_use_marks_used(
    service: InstructionService,
    mock_repo: MagicMock,
    sample_subject: Subject,
    sample_instruction: CashSettlementInstruction,
) -> None:
    active = sample_instruction.model_copy(update={"status": InstructionStatus.SINGLE_USE})
    used = active.model_copy(update={"status": InstructionStatus.USED, "usage_count": 1})
    mock_repo.get_current = AsyncMock(return_value=_versioned(active))
    mock_repo.append_version = AsyncMock(return_value=_versioned(used, version=2))

    with patch("inst.service.mongo_transaction") as mock_tx:
        mock_tx.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
        mock_tx.return_value.__aexit__ = AsyncMock(return_value=False)
        with patch("inst.service.kafka_publisher") as mock_kafka:
            mock_kafka.publish_instruction_fact = AsyncMock()
            response = await service.use(
                "instr-001",
                sample_subject,
                UseInstructionRequest(payment_reference="pay-123"),
            )

    assert response.status == "USED"
    assert response.usage_count == 1


@pytest.mark.asyncio
async def test_list_skips_denied(
    service: InstructionService,
    mock_repo: MagicMock,
    mock_authz: AsyncMock,
    sample_subject: Subject,
    sample_instruction: CashSettlementInstruction,
) -> None:
    mock_repo.list_current = AsyncMock(return_value=[_versioned(sample_instruction)])
    mock_authz.evaluate_instruction = AsyncMock(
        side_effect=[_denied_decision(), _allowed_decision()]
    )
    mock_repo.get_current = AsyncMock(return_value=_versioned(sample_instruction))

    visible = await service.list(sample_subject)
    assert visible == []


@pytest.mark.asyncio
async def test_should_record_security_event_excludes_etl_reader(
    sample_subject: Subject,
) -> None:
    assert InstructionService._should_record_security_event(sample_subject) is True
    excluded = sample_subject.model_copy(update={"user_id": "etl-reader"})
    assert InstructionService._should_record_security_event(excluded) is False
