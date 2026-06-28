"""Tests for etl.neo4j_client with mocked AsyncDriver."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from etl.neo4j_client import (
    Neo4jGraphWriter,
    _instruction_version_key,
    _payment_version_number,
    _roles_json,
)


def test_roles_json():
    assert _roles_json(["a", "b"]) == json.dumps(["a", "b"])
    assert _roles_json(None) is None
    assert _roles_json([]) is None


def test_instruction_version_key():
    assert _instruction_version_key("20260628-FICC-I-1", 2) == "20260628-FICC-I-1:2"


def test_payment_version_number_from_lifecycle():
    assert _payment_version_number({"lifecycle_events": [{}, {}]}) == 2
    assert _payment_version_number({"payment_snapshot": {"version_number": 3}}) == 3
    assert _payment_version_number({}) == 1


def _async_iter(records):
    async def _gen():
        for record in records:
            yield record

    return _gen()


class FakeRecord:
    def __init__(self, data: dict):
        self._data = data

    def __getitem__(self, key):
        return self._data[key]

    def keys(self):
        return self._data.keys()


class FakeNode:
    def __init__(self, props: dict):
        self._props = props

    def items(self):
        return self._props.items()


@pytest.fixture
def writer_with_driver() -> Neo4jGraphWriter:
    writer = Neo4jGraphWriter()
    writer._driver = AsyncMock()
    return writer


async def test_graph_stats(writer_with_driver: Neo4jGraphWriter):
    records = [
        FakeRecord({"label": "SecurityEvent", "count": 10}),
        FakeRecord({"label": "User", "count": 5}),
    ]
    result_mock = MagicMock()
    result_mock.__aiter__ = lambda self: _async_iter(records)

    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    session.run = AsyncMock(return_value=result_mock)
    writer_with_driver._driver.session = MagicMock(return_value=session)

    stats = await writer_with_driver.graph_stats()
    assert stats == {"SecurityEvent": 10, "User": 5}


async def test_graph_stats_not_connected():
    writer = Neo4jGraphWriter()
    with pytest.raises(RuntimeError, match="not connected"):
        await writer.graph_stats()


async def test_run_read_cypher_plain_values(writer_with_driver: Neo4jGraphWriter):
    records = [FakeRecord({"name": "alice", "count": 3})]
    result_mock = MagicMock()
    result_mock.__aiter__ = lambda self: _async_iter(records)

    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    session.run = AsyncMock(return_value=result_mock)
    writer_with_driver._driver.session = MagicMock(return_value=session)

    rows = await writer_with_driver.run_read_cypher("MATCH (n) RETURN n.name AS name, 3 AS count LIMIT 1")
    assert rows == [{"name": "alice", "count": 3}]
    writer_with_driver._driver.session.assert_called_once_with(default_access_mode="READ")


async def test_run_read_cypher_node_and_list_values(writer_with_driver: Neo4jGraphWriter):
    node = FakeNode({"user_id": "u1", "lob": "LOB-A"})
    records = [FakeRecord({"user": node, "tags": [FakeNode({"tag": "a"}), "plain"]})]
    result_mock = MagicMock()
    result_mock.__aiter__ = lambda self: _async_iter(records)

    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    session.run = AsyncMock(return_value=result_mock)
    writer_with_driver._driver.session = MagicMock(return_value=session)

    rows = await writer_with_driver.run_read_cypher("MATCH (u:User) RETURN u LIMIT 1")
    assert rows[0]["user"] == {"user_id": "u1", "lob": "LOB-A"}
    assert rows[0]["tags"][0] == {"tag": "a"}
    assert rows[0]["tags"][1] == "plain"


async def test_run_read_cypher_not_connected():
    writer = Neo4jGraphWriter()
    with pytest.raises(RuntimeError, match="not connected"):
        await writer.run_read_cypher("MATCH (n) RETURN n LIMIT 1")


async def test_connect_and_close():
    writer = Neo4jGraphWriter()
    mock_driver = AsyncMock()
    mock_driver.verify_connectivity = AsyncMock()

    with (
        patch("etl.neo4j_client.AsyncGraphDatabase.driver", return_value=mock_driver),
        patch.object(writer, "_apply_schema", AsyncMock()),
    ):
        await writer.connect()
        assert writer._driver is mock_driver
        await writer.close()
        mock_driver.close.assert_awaited_once()
        assert writer._driver is None


async def test_apply_schema_missing_file():
    writer = Neo4jGraphWriter()
    writer._driver = AsyncMock()
    with patch("etl.neo4j_client.settings") as mock_settings:
        mock_settings.graph_model_dir = "/nonexistent/path"
        await writer._apply_schema()
    assert writer._schema_applied is False


async def test_search_events(writer_with_driver: Neo4jGraphWriter):
    records = [FakeRecord({"e": {"event_id": "e1", "message": "denied"}})]
    result_mock = MagicMock()
    result_mock.__aiter__ = lambda self: _async_iter(records)

    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    session.run = AsyncMock(return_value=result_mock)
    writer_with_driver._driver.session = MagicMock(return_value=session)

    events = await writer_with_driver.search_events(text="deny", action="READ", limit=5)
    assert len(events) == 1
    assert events[0]["event_id"] == "e1"


async def test_apply_schema_applies_statements(tmp_path):
    schema_dir = tmp_path / "schema"
    schema_dir.mkdir()
    schema_file = schema_dir / "schema.cypher"
    schema_file.write_text(
        "CREATE INDEX idx IF NOT EXISTS FOR (n:User) ON (n.user_id);\n"
        "// comment only\n"
        "CREATE CONSTRAINT c IF NOT EXISTS FOR (n:User) REQUIRE n.user_id IS UNIQUE;",
        encoding="utf-8",
    )

    writer = Neo4jGraphWriter()
    writer._driver = AsyncMock()
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    session.run = AsyncMock()
    writer._driver.session = MagicMock(return_value=session)

    with patch("etl.neo4j_client.settings") as mock_settings:
        mock_settings.graph_model_dir = str(schema_dir)
        await writer._apply_schema()

    assert writer._schema_applied is True
    # 9 graph repair queries + 1 schema statement (comment-only chunk skipped)
    assert session.run.await_count == 10
