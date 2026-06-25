from __future__ import annotations

import logging
import uuid
from typing import Any

from qdrant_client import QdrantClient
from qdrant_client.http import models

from security_event_chat.config import settings

logger = logging.getLogger(__name__)


class QdrantSearchClient:
    def __init__(self) -> None:
        self._client: QdrantClient | None = None

    def connect(self) -> None:
        self._client = QdrantClient(url=settings.qdrant_url)
        logger.info("Qdrant search client connected url=%s", settings.qdrant_url)

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def has_collection(self) -> bool:
        if self._client is None:
            return False
        return self._client.collection_exists(settings.qdrant_collection)

    def _to_hit(self, point: models.ScoredPoint, source: str) -> dict[str, Any]:
        payload = dict(point.payload or {})
        merged = payload.get("merged") or {}
        security_event = payload.get("security_event") or {}
        return {
            "source": source,
            "score": float(point.score or 0.0),
            "event_id": payload.get("event_id"),
            "instruction_id": payload.get("instruction_id"),
            "search_text": payload.get("search_text", ""),
            "merged": merged,
            "security_event": security_event,
            "payload": payload,
        }

    def search_vector(self, query_vector: list[float], *, limit: int) -> list[dict[str, Any]]:
        if self._client is None or not self.has_collection():
            return []
        response = self._client.query_points(
            collection_name=settings.qdrant_collection,
            query=query_vector,
            using=settings.qdrant_dense_vector_name,
            limit=limit,
            with_payload=True,
        )
        return [self._to_hit(point, "vector") for point in response.points]

    def fetch_by_event_id(self, event_id: str) -> dict[str, Any] | None:
        """Exact lookup by event_id using the deterministic Qdrant point id."""
        if self._client is None or not self.has_collection():
            return None

        point_id = str(uuid.uuid5(uuid.NAMESPACE_URL, event_id))
        records = self._client.retrieve(
            collection_name=settings.qdrant_collection,
            ids=[point_id],
            with_payload=True,
        )
        if not records:
            return None

        payload = dict(records[0].payload or {})
        merged = payload.get("merged") or {}
        security_event = payload.get("security_event") or {}
        return {
            "source": "exact",
            "score": 1.0,
            "event_id": payload.get("event_id"),
            "instruction_id": payload.get("instruction_id"),
            "search_text": payload.get("search_text", ""),
            "merged": merged,
            "security_event": security_event,
            "instruction": payload.get("instruction"),
            "payload": payload,
        }

    def search_bm25(self, query_text: str, *, limit: int) -> list[dict[str, Any]]:
        if self._client is None or not self.has_collection():
            return []
        response = self._client.query_points(
            collection_name=settings.qdrant_collection,
            query=models.Document(text=query_text, model=settings.qdrant_bm25_model),
            using=settings.qdrant_bm25_vector_name,
            limit=limit,
            with_payload=True,
        )
        return [self._to_hit(point, "bm25") for point in response.points]
