from datetime import datetime

import pytest

from inst.models.api import UserReference
from inst.models.enums import InstructionStatus, LifecycleAction
from inst.models.fact_validation import validate_instruction_snapshot
from inst.models.instruction import LifecycleEvent
from inst.models.instruction_fact import InstructionFact


def _with_lifecycle(sample_instruction, sample_subject, action: str = "CREATE") -> None:
    if not sample_instruction.lifecycle_events:
        sample_instruction.lifecycle_events.append(
            LifecycleEvent(action=action, actor_user_id=sample_subject.user_id)
        )


def test_instruction_fact_from_instruction(sample_subject, sample_instruction) -> None:
    _with_lifecycle(sample_instruction, sample_subject)
    fact = InstructionFact.from_instruction(
        LifecycleAction.CREATE,
        sample_subject,
        sample_instruction,
        version_number=1,
        authorization={"decision": "allow"},
    )
    assert fact.instruction_id == sample_instruction.instruction_id
    assert fact.version_number == 1
    assert fact.action == "CREATE"
    assert fact.actor_user_id == sample_subject.user_id
    assert fact.authorization == {"decision": "allow"}
    assert fact.instruction_snapshot["instruction_id"] == sample_instruction.instruction_id


def test_instruction_fact_create_has_cumulative_snapshot(
    sample_subject, sample_instruction
) -> None:
    _with_lifecycle(sample_instruction, sample_subject)
    fact = InstructionFact.from_instruction(
        LifecycleAction.CREATE,
        sample_subject,
        sample_instruction,
        version_number=1,
    )
    snap = fact.instruction_snapshot
    assert snap["created_by"]["user_id"]
    assert len(snap["lifecycle_events"]) >= 1


def test_instruction_fact_approve_requires_approver(
    sample_subject, sample_instruction
) -> None:
    _with_lifecycle(sample_instruction, sample_subject)
    sample_instruction.status = InstructionStatus.PENDING
    sample_instruction.approved_by = None
    with pytest.raises(ValueError, match="approved_by"):
        InstructionFact.from_instruction(
            LifecycleAction.APPROVE,
            sample_subject,
            sample_instruction,
            version_number=2,
        )


def test_instruction_fact_approve_includes_creator_and_approver(
    sample_subject, sample_instruction
) -> None:
    _with_lifecycle(sample_instruction, sample_subject)
    sample_instruction.status = InstructionStatus.PENDING
    sample_instruction.approved_by = UserReference(
        user_id=sample_subject.user_id,
        given_name=sample_subject.given_name,
        family_name=sample_subject.family_name,
        title=sample_subject.title,
        lob=sample_subject.lob,
        roles=sample_subject.roles,
        supervisor_id=sample_subject.supervisor_id,
    )
    sample_instruction.approved_at = datetime.utcnow()
    fact = InstructionFact.from_instruction(
        LifecycleAction.APPROVE,
        sample_subject,
        sample_instruction,
        version_number=2,
    )
    snap = fact.instruction_snapshot
    assert snap["created_by"]["user_id"]
    assert snap["approved_by"]["user_id"] == sample_subject.user_id


def test_validate_instruction_snapshot_rejects_empty_lifecycle() -> None:
    with pytest.raises(ValueError, match="lifecycle_events"):
        validate_instruction_snapshot(
            {
                "instruction_id": "i1",
                "status": "DRAFT",
                "created_by": {"user_id": "u1"},
                "lifecycle_events": [],
                "effective_date": "2024-01-01",
                "end_date": "2024-12-31",
            },
            action="CREATE",
            version_number=1,
        )
