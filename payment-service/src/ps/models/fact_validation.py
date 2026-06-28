"""Validate payment Kafka facts carry full cumulative state."""

from __future__ import annotations

from typing import Any


def _require(doc: dict[str, Any], field: str, *, action: str) -> None:
    value = doc.get(field)
    if value is None or value == "" or value == []:
        raise ValueError(f"payment fact missing required field {field!r} for action {action}")


def _require_user_ref(doc: dict[str, Any], field: str, *, action: str) -> None:
    _require(doc, field, action=action)
    user = doc.get(field)
    if not isinstance(user, dict) or not user.get("user_id"):
        raise ValueError(f"payment fact {field}.user_id required for action {action}")


def validate_payment_document(doc: dict[str, Any], *, action: str) -> None:
    """Ensure the published payment fact is a complete cumulative document."""
    for field in (
        "payment_id",
        "instruction_id",
        "instruction_version",
        "version_number",
        "status",
        "amount",
        "currency",
        "value_date",
        "owning_lob",
        "instruction_type",
        "created_by",
        "lifecycle_events",
        "created_at",
        "updated_at",
    ):
        _require(doc, field, action=action)

    _require_user_ref(doc, "created_by", action=action)

    events = doc.get("lifecycle_events")
    if not isinstance(events, list) or not events:
        raise ValueError("payment fact lifecycle_events must be a non-empty list")

    version_number = doc.get("version_number")
    if not isinstance(version_number, int) or version_number < 1:
        raise ValueError("payment fact version_number must be a positive integer")
    if len(events) != version_number:
        raise ValueError(
            "payment fact version_number must equal len(lifecycle_events) "
            f"(got version_number={version_number}, events={len(events)})"
        )

    action_fields: dict[str, list[tuple[str, bool]]] = {
        "SUBMIT_PAYMENT": [("submitted_by", True)],
        "APPROVE_PAYMENT": [("approved_by", True), ("created_by", True)],
        "REJECT_PAYMENT": [("rejected_by", True), ("rejection_reason", False)],
        "CANCEL_PAYMENT": [("cancelled_by", True), ("cancellation_reason", False)],
    }
    for field, is_user_ref in action_fields.get(action, []):
        if is_user_ref:
            _require_user_ref(doc, field, action=action)
        else:
            _require(doc, field, action=action)

    # Cumulative: creator and prior party refs must remain after approve/reject/cancel.
    if action in {"APPROVE_PAYMENT", "REJECT_PAYMENT", "CANCEL_PAYMENT", "SUBMIT_PAYMENT"}:
        _require_user_ref(doc, "created_by", action=action)
    if action in {"REJECT_PAYMENT", "CANCEL_PAYMENT", "APPROVE_PAYMENT"} and doc.get("submitted_by"):
        _require_user_ref(doc, "submitted_by", action=action)
