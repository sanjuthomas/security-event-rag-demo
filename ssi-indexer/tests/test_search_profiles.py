"""Tests for YAML-driven search_text profiles."""

from __future__ import annotations

from etl.authorization_context import (
    authorization_merged_fields,
    authorization_merged_from_fact,
    authorization_search_parts,
)
from etl.enrichment import build_merged_context, build_search_text
from etl.instruction_pipeline import build_instruction_state_search_text
from etl.payment_pipeline import build_payment_event_search_text, build_payment_fact_search_text
from etl.search_text.builder import (
    build_search_text_from_profile,
    expand_includes,
    list_profile_fields,
    list_search_profiles,
    load_entity_profile,
    profiles_dir,
)
from etl.search_text.context import instruction_state_context, payment_security_event_context
from tests.test_enrichment import _sample_event, _sample_instruction


def _legacy_instruction_security_event_search_text(ctx: dict) -> str:
    """Pre-YAML builder — parity reference for instruction_security_event."""
    parts = [
        ctx.get("message", ""),
        ctx.get("severity", ""),
        ctx.get("action", ""),
        ctx.get("outcome", ""),
        ctx.get("reason") or "",
        *authorization_search_parts(ctx),
        ctx.get("actor_user_id", ""),
        ctx.get("actor_given_name") or "",
        ctx.get("actor_family_name") or "",
        ctx.get("actor_title", ""),
        " ".join(ctx.get("actor_roles") or []),
        ctx.get("actor_lob") or "",
        ctx.get("actor_supervisor_id") or "",
        ctx.get("owning_lob", ""),
        ctx.get("status", ""),
        ctx.get("instruction_type") or "",
        ctx.get("wire_scope", ""),
        ctx.get("currency", ""),
        ctx.get("creditor_name") or "",
        ctx.get("creditor_account_id") or "",
        ctx.get("debtor_name") or "",
        ctx.get("debtor_account_id") or "",
        ctx.get("creditor_agent_bic") or "",
        ctx.get("creator_user_id", ""),
        ctx.get("creator_given_name") or "",
        ctx.get("creator_family_name") or "",
        ctx.get("creator_title", ""),
        ctx.get("creator_lob") or "",
        ctx.get("approver_user_id", ""),
        ctx.get("approver_given_name") or "",
        ctx.get("approver_family_name") or "",
        ctx.get("approver_title", ""),
        ctx.get("approver_lob") or "",
        ctx.get("rejector_user_id", ""),
        ctx.get("rejector_given_name") or "",
        ctx.get("rejector_family_name") or "",
        ctx.get("rejector_title", ""),
        ctx.get("rejector_lob") or "",
        ctx.get("effective_date", ""),
        ctx.get("end_date", ""),
    ]
    return " ".join(str(part) for part in parts if part).strip()


def _legacy_instruction_state_search_text(fact: dict) -> str:
    snap = fact.get("instruction_snapshot") or {}
    creditor = snap.get("creditor") or {}
    debtor = snap.get("debtor") or {}
    creditor_account = snap.get("creditor_account") or {}
    debtor_account = snap.get("debtor_account") or {}
    creditor_agent_fi = (snap.get("creditor_agent") or {}).get("financial_institution") or {}
    created_by = snap.get("created_by") or {}
    approved_by = snap.get("approved_by") or {}
    rejected_by = snap.get("rejected_by") or {}

    parts = [
        snap.get("instruction_id", ""),
        snap.get("status", ""),
        snap.get("instruction_type", ""),
        snap.get("owning_lob", ""),
        snap.get("wire_scope", ""),
        snap.get("currency", ""),
        creditor.get("name") or "",
        creditor_account.get("identification") or "",
        creditor_account.get("identification_scheme") or "",
        creditor_agent_fi.get("identification") or "",
        debtor.get("name") or "",
        debtor_account.get("identification") or "",
        snap.get("effective_date") or "",
        snap.get("end_date") or "",
        created_by.get("user_id") or "",
        created_by.get("given_name") or "",
        created_by.get("family_name") or "",
        created_by.get("lob") or "",
        approved_by.get("user_id") or "",
        approved_by.get("given_name") or "",
        approved_by.get("family_name") or "",
        approved_by.get("lob") or "",
        rejected_by.get("user_id") or "",
        rejected_by.get("given_name") or "",
        rejected_by.get("family_name") or "",
        snap.get("approved_at") or "",
        fact.get("actor_user_id") or "",
        fact.get("actor_given_name") or "",
        fact.get("actor_family_name") or "",
        fact.get("actor_lob") or "",
        fact.get("action") or "",
        *authorization_search_parts(authorization_merged_from_fact(fact)),
    ]
    return " ".join(str(part) for part in parts if part).strip()


def _legacy_payment_event_search_text(event: dict) -> str:
    actor = event.get("actor") or {}
    resource = event.get("resource") or {}
    event_ctx = event.get("event") or {}
    snap = event.get("payment_snapshot") or {}
    created_by = snap.get("created_by") or {}
    approved_by = snap.get("approved_by") or {}
    auth_ctx = authorization_merged_fields(event)

    def display(user: dict) -> str:
        fn = user.get("family_name") or ""
        gn = user.get("given_name") or ""
        uid = user.get("user_id") or ""
        if fn and gn:
            return f"{fn}, {gn} ({uid})"
        return uid

    parts = [
        event.get("message", ""),
        event.get("timestamp", ""),
        event.get("severity", ""),
        event_ctx.get("action", ""),
        event_ctx.get("outcome", ""),
        event_ctx.get("reason") or "",
        *authorization_search_parts(auth_ctx),
        actor.get("user_id", ""),
        actor.get("given_name") or "",
        actor.get("family_name") or "",
        actor.get("title", ""),
        " ".join(actor.get("roles") or []),
        " ".join(actor.get("groups") or []),
        " ".join(actor.get("covering_lobs") or []),
        actor.get("lob") or "",
        resource.get("id", ""),
        resource.get("instruction_id", ""),
        resource.get("owning_lob", ""),
        resource.get("currency", ""),
        str(resource.get("amount", "")),
        snap.get("value_date") or "",
        snap.get("instruction_type") or "",
        created_by.get("user_id") or "",
        display(created_by),
        approved_by.get("user_id") or "",
        display(approved_by),
        "payment",
    ]
    return " ".join(str(part) for part in parts if part).strip()


def _legacy_payment_fact_search_text(fact: dict) -> str:
    created_by = fact.get("created_by") or {}
    approved_by = fact.get("approved_by") or {}
    rejected_by = fact.get("rejected_by") or {}

    def display(user: dict) -> str:
        fn = user.get("family_name") or ""
        gn = user.get("given_name") or ""
        uid = user.get("user_id") or ""
        if fn and gn:
            return f"{fn}, {gn} ({uid})"
        return uid

    parts = [
        fact.get("payment_id", ""),
        fact.get("instruction_id", ""),
        fact.get("status", ""),
        fact.get("currency", ""),
        str(fact.get("amount", "")),
        fact.get("value_date") or "",
        fact.get("owning_lob", ""),
        fact.get("instruction_type", ""),
        created_by.get("user_id") or "",
        display(created_by),
        approved_by.get("user_id") or "",
        display(approved_by),
        rejected_by.get("user_id") or "",
        display(rejected_by),
        "payment",
    ]
    return " ".join(str(part) for part in parts if part).strip()


def _sample_instruction_fact() -> dict:
    return {
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


def _sample_payment_event() -> dict:
    return {
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


def _sample_payment_fact() -> dict:
    return {
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


def test_profiles_dir_exists():
    assert (profiles_dir() / "instruction_security_event.yaml").is_file()


def test_all_entity_profiles_are_wired():
    profiles = list_search_profiles()
    assert len(profiles) == 4
    assert all(profile["wired"] for profile in profiles)


def test_instruction_security_event_profile_is_wired():
    meta = list_profile_fields("instruction_security_event")
    assert meta["wired"] is True
    assert meta["context_root"] == "merged"
    assert any(item.get("path") == "message" for item in meta["includes"])
    assert any(item.get("path") == "authorization_summary" for item in meta["includes"])
    assert "instruction_id" in meta["excludes"]


def test_instruction_state_profile_metadata():
    meta = list_profile_fields("instruction_state")
    assert meta["wired"] is True
    assert any(item.get("path") == "instruction_snapshot.status" for item in meta["includes"])


def test_expand_includes_resolves_shared_profile():
    profile = load_entity_profile("instruction_security_event")
    fields = expand_includes(profile)
    paths = [f.get("path") for f in fields if f.get("path")]
    assert "authorization_summary" in paths
    assert "authorization_basis" in paths
    assert paths.index("message") < paths.index("authorization_summary")


def test_profile_matches_legacy_instruction_security_event():
    event = _sample_event()
    instruction = _sample_instruction()
    merged = build_merged_context(event, instruction)
    legacy = _legacy_instruction_security_event_search_text(merged)
    profile_text = build_search_text_from_profile("instruction_security_event", merged)
    assert profile_text == legacy


def test_enrichment_build_search_text_uses_profile():
    event = _sample_event()
    instruction = _sample_instruction()
    merged = build_merged_context(event, instruction)
    assert build_search_text(event, instruction) == build_search_text_from_profile(
        "instruction_security_event", merged
    )


def test_profile_matches_legacy_instruction_security_event_with_lists():
    event = _sample_event()
    event["details"]["authorization"]["allow_basis"] = ["rule-a", "rule-b"]
    event["actor"]["groups"] = ["MIDDLE_OFFICE"]
    event["actor"]["covering_lobs"] = ["FICC", "RATES"]
    instruction = _sample_instruction()
    merged = build_merged_context(event, instruction)
    legacy = _legacy_instruction_security_event_search_text(merged)
    profile_text = build_search_text_from_profile("instruction_security_event", merged)
    assert profile_text == legacy


def test_profile_matches_legacy_instruction_state():
    fact = _sample_instruction_fact()
    legacy = _legacy_instruction_state_search_text(fact)
    profile_text = build_search_text_from_profile("instruction_state", instruction_state_context(fact))
    assert profile_text == legacy
    assert build_instruction_state_search_text(fact) == legacy


def test_profile_matches_legacy_payment_security_event():
    event = _sample_payment_event()
    legacy = _legacy_payment_event_search_text(event)
    profile_text = build_search_text_from_profile(
        "payment_security_event",
        payment_security_event_context(event),
    )
    assert profile_text == legacy
    assert build_payment_event_search_text(event) == legacy


def test_profile_matches_legacy_payment_fact():
    fact = _sample_payment_fact()
    legacy = _legacy_payment_fact_search_text(fact)
    profile_text = build_search_text_from_profile("payment_fact", fact)
    assert profile_text == legacy
    assert build_payment_fact_search_text(fact) == legacy
