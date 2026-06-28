from __future__ import annotations

from chat_application.formatting import (
    format_usd_compact,
    humanize_authorization_text,
    humanize_policy_basis,
    humanize_policy_basis_point,
)
from chat_application.rag import (
    RagService,
    _append_policy_basis,
    _display_from_snap_user,
    _format_alert_ranking_answer,
    _format_instruction_count_aggregate_answer,
    _format_max_payments_per_instruction_answer,
    _format_payment_count_aggregate_answer,
    _format_payment_total_amount_answer,
    _format_payments_for_instruction_answer,
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
        assert format_usd_compact(1_000_000) == "$1 million"
        assert format_usd_compact(5_000_000) == "$5 million"

    def test_billion(self) -> None:
        assert format_usd_compact(10_000_000_000) == "$10 billion"

    def test_scientific_notation_input(self) -> None:
        assert format_usd_compact(float("1e+06")) == "$1 million"
        assert format_usd_compact(float("1e+07")) == "$10 million"

    def test_humanize_policy_basis_point(self) -> None:
        raw = "amount 1e+06 within subject and absolute limits"
        assert humanize_policy_basis_point(raw) == (
            "amount $1 million within subject and absolute limits"
        )

    def test_humanize_policy_basis_list(self) -> None:
        basis = [
            "amount 10000000.0 within subject and absolute limits",
            "role FUNDING_APPROVER",
        ]
        readable = humanize_policy_basis(basis)
        assert readable[0] == "amount $10 million within subject and absolute limits"
        assert readable[1] == "role FUNDING_APPROVER"

    def test_humanize_authorization_text(self) -> None:
        summary = (
            "User was allowed because amount 1e+06 within subject and absolute limits; "
            "role FUNDING_APPROVER"
        )
        assert "amount $1 million within subject and absolute limits" in humanize_authorization_text(
            summary
        )

    def test_append_policy_basis_formats_amounts(self) -> None:
        why = "Approved under policy."
        basis = ["amount 1e+06 within subject and absolute limits"]
        result = _append_policy_basis(why, basis)
        assert "Policy basis (1 checks):" in result
        assert "Policy check" in result
        assert "| --- |" in result or "| ---  |" in result
        assert "amount $1 million within subject and absolute limits" in result


class TestMaxPaymentsPerInstructionAnswer:
    def test_formats_instruction_summary_and_payment_rows(self) -> None:
        iid = "3bcb9b9a-9415-44ce-b707-4cc4c8281bb9"
        rows = [
            {
                "instruction_id": iid,
                "payment_count": 2,
                "payment_id": "pay-1",
                "created_at": "2026-06-27T10:00:00Z",
                "creator_display": "Creator One (c-1)",
                "approver_display": "Approver One (a-1)",
            },
            {
                "instruction_id": iid,
                "payment_count": 2,
                "payment_id": "pay-2",
                "created_at": "2026-06-27T11:00:00Z",
                "creator_display": "Creator Two (c-2)",
                "approver_display": "Approver Two (a-2)",
            },
        ]
        answer = _format_max_payments_per_instruction_answer(rows)
        assert answer is not None
        assert f"Instruction: {iid}" in answer
        assert "Total payments: 2" in answer
        assert "Payment ID" in answer
        assert "Created At" in answer
        assert "| pay-1" in answer
        assert "| pay-2" in answer

    def test_dedupes_duplicate_payment_rows_and_uses_row_count(self) -> None:
        iid = "3bcb9b9a-9415-44ce-b707-4cc4c8281bb9"
        rows = [
            {
                "instruction_id": iid,
                "payment_count": 20,
                "payment_id": "pay-1",
                "created_at": "2026-06-27T10:00:00Z",
                "creator_display": "Creator One (c-1)",
                "approver_display": "Approver One (a-1)",
            },
            {
                "instruction_id": iid,
                "payment_count": 20,
                "payment_id": "pay-1",
                "created_at": "2026-06-27T10:00:00Z",
                "creator_display": "Creator One (c-1)",
                "approver_display": "Approver One (a-1)",
            },
            {
                "instruction_id": iid,
                "payment_count": 20,
                "payment_id": "pay-2",
                "created_at": "2026-06-27T11:00:00Z",
                "creator_display": "Creator Two (c-2)",
                "approver_display": "Approver Two (a-2)",
            },
        ]
        answer = _format_max_payments_per_instruction_answer(rows)
        assert answer is not None
        assert "Total payments: 2" in answer
        assert answer.count("pay-1") == 1


class TestPaymentsForInstructionAnswer:
    def test_includes_total_and_payment_rows(self) -> None:
        iid = "3bcb9b9a-9415-44ce-b707-4cc4c8281bb9"
        rows = [
            {
                "payment_id": "92831268-b1d0-44c8-a24a-b84a912cb051",
                "instruction_id": iid,
                "status": "APPROVED",
                "amount": 10_000_000,
                "currency": "USD",
                "value_date": "2026-06-28",
                "owning_lob": "FICC",
                "creator_display": "Nakamura, Kenji (pay-102)",
                "approver_display": "Laurent, Sophie (pay-201)",
            },
            {
                "payment_id": "9b3251c9-d28e-4ad5-9bf4-dbc3c4fc13d8",
                "instruction_id": iid,
                "status": "APPROVED",
                "amount": 1_000_000,
                "currency": "USD",
                "value_date": "2026-06-28",
                "owning_lob": "FICC",
                "creator_display": "Rodriguez, Emily (pay-101)",
                "approver_display": "Laurent, Sophie (pay-201)",
            },
        ]
        answer = _format_payments_for_instruction_answer(iid, rows)
        assert answer.startswith(f"There are 2 payments in total for instruction {iid}.")
        assert "Payment ID" in answer
        assert "Status" in answer
        assert "92831268-b1d0-44c8-a24a-b84a912cb051" in answer
        assert "10,000,000.00 USD" in answer


class TestAlertRankingAnswer:
    def test_formats_ranking_table(self) -> None:
        rows = [
            {
                "user_id": "fx-201",
                "actor_display": "Hassan, Amira (fx-201)",
                "alert_count": 12,
                "payment_alerts": 4,
                "instruction_alerts": 8,
            },
            {
                "user_id": "pay-101",
                "actor_display": "Rodriguez, Emily (pay-101)",
                "alert_count": 5,
                "payment_alerts": 5,
                "instruction_alerts": 0,
            },
        ]
        answer = _format_alert_ranking_answer(
            "Which user triggered the most policy denial alerts this week?",
            rows,
        )
        assert "policy denial alerts (this week)" in answer
        assert "User" in answer
        assert "Total Alerts" in answer
        assert "Hassan, Amira (fx-201)" in answer
        assert "| 12" in answer or "| 12 " in answer


class TestPaymentAggregateAnswers:
    def test_formats_total_amount_from_graph_rows(self) -> None:
        rows = [
            {
                "currency": "USD",
                "payment_count": 18,
                "total_amount": 125_000_000,
            }
        ]
        answer = _format_payment_total_amount_answer(
            "What is the total approved payment amount for FICC today?",
            rows,
        )
        assert "LOB FICC" in answer
        assert "today" in answer
        assert "125,000,000.00 USD" in answer
        assert "18 payments" in answer

    def test_formats_count_from_graph_rows(self) -> None:
        answer = _format_payment_count_aggregate_answer(
            "How many payments were approved today for FICC?",
            [{"total": 18}],
        )
        assert "18 matching payment(s)" in answer
        assert "LOB FICC" in answer

    def test_formats_instruction_count(self) -> None:
        answer = _format_instruction_count_aggregate_answer(
            "How many instructions are there in the store?",
            [{"total": 10}],
        )
        assert answer == "There are 10 instructions in the store."


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
