from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from chat_application.rag import RagService


@pytest.fixture
def rag_service(mock_ollama, mock_qdrant, mock_neo4j, monkeypatch):
    monkeypatch.setattr("chat_application.rag.load_graph_schema", lambda: "schema")
    return RagService(ollama=mock_ollama, qdrant=mock_qdrant, neo4j=mock_neo4j)


class TestRagServiceAsk:
    @pytest.mark.asyncio
    async def test_ask_returns_chat_response(self, rag_service, mock_ollama, mock_qdrant) -> None:
        mock_qdrant.search_vector = MagicMock(return_value=[])
        mock_qdrant.search_bm25 = MagicMock(return_value=[])
        mock_ollama.synthesize_answer = AsyncMock(return_value="There were 0 alerts.")

        response = await rag_service.ask("How many alerts?", [], mode="events")
        assert response.answer == "There were 0 alerts."
        assert response.retrieval_ms is not None
        assert response.generation_ms is not None

    @pytest.mark.asyncio
    async def test_ask_with_event_uuid_triggers_exact_lookup(
        self, rag_service, mock_qdrant, mock_neo4j, mock_ollama
    ) -> None:
        event_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        mock_qdrant.fetch_by_event_id = MagicMock(
            return_value={
                "source": "exact",
                "event_id": event_id,
                "summary": "exact",
                "merged": {"source": "instruction_security_event", "action": "VIEW"},
            }
        )
        mock_qdrant.search_vector = MagicMock(return_value=[])
        mock_qdrant.search_bm25 = MagicMock(return_value=[])
        mock_neo4j.lookup_instruction_for_event = AsyncMock(
            return_value=[{"event_id": event_id, "instruction_id": "inst-1"}]
        )
        mock_ollama.synthesize_answer = AsyncMock(return_value="Found event.")

        response = await rag_service.ask(f"What about event {event_id}?", [], mode="events")
        assert "Found event" in response.answer
        mock_qdrant.fetch_by_event_id.assert_called_once_with(event_id)

    @pytest.mark.asyncio
    async def test_ask_instruction_approval_synthesis(
        self, rag_service, mock_ollama, mock_qdrant, mock_neo4j
    ) -> None:
        iid = "2846a7c0-4734-4626-bb58-13a966f935a1"
        mock_qdrant.search_vector = MagicMock(return_value=[])
        mock_qdrant.search_bm25 = MagicMock(return_value=[])
        mock_qdrant.fetch_by_instruction_id = MagicMock(
            return_value={
                "source": "exact_instruction",
                "instruction_id": iid,
                "merged": {
                    "source": "instruction_state",
                    "instruction_id": iid,
                    "approver_display": "Torres, Michael (ficc-201)",
                    "approved_at": "2026-01-01",
                    "authorization_summary": "OPA allowed",
                    "authorization_basis": ["role match"],
                    "instruction_snapshot": {"status": "STANDING"},
                },
            }
        )
        mock_neo4j.run_cypher = AsyncMock(
            return_value=[
                {
                    "instruction_id": iid,
                    "approver_display": "Torres, Michael (ficc-201)",
                    "approved_at": "2026-01-01",
                    "authorization_summary": "OPA allowed",
                    "authorization_basis": '["role match"]',
                }
            ]
        )
        mock_ollama.summarize_authorization_why = AsyncMock(return_value="Readable why.")

        response = await rag_service.ask(f"Who approved instruction {iid}?", [], mode="instructions")
        assert response.answer.startswith("WHO:")
        assert "Readable why." in response.answer
        mock_ollama.synthesize_answer.assert_not_called()

    @pytest.mark.asyncio
    async def test_ask_payment_approval_synthesis(
        self, rag_service, mock_ollama, mock_qdrant, mock_neo4j
    ) -> None:
        pid = "9b3251c9-d28e-4ad5-9bf4-dbc3c4fc13d8"
        mock_qdrant.search_vector = MagicMock(return_value=[])
        mock_qdrant.search_bm25 = MagicMock(return_value=[])
        mock_qdrant.fetch_by_payment_id = MagicMock(
            return_value={
                "source": "exact_payment",
                "payment_id": pid,
                "merged": {
                    "source": "payment_fact",
                    "payment_id": pid,
                    "approver_display": "Laurent, Sophie (pay-201)",
                    "status": "APPROVED",
                },
            }
        )
        mock_qdrant.fetch_payment_approve_events = MagicMock(
            return_value=[
                {
                    "source": "exact_approve_payment_event",
                    "payment_id": pid,
                    "merged": {
                        "source": "payment_security_event",
                        "payment_id": pid,
                        "action": "APPROVE_PAYMENT",
                        "outcome": "success",
                        "actor_display": "Laurent, Sophie (pay-201)",
                        "timestamp": "2026-06-27T21:39:26.072387Z",
                        "authorization_summary": (
                            "Laurent, Sophie (pay-201) was allowed to APPROVE_PAYMENT because "
                            "role FUNDING_APPROVER; group MIDDLE_OFFICE"
                        ),
                        "authorization_basis": [
                            "role FUNDING_APPROVER",
                            "group MIDDLE_OFFICE",
                            "covers LOB FICC",
                            "amount 1e+06 within subject and absolute limits",
                            "not self-approval (creator is not approver)",
                            "approver does not report to payment creator",
                        ],
                    },
                }
            ]
        )
        mock_neo4j.run_cypher = AsyncMock(return_value=[])
        mock_ollama.summarize_authorization_why = AsyncMock(
            return_value="Sophie Laurent was authorized as a funding approver covering FICC."
        )

        response = await rag_service.ask(
            f"Who approved the payment {pid}?",
            [],
            mode="payments",
        )
        assert response.answer.startswith("WHO:")
        assert "Policy basis:" in response.answer
        assert "role FUNDING_APPROVER" in response.answer
        assert "covers LOB FICC" in response.answer
        assert "amount $1 million within subject and absolute limits" in response.answer
        assert "1e+06" not in response.answer
        mock_ollama.synthesize_answer.assert_not_called()

    @pytest.mark.asyncio
    async def test_search_vector_handles_embed_failure(self, rag_service, mock_ollama) -> None:
        mock_ollama.embed = AsyncMock(side_effect=RuntimeError("embed down"))
        hits = await rag_service._search_vector("query", 5)
        assert hits == []

    @pytest.mark.asyncio
    async def test_search_graph_uses_planned_queries(self, rag_service, mock_neo4j) -> None:
        mock_neo4j.run_cypher = AsyncMock(return_value=[{"total": 5}])
        result = await rag_service._search_graph("How many alerts today?", mode="events")
        assert {"total": 5} in result["rows"]
        assert result.get("cypher")
        assert "count(e)" in result["cypher"]

    @pytest.mark.asyncio
    async def test_search_graph_falls_back_on_invalid_llm_cypher(
        self, rag_service, mock_ollama, mock_neo4j
    ) -> None:
        mock_ollama.generate_cypher = AsyncMock(return_value="CREATE (n) RETURN n LIMIT 1")

        async def run_cypher_side_effect(cypher: str):
            from chat_application.cypher import validate_read_only_cypher

            validate_read_only_cypher(cypher)
            return []

        mock_neo4j.run_cypher = AsyncMock(side_effect=run_cypher_side_effect)
        result = await rag_service._search_graph("random question", mode="events")
        assert result.get("graph_unavailable") is True
