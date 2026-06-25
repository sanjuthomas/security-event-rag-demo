from dataclasses import dataclass
from datetime import datetime
from typing import Any

from instruction_lifecycle_manager.models.instruction import CashSettlementInstruction


@dataclass(frozen=True)
class VersionedInstruction:
    instruction: CashSettlementInstruction
    version_number: int
    valid_in: datetime
    valid_out: datetime | None


def _format_timestamp(value: datetime) -> str:
    return value.isoformat() + "Z"


def _parse_timestamp(value: str | datetime | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    normalized = value.replace("Z", "+00:00")
    return datetime.fromisoformat(normalized).replace(tzinfo=None)


def versioned_instruction_to_document(
    instruction: CashSettlementInstruction,
    *,
    version_number: int,
    valid_in: datetime,
    valid_out: datetime | None = None,
) -> dict[str, Any]:
    return {
        "instruction_id": instruction.instruction_id,
        "version_number": version_number,
        "in": _format_timestamp(valid_in),
        "out": _format_timestamp(valid_out) if valid_out else None,
        "status": instruction.status.value,
        "owning_lob": instruction.owning_lob,
        "wire_scope": instruction.wire_scope.value,
        "payload": instruction.model_dump(mode="json"),
    }


def document_to_versioned_instruction(document: dict[str, Any]) -> VersionedInstruction:
    payload = dict(document.get("payload", document))
    payload.pop("_id", None)
    instruction = CashSettlementInstruction.model_validate(payload)
    return VersionedInstruction(
        instruction=instruction,
        version_number=document["version_number"],
        valid_in=_parse_timestamp(document["in"]) or instruction.created_at,
        valid_out=_parse_timestamp(document.get("out")),
    )
