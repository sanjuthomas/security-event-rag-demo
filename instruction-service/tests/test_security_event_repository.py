from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from inst.models.enums import LifecycleAction
from inst.models.security_event import SecurityEvent
from inst.security_event_repository import SecurityEventRepository


@pytest.fixture
def mock_collection() -> MagicMock:
    return MagicMock()


@pytest.fixture
def repo(mock_collection: MagicMock) -> SecurityEventRepository:
    sequence_client = AsyncMock()
    sequence_client.next_security_event_id = AsyncMock(
        return_value="20260628-FICC-I-1-SE-1"
    )
    with patch("inst.security_event_repository.get_security_events_database") as mock_get_db:
        mock_get_db.return_value.__getitem__ = MagicMock(return_value=mock_collection)
        yield SecurityEventRepository(
            collection_name="test-events",
            sequence_client=sequence_client,
        )


@pytest.mark.asyncio
async def test_insert_document(repo: SecurityEventRepository, mock_collection: MagicMock) -> None:
    mock_collection.insert_one = AsyncMock()
    doc = {"event_id": "e1"}
    result = await repo.insert_document(doc)
    assert result == doc
    mock_collection.insert_one.assert_awaited_once_with(doc, session=None)


@pytest.mark.asyncio
async def test_publish_delegates_to_kafka(
    repo: SecurityEventRepository,
) -> None:
    with patch("inst.security_event_repository.kafka_publisher") as mock_kafka:
        mock_kafka.publish = AsyncMock()
        await repo.publish({"event_id": "e1"})
        mock_kafka.publish.assert_awaited_once()


@pytest.mark.asyncio
async def test_insert_and_record_methods(
    sample_subject,
    sample_instruction,
) -> None:
    mock_collection = MagicMock()
    mock_collection.insert_one = AsyncMock()
    sequence_client = AsyncMock()
    sequence_client.next_security_event_id = AsyncMock(
        return_value="20260628-FICC-I-1-SE-1"
    )
    with patch("inst.security_event_repository.get_security_events_database") as mock_get_db:
        mock_get_db.return_value.__getitem__ = MagicMock(return_value=mock_collection)
        repo = SecurityEventRepository(
            collection_name="test-events",
            sequence_client=sequence_client,
        )
        with patch("inst.security_event_repository.kafka_publisher") as mock_kafka:
            mock_kafka.publish = AsyncMock()
            event = await repo.record_authorized_action(
                LifecycleAction.CREATE,
                sample_subject,
                sample_instruction,
                version_number=1,
            )
    assert isinstance(event, SecurityEvent)
    assert event.event_id == "20260628-FICC-I-1-SE-1"
    assert event.event.action == "CREATE"
