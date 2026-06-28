"""Tests for etl.enrichment."""

from __future__ import annotations

from etl.enrichment import (
    EnrichedSecurityEventDocument,
    build_merged_context,
    build_search_text,
    enrich_document,
)


def _sample_event() -> dict:
    return {
        "event_id": "evt-1",
        "timestamp": "2024-01-01T00:00:00Z",
        "severity": "ALERT",
        "message": "access denied",
        "actor": {
            "user_id": "u1",
            "given_name": "Jane",
            "family_name": "Doe",
            "title": "Analyst",
            "roles": ["viewer"],
            "lob": "LOB-A",
            "supervisor_id": "sup1",
        },
        "resource": {
            "id": "instr-1",
            "version_number": 2,
            "owning_lob": "LOB-A",
            "status": "ACTIVE",
            "instruction_type": "WIRE",
        },
        "event": {
            "action": "READ",
            "outcome": "DENY",
            "reason": "not authorized",
        },
        "details": {
            "authorization": {
                "summary": "denied by policy",
                "decision": "DENY",
                "allow_basis": [],
                "violations": ["missing role"],
            }
        },
    }


def _sample_instruction() -> dict:
    return {
        "instruction_id": "instr-1",
        "version_number": 3,
        "instruction_type": "WIRE",
        "wire_scope": "DOMESTIC",
        "owning_lob": "LOB-A",
        "status": "ACTIVE",
        "currency": "USD",
        "effective_date": "2024-01-01",
        "end_date": "2024-12-31",
        "usage_count": 5,
        "creditor": {"name": "Creditor Inc"},
        "debtor": {"name": "Debtor LLC"},
        "creditor_account": {"identification": "ACC-1", "identification_scheme": "IBAN"},
        "debtor_account": {"identification": "ACC-2"},
        "creditor_agent": {"financial_institution": {"identification": "BIC123"}},
        "created_by": {
            "user_id": "c1",
            "given_name": "Creator",
            "family_name": "One",
            "title": "Mgr",
            "lob": "LOB-A",
            "supervisor_id": "cs1",
        },
        "approved_by": {
            "user_id": "a1",
            "given_name": "Approver",
            "family_name": "Two",
            "title": "Dir",
            "lob": "LOB-A",
            "supervisor_id": "as1",
        },
        "rejected_by": {
            "user_id": "r1",
            "given_name": "Rejector",
            "family_name": "Three",
            "title": "VP",
            "lob": "LOB-B",
            "supervisor_id": "rs1",
        },
    }


def test_build_merged_context_with_instruction():
    event = _sample_event()
    instruction = _sample_instruction()
    merged = build_merged_context(event, instruction)

    assert merged["actor_user_id"] == "u1"
    assert merged["actor_display"] == "Doe, Jane (u1)"
    assert merged["instruction_id"] == "instr-1"
    assert merged["version_number"] == 2  # resource takes precedence
    assert merged["creditor_name"] == "Creditor Inc"
    assert merged["creditor_agent_bic"] == "BIC123"
    assert merged["creator_display"] == "One, Creator (c1)"
    assert merged["approver_display"] == "Two, Approver (a1)"
    assert merged["authorization_decision"] == "DENY"


def test_build_merged_context_uses_instruction_snapshot():
    event = _sample_event()
    event["instruction_snapshot"] = {
        "instruction_id": "snap-id",
        "version_number": 9,
        "status": "PENDING",
        "created_by": {"user_id": "snap-creator"},
    }
    merged = build_merged_context(event, None)
    assert merged["instruction_id"] == "instr-1"  # resource id still used
    assert merged["status"] == "PENDING"
    assert merged["creator_user_id"] == "snap-creator"


def test_build_merged_context_display_name_user_id_only():
    event = {"actor": {"user_id": "only-id"}}
    merged = build_merged_context(event, None)
    assert merged["actor_display"] == "only-id"


def test_build_search_text_includes_key_fields():
    event = _sample_event()
    instruction = _sample_instruction()
    text = build_search_text(event, instruction)
    assert "access denied" in text
    assert "ALERT" in text
    assert "READ" in text
    assert "DENY" in text
    assert "u1" in text
    assert "Creditor Inc" in text
    assert "denied by policy" in text


def test_build_search_text_with_prebuilt_merged():
    event = _sample_event()
    merged = build_merged_context(event, None)
    text = build_search_text(event, None, merged)
    assert "access denied" in text


def test_enrich_document():
    event = _sample_event()
    instruction = _sample_instruction()
    doc = enrich_document(event, instruction)

    assert isinstance(doc, EnrichedSecurityEventDocument)
    assert doc.event_id == "evt-1"
    assert doc.instruction_id == "instr-1"
    assert doc.version_number == 3  # instruction overrides resource
    assert doc.instruction == instruction
    assert doc.merged
    assert doc.search_text
    assert "access denied" in doc.search_text


def test_enrich_document_without_instruction():
    event = _sample_event()
    doc = enrich_document(event, None)
    assert doc.version_number == 2
    assert doc.instruction is None
