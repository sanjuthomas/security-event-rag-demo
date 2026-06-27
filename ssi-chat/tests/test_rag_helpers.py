from __future__ import annotations

from chat_application.rag import (
    RagService,
    _append_policy_basis,
    _display_from_snap_user,
    _format_usd_amount,
    _humanize_authorization_text,
    _humanize_policy_basis,
    _humanize_policy_basis_point,
    _instruction_lifecycle_party_lines,
    _parse_authorization_basis,
)
from chat_application.reranker import RankedHit


class TestParseAuthorizationBasis:
    def test_passthrough_list(self) -> None:
        assert _parse_authorization_basis(["role:approver", "lob:FX"]) == [
            "role:approver",
            "lob:FX",
        ]

    def test_parses_json_array_string(self) -> None:
        assert _parse_authorization_basis('["a", "b"]') == ["a", "b"]

    def test_invalid_json_returns_empty(self) -> None:
        assert _parse_authorization_basis("not-json") == []

    def test_empty_values_filtered(self) -> None:
        assert _parse_authorization_basis(["ok", "", None]) == ["ok"]


class TestUsdAmountFormatting:
    def test_million(self) -> None:
        assert _format_usd_amount(1_000_000) == "$1 million"
        assert _format_usd_amount(5_000_000) == "$5 million"

    def test_billion(self) -> None:
        assert _format_usd_amount(10_000_000_000) == "$10 billion"

    def test_scientific_notation_input(self) -> None:
        assert _format_usd_amount(float("1e+06")) == "$1 million"
        assert _format_usd_amount(float("1e+07")) == "$10 million"

    def test_humanize_policy_basis_point(self) -> None:
        raw = "amount 1e+06 within subject and absolute limits"
        assert _humanize_policy_basis_point(raw) == (
            "amount $1 million within subject and absolute limits"
        )

    def test_humanize_policy_basis_list(self) -> None:
        basis = [
            "amount 10000000.0 within subject and absolute limits",
            "role FUNDING_APPROVER",
        ]
        readable = _humanize_policy_basis(basis)
        assert readable[0] == "amount $10 million within subject and absolute limits"
        assert readable[1] == "role FUNDING_APPROVER"

    def test_humanize_authorization_text(self) -> None:
        summary = (
            "User was allowed because amount 1e+06 within subject and absolute limits; "
            "role FUNDING_APPROVER"
        )
        assert "amount $1 million within subject and absolute limits" in _humanize_authorization_text(
            summary
        )

    def test_append_policy_basis_formats_amounts(self) -> None:
        why = "Approved under policy."
        basis = ["amount 1e+06 within subject and absolute limits"]
        result = _append_policy_basis(why, basis)
        assert "amount $1 million within subject and absolute limits" in result


class TestDisplayFromSnapUser:
    def test_formats_family_given_and_id(self) -> None:
        snap = {
            "approved_by": {
                "family_name": "Torres",
                "given_name": "Michael",
                "user_id": "ficc-201",
            }
        }
        assert _display_from_snap_user(snap, "approved_by") == "Torres, Michael (ficc-201)"

    def test_falls_back_to_user_id(self) -> None:
        snap = {"approved_by": {"user_id": "fx-201"}}
        assert _display_from_snap_user(snap, "approved_by") == "fx-201"


class TestInstructionLifecyclePartyLines:
    def test_rejected_instruction(self) -> None:
        payload = {
            "rejector_display": "Chen, Sarah (mo-100)",
            "rejected_at": "2026-01-02",
            "rejection_reason": "Invalid creditor",
        }
        snap = {"status": "REJECTED"}
        lines = _instruction_lifecycle_party_lines(payload, snap)
        assert "rejected_by=Chen, Sarah (mo-100)" in lines
        assert "rejection_reason=Invalid creditor" in lines

    def test_standing_instruction(self) -> None:
        payload = {"approver_display": "Torres, Michael (ficc-201)", "approved_at": "2026-01-01"}
        snap = {"status": "STANDING"}
        lines = _instruction_lifecycle_party_lines(payload, snap)
        assert "approver=Torres, Michael (ficc-201)" in lines
        assert "approved_at=2026-01-01" in lines


class TestRagServiceBuildContext:
    def test_events_mode_header(self) -> None:
        context = RagService._build_context([], [], None, mode="events")
        assert "SECURITY EVENTS" in context

    def test_instructions_mode_with_graph_unavailable(self) -> None:
        context = RagService._build_context(
            [],
            [],
            None,
            graph_unavailable=True,
            mode="instructions",
        )
        assert "instruction graph search was unavailable" in context

    def test_includes_aggregate_count(self) -> None:
        context = RagService._build_context([], [{"total": 42}], "MATCH ... LIMIT 1", mode="events")
        assert "Neo4j aggregate count: 42" in context

    def test_includes_ranking_rows(self) -> None:
        rows = [{"actor_display": "User A", "alert_count": 10, "user_id": "u-1"}]
        context = RagService._build_context([], rows, "MATCH ... LIMIT 20", mode="events")
        assert "user ranking by policy alerts" in context
        assert "User A" in context

    def test_zero_graph_rows_with_cypher(self) -> None:
        context = RagService._build_context([], [], "MATCH (n) RETURN n LIMIT 1", mode="events")
        assert "0 rows" in context

    def test_instruction_state_hit_formatting(self) -> None:
        hit = RankedHit(
            key="instruction:inst-1",
            event_id=None,
            instruction_id="inst-1",
            score=0.5,
            sources={"vector"},
            merged={
                "source": "instruction_state",
                "creator_display": "Creator One",
                "authorization_summary": "Allowed",
                "authorization_basis": ["role match"],
                "instruction_snapshot": {
                    "status": "STANDING",
                    "instruction_type": "STANDING",
                    "owning_lob": "FX",
                    "currency": "USD",
                    "wire_scope": "DOMESTIC",
                    "creditor_name": "Bank",
                    "creditor_account_id": "123",
                    "effective_date": "2026-01-01",
                    "end_date": "2027-01-01",
                    "is_expired": False,
                },
            },
        )
        context = RagService._build_context([hit], [], None, mode="instructions")
        assert "INSTRUCTION instruction_id=inst-1" in context
        assert "creator=Creator One" in context

    def test_payment_fact_hit_formatting(self) -> None:
        hit = RankedHit(
            key="payment:p-1",
            event_id=None,
            instruction_id="inst-1",
            score=0.3,
            sources={"bm25"},
            merged={
                "source": "payment_fact",
                "payment_id": "pay-1",
                "instruction_id": "inst-1",
                "status": "APPROVED",
                "amount": 1000,
                "currency": "USD",
                "owning_lob": "FICC",
                "value_date": "2026-02-01",
                "creator_display": "A",
                "approver_display": "B",
            },
        )
        context = RagService._build_context([hit], [], None, mode="payments")
        assert "PAYMENT payment_id=pay-1" in context

    def test_empty_sections_returns_no_data_message(self) -> None:
        # With default mode, a mode header is always emitted even without hits.
        context = RagService._build_context([], [], None, mode="all")
        assert "ALL ENTITIES" in context


class TestRagServiceMergeWithExact:
    def test_pins_exact_hits_first(self, monkeypatch) -> None:
        monkeypatch.setattr("chat_application.rag.settings.max_context_hits", 5)
        exact = [{"source": "exact", "event_id": "evt-exact", "summary": "exact hit"}]
        vector = [{"source": "vector", "event_id": "evt-other", "summary": "vector hit"}]
        merged = RagService._merge_with_exact(exact, vector, [], [])
        assert merged[0].event_id == "evt-exact"

    def test_without_exact_returns_rrf_only(self, monkeypatch) -> None:
        monkeypatch.setattr("chat_application.rag.settings.max_context_hits", 10)
        vector = [{"source": "vector", "event_id": "evt-1", "summary": "one"}]
        merged = RagService._merge_with_exact([], vector, [], [])
        assert len(merged) == 1
        assert merged[0].event_id == "evt-1"


class TestRagServiceToSource:
    def test_converts_ranked_hit(self) -> None:
        hit = RankedHit(
            key="event:evt-1",
            event_id="evt-1",
            instruction_id="inst-1",
            score=0.123456,
            sources={"vector", "neo4j"},
            summary="test summary",
            merged={"action": "APPROVE"},
            security_event={"event_id": "evt-1"},
        )
        source = RagService._to_source(hit)
        assert source.event_id == "evt-1"
        assert source.instruction_id == "inst-1"
        assert source.score == 0.1235
        assert source.sources == ["neo4j", "vector"]
        assert source.summary == "test summary"
