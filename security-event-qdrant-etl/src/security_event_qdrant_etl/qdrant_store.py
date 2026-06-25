from __future__ import annotations

import logging
import uuid

from qdrant_client import QdrantClient
from qdrant_client.http import models

from security_event_qdrant_etl.config import settings
from security_event_qdrant_etl.enrichment import EnrichedSecurityEventDocument

logger = logging.getLogger(__name__)


class QdrantHybridStore:
    def __init__(self) -> None:
        self._client: QdrantClient | None = None
        self._collection_ready = False

    def connect(self) -> None:
        self._client = QdrantClient(url=settings.qdrant_url)
        logger.info("Qdrant client connected url=%s", settings.qdrant_url)

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None
            self._collection_ready = False

    def ensure_collection(self, dense_dimension: int) -> None:
        if self._client is None:
            raise RuntimeError("Qdrant client not connected")
        if self._collection_ready:
            return

        collection = settings.qdrant_collection
        if self._client.collection_exists(collection):
            self._collection_ready = True
            return

        self._client.create_collection(
            collection_name=collection,
            vectors_config={
                settings.qdrant_dense_vector_name: models.VectorParams(
                    size=dense_dimension,
                    distance=models.Distance.COSINE,
                )
            },
            sparse_vectors_config={
                settings.qdrant_bm25_vector_name: models.SparseVectorParams(
                    modifier=models.Modifier.IDF,
                )
            },
        )
        self._collection_ready = True
        logger.info(
            "created Qdrant collection=%s dense_dim=%s bm25=%s",
            collection,
            dense_dimension,
            settings.qdrant_bm25_vector_name,
        )

    def has_collection(self) -> bool:
        if self._client is None:
            return False
        return self._client.collection_exists(settings.qdrant_collection)

    def collection_info(self) -> dict:
        if self._client is None or not self.has_collection():
            return {"exists": False, "points_count": 0}
        info = self._client.get_collection(settings.qdrant_collection)
        return {
            "exists": True,
            "points_count": info.points_count,
            "status": str(info.status),
        }

    def _point_to_result(self, point: models.ScoredPoint) -> dict:
        payload = dict(point.payload or {})
        return {
            "score": point.score,
            "event_id": payload.get("event_id"),
            "instruction_id": payload.get("instruction_id"),
            "search_text": payload.get("search_text"),
            "security_event": payload.get("security_event"),
            "payload": payload,
        }

    def search_dense(self, query_vector: list[float], *, limit: int) -> list[dict]:
        if self._client is None:
            raise RuntimeError("Qdrant client not connected")
        if not self.has_collection():
            return []

        response = self._client.query_points(
            collection_name=settings.qdrant_collection,
            query=query_vector,
            using=settings.qdrant_dense_vector_name,
            limit=limit,
            with_payload=True,
        )
        return [self._point_to_result(point) for point in response.points]

    def search_bm25(self, query_text: str, *, limit: int) -> list[dict]:
        if self._client is None:
            raise RuntimeError("Qdrant client not connected")
        if not self.has_collection():
            return []

        response = self._client.query_points(
            collection_name=settings.qdrant_collection,
            query=models.Document(text=query_text, model=settings.qdrant_bm25_model),
            using=settings.qdrant_bm25_vector_name,
            limit=limit,
            with_payload=True,
        )
        return [self._point_to_result(point) for point in response.points]

    def search_hybrid(
        self,
        query_text: str,
        query_vector: list[float],
        *,
        limit: int,
    ) -> list[dict]:
        if self._client is None:
            raise RuntimeError("Qdrant client not connected")
        if not self.has_collection():
            return []

        prefetch_limit = max(limit * 2, 10)
        response = self._client.query_points(
            collection_name=settings.qdrant_collection,
            prefetch=[
                models.Prefetch(
                    query=query_vector,
                    using=settings.qdrant_dense_vector_name,
                    limit=prefetch_limit,
                ),
                models.Prefetch(
                    query=models.Document(text=query_text, model=settings.qdrant_bm25_model),
                    using=settings.qdrant_bm25_vector_name,
                    limit=prefetch_limit,
                ),
            ],
            query=models.FusionQuery(fusion=models.Fusion.RRF),
            limit=limit,
            with_payload=True,
        )
        return [self._point_to_result(point) for point in response.points]

    def upsert(
        self,
        document: EnrichedSecurityEventDocument,
        *,
        dense_vector: list[float],
    ) -> None:
        if self._client is None:
            raise RuntimeError("Qdrant client not connected")

        point_id = str(uuid.uuid5(uuid.NAMESPACE_URL, document.event_id))
        payload = document.model_dump(mode="json")

        self._client.upsert(
            collection_name=settings.qdrant_collection,
            points=[
                models.PointStruct(
                    id=point_id,
                    vector={
                        settings.qdrant_dense_vector_name: dense_vector,
                        settings.qdrant_bm25_vector_name: models.Document(
                            text=document.search_text,
                            model=settings.qdrant_bm25_model,
                        ),
                    },
                    payload=payload,
                )
            ],
        )
