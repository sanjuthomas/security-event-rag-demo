from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from ps.models.enums import PaymentAction, SecurityEventSeverity
from ps.models.payment import Payment
from ps.repository import PaymentNotFoundError, PaymentRepository
from ps.security_event_repository import SecurityEventRepository


@pytest.fixture
def mock_collection() -> AsyncMock:
    col = AsyncMock()
    col.insert_one = AsyncMock()
    col.find_one = AsyncMock()
    col.replace_one = AsyncMock()
    col.create_index = AsyncMock()
    return col


@pytest.fixture
def patched_db(mock_collection: AsyncMock):
    with patch("ps.repository.get_db", return_value={"payments": mock_collection}):
        yield mock_collection


@pytest.mark.asyncio
async def test_insert(patched_db: AsyncMock, payment: Payment) -> None:
    repo = PaymentRepository()
    await repo.insert(payment)
    patched_db.insert_one.assert_awaited_once()


@pytest.mark.asyncio
async def test_find_by_id_found(patched_db: AsyncMock, payment: Payment) -> None:
    patched_db.find_one.return_value = payment.to_mongo()
    repo = PaymentRepository()
    found = await repo.find_by_id(payment.payment_id)
    assert found.payment_id == payment.payment_id


@pytest.mark.asyncio
async def test_find_by_id_missing(patched_db: AsyncMock) -> None:
    patched_db.find_one.return_value = None
    repo = PaymentRepository()
    with pytest.raises(PaymentNotFoundError):
        await repo.find_by_id("missing")


@pytest.mark.asyncio
async def test_update(patched_db: AsyncMock, payment: Payment) -> None:
    repo = PaymentRepository()
    await repo.update(payment)
    patched_db.replace_one.assert_awaited_once()


@pytest.mark.asyncio
async def test_list_with_filters(patched_db: AsyncMock, payment: Payment) -> None:
    class AsyncCursor:
        def __init__(self, docs):
            self._docs = docs

        def sort(self, *_args, **_kwargs):
            return self

        def limit(self, *_args, **_kwargs):
            return self

        def __aiter__(self):
            async def generator():
                for doc in self._docs:
                    yield doc

            return generator()

    patched_db.find = MagicMock(return_value=AsyncCursor([payment.to_mongo()]))
    repo = PaymentRepository()
    items = await repo.list(instruction_id="instr-001", status="DRAFT", limit=5)
    assert len(items) == 1
    patched_db.find.assert_called_once_with(
        {"instruction_id": "instr-001", "status": "DRAFT"}
    )


@pytest.mark.asyncio
async def test_ensure_indexes(patched_db: AsyncMock) -> None:
    repo = PaymentRepository()
    await repo.ensure_indexes()
    assert patched_db.create_index.await_count == 4


@pytest.fixture
def event_collection() -> AsyncMock:
    col = AsyncMock()
    col.insert_one = AsyncMock()
    col.create_index = AsyncMock()
    return col


@pytest.fixture
def event_repo(event_collection: AsyncMock):
    with patch(
        "ps.security_event_repository.get_security_events_db",
        return_value={"payment-service": event_collection},
    ):
        yield SecurityEventRepository(), event_collection


@pytest.mark.asyncio
async def test_security_event_insert(
    event_repo,
    subject,
    payment: Payment,
) -> None:
    repo, col = event_repo
    event = await repo.record_authorized_action(
        PaymentAction.CREATE_PAYMENT,
        subject,
        payment,
        details={"authorization": {"summary": "ok"}},
    )
    col.insert_one.assert_awaited_once()
    assert event.event.action == "CREATE_PAYMENT"


@pytest.mark.asyncio
async def test_security_event_policy_denial(
    event_repo,
    subject,
    payment: Payment,
) -> None:
    repo, col = event_repo
    event = await repo.record_policy_denial(
        PaymentAction.APPROVE_PAYMENT,
        subject,
        payment,
        reason="denied",
    )
    col.insert_one.assert_awaited_once()
    assert event.severity == SecurityEventSeverity.ALERT


@pytest.mark.asyncio
async def test_security_event_publish_failure_logged(
    event_repo,
    subject,
    payment: Payment,
) -> None:
    repo, _col = event_repo
    with patch(
        "ps.security_event_repository.kafka_publisher.publish_security_event",
        new_callable=AsyncMock,
        side_effect=RuntimeError("kafka down"),
    ):
        await repo.record_authorized_action(PaymentAction.SUBMIT_PAYMENT, subject, payment)


@pytest.mark.asyncio
async def test_security_event_ensure_indexes(event_repo) -> None:
    repo, col = event_repo
    await repo.ensure_indexes()
    assert col.create_index.await_count == 7
