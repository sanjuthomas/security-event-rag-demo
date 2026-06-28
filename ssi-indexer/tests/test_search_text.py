"""Tests for search-text helpers in instruction and payment pipelines."""

from __future__ import annotations

from etl.instruction_pipeline import _build_instruction_search_text, _display_name
from etl.payment_pipeline import (
    _build_payment_event_search_text,
    _build_payment_fact_search_text,
    _display,
    _roles_json,
)


def test_instruction_build_search_text():
    fact = {
        "instruction_id": "i-1",
        "action": "SUBMIT",
        "actor_user_id": "u1",
        "actor_given_name": "A",
        "actor_family_name": "B",
        "actor_lob": "LOB1",
        "instruction_snapshot": {
            "instruction_id": "i-1",
            "status": "PENDING",
            "instruction_type": "WIRE",
            "owning_lob": "LOB1",
            "wire_scope": "DOMESTIC",
            "currency": "USD",
            "creditor": {"name": "Cred"},
            "creditor_account": {"identification": "CA1", "identification_scheme": "IBAN"},
            "creditor_agent": {"financial_institution": {"identification": "BIC"}},
            "debtor": {"name": "Deb"},
            "debtor_account": {"identification": "DA1"},
            "effective_date": "2024-01-01",
            "end_date": "2024-12-31",
            "created_by": {"user_id": "c1", "given_name": "C", "family_name": "One", "lob": "L1"},
            "approved_by": {"user_id": "a1", "given_name": "A", "family_name": "Two", "lob": "L2"},
            "rejected_by": {"user_id": "r1", "given_name": "R", "family_name": "Three"},
            "approved_at": "2024-02-01",
        },
        "authorization": {
            "summary": "submitted",
            "decision": "PENDING",
            "allow_basis": ["rule"],
        },
    }
    text = _build_instruction_search_text(fact)
    assert "i-1" in text
    assert "WIRE" in text
    assert "Cred" in text
    assert "submitted" in text
    assert "u1" in text


def test_instruction_display_name_with_prefix():
    user = {"actor_family_name": "Smith", "actor_given_name": "John", "actor_user_id": "u99"}
    assert _display_name(user, prefix="actor_") == "Smith, John (u99)"


def test_instruction_display_name_fallback():
    assert _display_name({"user_id": "solo"}) == "solo"


def test_payment_event_search_text():
    event = {
        "message": "payment blocked",
        "timestamp": "2024-01-01",
        "severity": "ALERT",
        "actor": {
            "user_id": "p1",
            "given_name": "Pay",
            "family_name": "User",
            "title": "Clerk",
            "roles": ["pay"],
            "groups": ["g1"],
            "covering_lobs": ["LOB-P"],
            "lob": "LOB-P",
        },
        "resource": {
            "id": "pay-1",
            "instruction_id": "instr-1",
            "owning_lob": "LOB-P",
            "currency": "USD",
            "amount": 1000,
        },
        "event": {"action": "CREATE", "outcome": "DENY", "reason": "limit"},
        "payment_snapshot": {
            "value_date": "2024-01-15",
            "instruction_type": "WIRE",
            "created_by": {"user_id": "c1", "given_name": "C", "family_name": "One"},
            "approved_by": {"user_id": "a1", "given_name": "A", "family_name": "Two"},
        },
        "details": {
            "authorization": {
                "summary": "over limit",
                "decision": "DENY",
                "allow_basis": [],
                "violations": ["amount"],
            }
        },
    }
    text = _build_payment_event_search_text(event)
    assert "payment blocked" in text
    assert "pay-1" in text
    assert "over limit" in text
    assert "payment" in text


def test_payment_fact_search_text():
    fact = {
        "payment_id": "pay-99",
        "instruction_id": "instr-9",
        "status": "APPROVED",
        "currency": "EUR",
        "amount": 500,
        "value_date": "2024-03-01",
        "owning_lob": "LOB-E",
        "instruction_type": "ACH",
        "created_by": {"user_id": "c1", "given_name": "C", "family_name": "Cr"},
        "approved_by": {"user_id": "a1", "given_name": "A", "family_name": "Ap"},
        "rejected_by": {"user_id": "r1", "given_name": "R", "family_name": "Re"},
    }
    text = _build_payment_fact_search_text(fact)
    assert "pay-99" in text
    assert "EUR" in text
    assert "500" in text
    assert "payment" in text


def test_payment_display_helpers():
    assert _display({"given_name": "A", "family_name": "B", "user_id": "u1"}) == "B, A (u1)"
    assert _display({"user_id": "only"}) == "only"
    assert _roles_json(["admin", "viewer"]) == '["admin", "viewer"]'
    assert _roles_json(None) is None
    assert _roles_json([]) is None
