"""Fact event published to the ssi-instructions Kafka topic.

Every instruction mutation publishes one of these after the MongoDB transaction
commits.  The ETL consumes this topic to maintain the instruction master graph
in Neo4j and instruction-state Qdrant points — without ever calling back to the
ILM API or MongoDB.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from inst.models.api import Subject
from inst.models.enums import LifecycleAction
from inst.models.fact_validation import validate_instruction_snapshot
from inst.models.instruction import CashSettlementInstruction


class InstructionFact(BaseModel):
    """Thin coordination record + full instruction snapshot."""

    fact_id: str = Field(default_factory=lambda: str(uuid4()))
    instruction_id: str
    version_number: int
    action: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    actor_user_id: str
    actor_given_name: str | None = None
    actor_family_name: str | None = None
    actor_title: str
    actor_lob: str | None = None
    actor_roles: list[str] = Field(default_factory=list)
    actor_supervisor_id: str | None = None

    instruction_snapshot: dict[str, Any] = Field(
        description="Full serialized CashSettlementInstruction at mutation time"
    )
    authorization: dict[str, Any] | None = Field(
        default=None,
        description="OPA decision context for this mutation (allow_basis, summary, violations)",
    )

    @classmethod
    def from_instruction(
        cls,
        action: LifecycleAction,
        subject: Subject,
        instruction: CashSettlementInstruction,
        *,
        version_number: int,
        authorization: dict[str, Any] | None = None,
    ) -> "InstructionFact":
        snapshot = instruction.model_dump(mode="json")
        validate_instruction_snapshot(
            snapshot,
            action=action.value,
            version_number=version_number,
        )
        return cls(
            instruction_id=instruction.instruction_id,
            version_number=version_number,
            action=action.value,
            actor_user_id=subject.user_id,
            actor_given_name=subject.given_name,
            actor_family_name=subject.family_name,
            actor_title=subject.title,
            actor_lob=subject.lob,
            actor_roles=subject.roles,
            actor_supervisor_id=subject.supervisor_id,
            instruction_snapshot=snapshot,
            authorization=authorization,
        )
