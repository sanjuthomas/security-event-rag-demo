from __future__ import annotations

import logging
from typing import Any

from neo4j import AsyncGraphDatabase, AsyncDriver

from security_event_chat.config import settings
from security_event_chat.cypher import (
    LOOKUP_INSTRUCTION_BY_EVENT_CYPHER,
    records_to_rows,
    validate_read_only_cypher,
)

logger = logging.getLogger(__name__)


class Neo4jClient:
    def __init__(self) -> None:
        self._driver: AsyncDriver | None = None

    async def connect(self) -> None:
        self._driver = AsyncGraphDatabase.driver(
            settings.neo4j_uri,
            auth=(settings.neo4j_user, settings.neo4j_password),
        )
        await self._driver.verify_connectivity()
        logger.info("Neo4j client connected")

    async def close(self) -> None:
        if self._driver is not None:
            await self._driver.close()
            self._driver = None

    async def lookup_instruction_for_event(self, event_id: str) -> list[dict[str, Any]]:
        if self._driver is None:
            raise RuntimeError("Neo4j client not connected")

        async with self._driver.session() as session:
            result = await session.run(
                LOOKUP_INSTRUCTION_BY_EVENT_CYPHER,
                event_id=event_id,
            )
            records = [record async for record in result]
        return records_to_rows(records)

    async def run_cypher(self, cypher: str) -> list[dict[str, Any]]:
        if self._driver is None:
            raise RuntimeError("Neo4j client not connected")

        validate_read_only_cypher(cypher)
        async with self._driver.session() as session:
            result = await session.run(cypher)
            records = [record async for record in result]
        return records_to_rows(records)
