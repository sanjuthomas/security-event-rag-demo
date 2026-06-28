"""Tests for etl.qdrant_store with mocked QdrantClient."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from etl.enrichment import enrich_document
from etl.qdrant_store import QdrantHybridStore
from qdrant_client.http import models


@pytest.fixture
def store() -> QdrantHybridStore:
    s = QdrantHybridStore()
    s._client = MagicMock()
    return s


def test_point_to_result(store: QdrantHybridStore):
    point = models.ScoredPoint(
        id="p1",
        version=1,
        score=0.95,
        payload={
            "event_id": "e1",
            "instruction_id": "i1",
            "search_text": "hello",
            "security_event": {"event_id": "e1"},
        },
    )
    result = store._point_to_result(point)
    assert result["score"] == 0.95
    assert result["event_id"] == "e1"
    assert result["instruction_id"] == "i1"
    assert result["search_text"] == "hello"
    assert result["payload"]["event_id"] == "e1"


def test_point_to_result_empty_payload(store: QdrantHybridStore):
    point = models.ScoredPoint(id="p2", version=1, score=0.5, payload=None)
    result = store._point_to_result(point)
    assert result["event_id"] is None
    assert result["payload"] == {}


def test_ensure_collection_already_ready(store: QdrantHybridStore):
    store._collection_ready = True
    store.ensure_collection(384)
    store._client.collection_exists.assert_not_called()


def test_ensure_collection_exists(store: QdrantHybridStore):
    store._client.collection_exists.return_value = True
    store.ensure_collection(384)
    assert store._collection_ready is True
    store._client.create_collection.assert_not_called()


def test_ensure_collection_creates(store: QdrantHybridStore):
    store._client.collection_exists.return_value = False
    store.ensure_collection(768)
    store._client.create_collection.assert_called_once()
    assert store._collection_ready is True


def test_ensure_collection_not_connected():
    store = QdrantHybridStore()
    with pytest.raises(RuntimeError, match="not connected"):
        store.ensure_collection(384)


def test_upsert_security_event(store: QdrantHybridStore):
    event = {
        "event_id": "evt-upsert",
        "resource": {"id": "i1", "version_number": 1},
        "message": "test",
    }
    doc = enrich_document(event, None)
    vector = [0.1, 0.2, 0.3]
    store.upsert(doc, dense_vector=vector)
    store._client.upsert.assert_called_once()
    call_kwargs = store._client.upsert.call_args.kwargs
    assert call_kwargs["collection_name"]
    assert len(call_kwargs["points"]) == 1
    assert call_kwargs["points"][0].payload["source"] == "instruction_security_event"


def test_upsert_payment_point(store: QdrantHybridStore):
    store.upsert_payment_point(
        point_id="pid-1",
        search_text="payment text",
        payload={"payment_id": "p1"},
        dense_vector=[1.0, 2.0],
    )
    store._client.upsert.assert_called_once()


def test_upsert_instruction_state(store: QdrantHybridStore):
    store.upsert_instruction_state(
        instruction_id="instr-42",
        search_text="instruction text",
        payload={"status": "ACTIVE"},
        dense_vector=[0.5],
    )
    store._client.upsert.assert_called_once()
    payload = store._client.upsert.call_args.kwargs["points"][0].payload
    assert payload["source"] == "instruction_state"
    assert payload["instruction_id"] == "instr-42"


def test_get_instruction_state_payload(store: QdrantHybridStore):
    record = MagicMock()
    record.payload = {"status": "APPROVED"}
    store._client.retrieve.return_value = [record]
    result = store.get_instruction_state_payload("instr-1")
    assert result == {"status": "APPROVED"}


def test_get_instruction_state_payload_missing(store: QdrantHybridStore):
    store._client.retrieve.return_value = []
    assert store.get_instruction_state_payload("missing") is None


def test_has_collection_false_when_not_connected():
    store = QdrantHybridStore()
    assert store.has_collection() is False


def test_search_dense_no_collection(store: QdrantHybridStore):
    store._client.collection_exists.return_value = False
    assert store.search_dense([0.1], limit=5) == []


def test_search_dense_returns_results(store: QdrantHybridStore):
    store._client.collection_exists.return_value = True
    point = models.ScoredPoint(
        id="p1",
        version=1,
        score=0.9,
        payload={"event_id": "e1", "search_text": "q"},
    )
    store._client.query_points.return_value = MagicMock(points=[point])
    results = store.search_dense([0.1, 0.2], limit=3)
    assert len(results) == 1
    assert results[0]["event_id"] == "e1"


def test_search_bm25_returns_results(store: QdrantHybridStore):
    store._client.collection_exists.return_value = True
    point = models.ScoredPoint(id="p1", version=1, score=0.7, payload={"event_id": "e2"})
    store._client.query_points.return_value = MagicMock(points=[point])
    results = store.search_bm25("query text", limit=5)
    assert results[0]["event_id"] == "e2"


def test_search_hybrid_returns_results(store: QdrantHybridStore):
    store._client.collection_exists.return_value = True
    point = models.ScoredPoint(id="p1", version=1, score=0.85, payload={"event_id": "e3"})
    store._client.query_points.return_value = MagicMock(points=[point])
    results = store.search_hybrid("text", [0.1], limit=2)
    assert results[0]["event_id"] == "e3"


def test_collection_info(store: QdrantHybridStore):
    store._client.collection_exists.return_value = True
    info = MagicMock()
    info.points_count = 100
    info.status = "green"
    store._client.get_collection.return_value = info
    result = store.collection_info()
    assert result["exists"] is True
    assert result["points_count"] == 100


def test_collection_info_not_exists(store: QdrantHybridStore):
    store._client.collection_exists.return_value = False
    assert store.collection_info() == {"exists": False, "points_count": 0}


def test_search_text_chunk_stats_empty_collection(store: QdrantHybridStore):
    store._client.collection_exists.return_value = False
    stats = store.search_text_chunk_stats(top_n=10)
    assert stats["points_count"] == 0
    assert stats["top_chunks"] == []
    assert stats["indexing_model"] == "one_point_per_record"


def test_search_text_chunk_stats_returns_top_chunks(store: QdrantHybridStore):
    store._client.collection_exists.return_value = True

    short = MagicMock()
    short.id = "p-short"
    short.payload = {
        "source": "instruction_state",
        "instruction_id": "i-small",
        "search_text": "short text",
    }

    long_text = "word " * 200
    long = MagicMock()
    long.id = "p-long"
    long.payload = {
        "source": "instruction_security_event",
        "event_id": "evt-big",
        "instruction_id": "i-big",
        "search_text": long_text,
    }

    store._client.scroll.side_effect = [
        ([short, long], None),
    ]

    stats = store.search_text_chunk_stats(top_n=1)
    assert stats["points_count"] == 2
    assert len(stats["top_chunks"]) == 1
    assert stats["top_chunks"][0]["event_id"] == "evt-big"
    assert stats["top_chunks"][0]["char_count"] == len(long_text)
    assert stats["summary"]["char_count"]["max"] == len(long_text)
    assert stats["by_source"]["instruction_security_event"]["count"] == 1


def test_patch_instruction_state_authorization(store: QdrantHybridStore):
    record = MagicMock()
    record.payload = {"search_text": "base", "approved_at": None}
    record.vector = {"dense": [0.1]}
    store._client.retrieve.return_value = [record]
    store.patch_instruction_state_authorization(
        "instr-1",
        approved_at="2024-01-01",
        authorization_summary="approved",
        authorization_basis=["rule-a"],
    )
    store._client.upsert.assert_called_once()
    payload = store._client.upsert.call_args.kwargs["points"][0].payload
    assert "approved" in payload["search_text"]


def test_patch_instruction_state_skips_without_summary(store: QdrantHybridStore):
    store.patch_instruction_state_authorization(
        "instr-1",
        approved_at="2024-01-01",
        authorization_summary=None,
        authorization_basis=[],
    )
    store._client.retrieve.assert_not_called()


def test_get_instruction_state_payload_no_client():
    store = QdrantHybridStore()
    assert store.get_instruction_state_payload("x") is None


def test_connect_and_close():
    store = QdrantHybridStore()
    with patch("etl.qdrant_store.QdrantClient") as mock_cls:
        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        store.connect()
        assert store._client is mock_client
        store.close()
        mock_client.close.assert_called_once()
        assert store._client is None
