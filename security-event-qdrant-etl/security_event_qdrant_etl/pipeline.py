from __future__ import annotations

import logging
from typing import Any

from security_event_qdrant_etl.enrichment import enrich_document
from security_event_qdrant_etl.instruction_client import InstructionClient
from security_event_qdrant_etl.neo4j_client import Neo4jGraphWriter
from security_event_qdrant_etl.ollama_client import OllamaEmbeddingClient
from security_event_qdrant_etl.qdrant_store import QdrantHybridStore

logger = logging.getLogger(__name__)


class SecurityEventPipeline:
    def __init__(
        self,
        *,
        instruction_store: InstructionClient,
        neo4j_writer: Neo4jGraphWriter,
        ollama_client: OllamaEmbeddingClient,
        qdrant_store: QdrantHybridStore,
    ) -> None:
        self.instruction_store = instruction_store
        self.neo4j_writer = neo4j_writer
        self.ollama_client = ollama_client
        self.qdrant_store = qdrant_store
        self._qdrant_ready = False

    async def start(self) -> None:
        await self.instruction_store.connect()
        await self.neo4j_writer.connect()
        self.qdrant_store.connect()
        logger.info("security event ETL pipeline ready (Ollama/Qdrant init on first event)")

    async def close(self) -> None:
        await self.instruction_store.close()
        await self.neo4j_writer.close()
        self.qdrant_store.close()

    async def process_security_event(self, security_event: dict[str, Any]) -> None:
        resource = security_event.get("resource") or {}
        instruction_id = resource.get("id")

        instruction = None
        if instruction_id:
            instruction = await self.instruction_store.fetch_instruction(instruction_id)

        document = enrich_document(security_event, instruction)

        await self.neo4j_writer.upsert(document)

        if not self._qdrant_ready:
            await self.ollama_client.warmup()
            self.qdrant_store.ensure_collection(self.ollama_client.dimension)
            self._qdrant_ready = True

        dense_vector = await self.ollama_client.embed(document.search_text)
        self.qdrant_store.upsert(document, dense_vector=dense_vector)

        logger.info(
            "processed event_id=%s instruction_id=%s",
            document.event_id,
            document.instruction_id,
        )
