from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from inst.security_event_ui_store import SecurityEventUiStore, _merge_recent_documents


@pytest.mark.asyncio
async def test_security_event_ui_store_list_recent() -> None:
    store = SecurityEventUiStore()
    mock_collection = MagicMock()

    async def _notable_iter():
        yield {
            "event_id": "e-denial",
            "timestamp": "2025-01-02T00:00:00Z",
            "severity": "ALERT",
            "event": {"outcome": "failure"},
        }

    async def _info_iter():
        yield {
            "event_id": "e1",
            "timestamp": "2025-01-01T00:00:00Z",
            "severity": "INFO",
        }

    notable_cursor = MagicMock()
    notable_cursor.sort.return_value.limit.return_value = _notable_iter()
    info_cursor = MagicMock()
    info_cursor.sort.return_value.limit.return_value = _info_iter()
    mock_collection.find.side_effect = [notable_cursor, info_cursor]

    with patch("inst.security_event_ui_store.get_security_events_database") as mock_get_db:
        mock_get_db.return_value.__getitem__ = MagicMock(return_value=mock_collection)
        events = await store.list_recent(limit=5)
    assert events[0]["event_id"] == "e-denial"
    assert events[1]["event_id"] == "e1"
    assert "e-denial" in store.seen_event_ids


def test_merge_recent_documents_prefers_notable_events() -> None:
    merged = _merge_recent_documents(
        [
            {"event_id": "denial", "timestamp": "2025-01-02T00:00:00Z"},
        ],
        [
            {"event_id": "info-new", "timestamp": "2025-01-03T00:00:00Z"},
            {"event_id": "info-old", "timestamp": "2025-01-01T00:00:00Z"},
            {"event_id": "denial", "timestamp": "2025-01-02T00:00:00Z"},
        ],
        limit=2,
    )
    assert [doc["event_id"] for doc in merged] == ["info-new", "denial"]


def test_security_event_ui_store_remember_helpers() -> None:
    store = SecurityEventUiStore()
    assert store.remember_event_id("e1") is True
    assert store.remember_event_id("e1") is False
    store.remember_poll_timestamp("2025-06-01T00:00:00Z")
    assert store.last_poll_at is not None


def test_main_health_endpoint() -> None:
    with patch("inst.main.connect", AsyncMock()), \
         patch("inst.main.close", AsyncMock()), \
         patch("inst.main.kafka_publisher.start", AsyncMock()), \
         patch("inst.main.kafka_publisher.close", AsyncMock()), \
         patch("inst.main.security_event_ui_store.connect", AsyncMock()):

        from inst.main import app

        with TestClient(app) as client:
            response = client.get("/health")
            assert response.status_code == 200
            assert response.json()["status"] == "UP"
