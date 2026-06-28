from __future__ import annotations

from typing import Any

from etl.authorization_context import (
    authorization_merged_fields,
    authorization_merged_from_fact,
)


def instruction_state_context(fact: dict[str, Any]) -> dict[str, Any]:
    """Document root for instruction_state search profile."""
    return {**fact, **authorization_merged_from_fact(fact)}


def payment_security_event_context(event: dict[str, Any]) -> dict[str, Any]:
    """Document root for payment_security_event search profile."""
    return {**event, **authorization_merged_fields(event)}
