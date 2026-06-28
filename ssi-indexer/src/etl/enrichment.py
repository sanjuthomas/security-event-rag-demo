from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from etl.authorization_context import authorization_merged_fields
from etl.search_text.builder import build_search_text_from_profile


def _display_name(user: dict[str, Any]) -> str:
    """Return 'Family, Given (user_id)' when names are known, else just user_id."""
    fn = user.get("family_name")
    gn = user.get("given_name")
    uid = user.get("user_id") or ""
    if fn and gn and uid:
        return f"{fn}, {gn} ({uid})"
    return uid


class EnrichedSecurityEventDocument(BaseModel):
    """Security event merged with the current instruction from ILM API."""

    event_id: str
    instruction_id: str
    version_number: int | None = None
    security_event: dict[str, Any]
    instruction: dict[str, Any] | None = None
    merged: dict[str, Any] = Field(
        default_factory=dict,
        description="Denormalized actor + instruction parties for analytics and search",
    )
    search_text: str = Field(
        description="Flattened text used for dense embeddings and BM25 lexical index"
    )


def build_merged_context(
    security_event: dict[str, Any],
    instruction: dict[str, Any] | None,
) -> dict[str, Any]:
    actor = security_event.get("actor") or {}
    resource = security_event.get("resource") or {}
    event_ctx = security_event.get("event") or {}
    # Prefer the embedded instruction_snapshot (fact-event model); fall back to
    # a separately fetched instruction dict for backward compatibility.
    instr = security_event.get("instruction_snapshot") or instruction or {}

    created_by = instr.get("created_by") or {}
    approved_by = instr.get("approved_by") or {}
    rejected_by = instr.get("rejected_by") or {}

    creditor_account = instr.get("creditor_account") or {}
    debtor_account = instr.get("debtor_account") or {}
    creditor = instr.get("creditor") or {}
    debtor = instr.get("debtor") or {}
    creditor_agent_fi = (instr.get("creditor_agent") or {}).get("financial_institution") or {}

    return {
        "timestamp": security_event.get("timestamp"),
        "severity": security_event.get("severity"),
        "message": security_event.get("message"),
        "action": event_ctx.get("action"),
        "outcome": event_ctx.get("outcome"),
        "reason": event_ctx.get("reason"),
        # Actor (subject who performed the action)
        "actor_user_id": actor.get("user_id"),
        "actor_given_name": actor.get("given_name"),
        "actor_family_name": actor.get("family_name"),
        "actor_display": _display_name(actor),
        "actor_title": actor.get("title"),
        "actor_roles": actor.get("roles") or [],
        "actor_lob": actor.get("lob"),
        "actor_supervisor_id": actor.get("supervisor_id"),
        # Instruction metadata
        "instruction_id": resource.get("id") or instr.get("instruction_id"),
        "version_number": resource.get("version_number") or instr.get("version_number"),
        "instruction_type": instr.get("instruction_type") or resource.get("instruction_type"),
        "wire_scope": instr.get("wire_scope"),
        "owning_lob": instr.get("owning_lob") or resource.get("owning_lob"),
        "status": instr.get("status") or resource.get("status"),
        "currency": instr.get("currency"),
        "effective_date": instr.get("effective_date"),
        "end_date": instr.get("end_date"),
        "usage_count": instr.get("usage_count"),
        # Counterparty details — creditor and debtor for duplicate-route and conflict detection
        "creditor_name": creditor.get("name"),
        "creditor_account_id": creditor_account.get("identification"),
        "creditor_account_scheme": creditor_account.get("identification_scheme"),
        "debtor_name": debtor.get("name"),
        "debtor_account_id": debtor_account.get("identification"),
        "creditor_agent_bic": creditor_agent_fi.get("identification"),
        # Instruction parties (full detail for graph traversal queries)
        "creator_user_id": created_by.get("user_id"),
        "creator_given_name": created_by.get("given_name"),
        "creator_family_name": created_by.get("family_name"),
        "creator_display": _display_name(created_by),
        "creator_title": created_by.get("title"),
        "creator_lob": created_by.get("lob"),
        "creator_supervisor_id": created_by.get("supervisor_id"),
        "approver_user_id": approved_by.get("user_id"),
        "approver_given_name": approved_by.get("given_name"),
        "approver_family_name": approved_by.get("family_name"),
        "approver_display": _display_name(approved_by),
        "approver_title": approved_by.get("title"),
        "approver_lob": approved_by.get("lob"),
        "approver_supervisor_id": approved_by.get("supervisor_id"),
        "rejector_user_id": rejected_by.get("user_id"),
        "rejector_given_name": rejected_by.get("given_name"),
        "rejector_family_name": rejected_by.get("family_name"),
        "rejector_display": _display_name(rejected_by),
        "rejector_title": rejected_by.get("title"),
        "rejector_lob": rejected_by.get("lob"),
        "rejector_supervisor_id": rejected_by.get("supervisor_id"),
        **authorization_merged_fields(security_event),
    }


def build_search_text(
    security_event: dict[str, Any],
    instruction: dict[str, Any] | None,
    merged: dict[str, Any] | None = None,
) -> str:
    ctx = merged or build_merged_context(security_event, instruction)
    return build_search_text_from_profile("instruction_security_event", ctx)


def enrich_document(
    security_event: dict[str, Any],
    instruction: dict[str, Any] | None,
) -> EnrichedSecurityEventDocument:
    resource = security_event.get("resource") or {}
    instruction_id = resource.get("id", "")
    version_number = resource.get("version_number")
    if instruction:
        version_number = instruction.get("version_number", version_number)

    merged = build_merged_context(security_event, instruction)

    return EnrichedSecurityEventDocument(
        event_id=security_event["event_id"],
        instruction_id=instruction_id,
        version_number=version_number,
        security_event=security_event,
        instruction=instruction,
        merged=merged,
        search_text=build_search_text(security_event, instruction, merged),
    )
