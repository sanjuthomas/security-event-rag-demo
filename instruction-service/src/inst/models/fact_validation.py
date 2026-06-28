"""Validate instruction/payment Kafka facts carry full cumulative state."""

from __future__ import annotations

from typing import Any


def _require(snapshot: dict[str, Any], field: str, *, action: str, entity: str) -> None:
    value = snapshot.get(field)
    if value is None or value == "" or value == []:
        raise ValueError(
            f"{entity} fact missing required field {field!r} for action {action}"
        )


def _require_user_ref(snapshot: dict[str, Any], field: str, *, action: str, entity: str) -> None:
    _require(snapshot, field, action=action, entity=entity)
    user = snapshot.get(field)
    if not isinstance(user, dict) or not user.get("user_id"):
        raise ValueError(
            f"{entity} fact {field}.user_id required for action {action}"
        )


def validate_instruction_snapshot(
    snapshot: dict[str, Any],
    *,
    action: str,
    version_number: int,
) -> None:
    """Ensure instruction_snapshot is a complete cumulative document for the indexer."""
    entity = "instruction"
    for field in (
        "instruction_id",
        "status",
        "created_by",
        "lifecycle_events",
        "effective_date",
        "end_date",
    ):
        _require(snapshot, field, action=action, entity=entity)

    _require_user_ref(snapshot, "created_by", action=action, entity=entity)

    events = snapshot.get("lifecycle_events")
    if not isinstance(events, list) or not events:
        raise ValueError(f"{entity} fact lifecycle_events must be a non-empty list")

    if version_number < 1:
        raise ValueError(f"{entity} fact version_number must be >= 1")

    action_fields: dict[str, list[str]] = {
        "SUBMIT": ["submitted_at"],
        "APPROVE": ["approved_by", "approved_at"],
        "REJECT": ["rejected_by", "rejected_at", "rejection_reason"],
        "SUSPEND": ["suspended_by", "suspended_at"],
    }
    for field in action_fields.get(action, []):
        _require(snapshot, field, action=action, entity=entity)
    if action == "APPROVE":
        _require_user_ref(snapshot, "approved_by", action=action, entity=entity)
    if action == "REJECT":
        _require_user_ref(snapshot, "rejected_by", action=action, entity=entity)

    # Cumulative: approval metadata must survive later mutations when present on source.
    if action in {"UPDATE", "SUSPEND", "REACTIVATE", "USE", "DELETE"}:
        if snapshot.get("approved_by") is not None:
            _require_user_ref(snapshot, "approved_by", action=action, entity=entity)
