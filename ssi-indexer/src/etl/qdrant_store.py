from __future__ import annotations

import logging
import statistics
import uuid
from typing import Any

from qdrant_client import QdrantClient
from qdrant_client.http import models

from etl.config import settings
from etl.enrichment import EnrichedSecurityEventDocument

logger = logging.getLogger(__name__)

# One Qdrant point per business record; search_text is a flattened field subset (not chunked).
INDEXING_MODEL = "one_point_per_record"


def _estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, round(len(text.split()) * 1.3))


def _numeric_summary(values: list[int]) -> dict[str, int | float]:
    if not values:
        return {"min": 0, "max": 0, "avg": 0, "median": 0}
    return {
        "min": min(values),
        "max": max(values),
        "avg": round(sum(values) / len(values)),
        "median": int(statistics.median(values)),
    }


def _chunk_record_id(payload: dict[str, Any]) -> str | None:
    return (
        payload.get("event_id")
        or payload.get("payment_id")
        or payload.get("instruction_id")
    )


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

    def search_text_chunk_stats(self, *, top_n: int = 10) -> dict[str, Any]:
        """Summarize indexed search_text sizes and return the largest points."""
        if self._client is None:
            raise RuntimeError("Qdrant client not connected")
        if not self.has_collection():
            return {
                "collection": settings.qdrant_collection,
                "indexing_model": INDEXING_MODEL,
                "points_count": 0,
                "search_text_field": "search_text",
                "summary": {
                    "char_count": _numeric_summary([]),
                    "word_count": _numeric_summary([]),
                    "estimated_tokens": _numeric_summary([]),
                },
                "by_source": {},
                "top_chunks": [],
            }

        rows: list[dict[str, Any]] = []
        offset: str | int | None = None
        while True:
            batch, offset = self._client.scroll(
                collection_name=settings.qdrant_collection,
                limit=100,
                offset=offset,
                with_payload=True,
                with_vectors=False,
            )
            for record in batch:
                payload = dict(record.payload or {})
                text = str(payload.get("search_text") or "")
                char_count = len(text)
                word_count = len(text.split())
                rows.append(
                    {
                        "point_id": str(record.id),
                        "source": payload.get("source") or "unknown",
                        "event_id": payload.get("event_id"),
                        "instruction_id": payload.get("instruction_id"),
                        "payment_id": payload.get("payment_id"),
                        "record_id": _chunk_record_id(payload),
                        "char_count": char_count,
                        "word_count": word_count,
                        "estimated_tokens": _estimate_tokens(text),
                        "preview": text[:240].replace("\n", " "),
                    }
                )
            if offset is None:
                break

        char_counts = [row["char_count"] for row in rows]
        word_counts = [row["word_count"] for row in rows]
        token_counts = [row["estimated_tokens"] for row in rows]

        by_source: dict[str, dict[str, int | float]] = {}
        for source in {row["source"] for row in rows}:
            source_chars = [row["char_count"] for row in rows if row["source"] == source]
            by_source[source] = {
                "count": len(source_chars),
                "max_chars": max(source_chars) if source_chars else 0,
                "avg_chars": round(sum(source_chars) / len(source_chars)) if source_chars else 0,
            }

        top_chunks = sorted(rows, key=lambda row: row["char_count"], reverse=True)[:top_n]
        for index, row in enumerate(top_chunks, start=1):
            row["rank"] = index

        return {
            "collection": settings.qdrant_collection,
            "indexing_model": INDEXING_MODEL,
            "points_count": len(rows),
            "search_text_field": "search_text",
            "summary": {
                "char_count": _numeric_summary(char_counts),
                "word_count": _numeric_summary(word_counts),
                "estimated_tokens": _numeric_summary(token_counts),
            },
            "by_source": by_source,
            "top_chunks": top_chunks,
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
        payload["source"] = "instruction_security_event"

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

    def upsert_payment_point(
        self,
        point_id: str,
        search_text: str,
        payload: dict,
        *,
        dense_vector: list[float],
    ) -> None:
        """Upsert a payment or payment security event into the shared Qdrant collection."""
        if self._client is None:
            raise RuntimeError("Qdrant client not connected")

        self._client.upsert(
            collection_name=settings.qdrant_collection,
            points=[
                models.PointStruct(
                    id=point_id,
                    vector={
                        settings.qdrant_dense_vector_name: dense_vector,
                        settings.qdrant_bm25_vector_name: models.Document(
                            text=search_text,
                            model=settings.qdrant_bm25_model,
                        ),
                    },
                    payload=payload,
                )
            ],
        )

    def upsert_instruction_state(
        self,
        instruction_id: str,
        search_text: str,
        payload: dict,
        *,
        dense_vector: list[float],
    ) -> None:
        """Upsert a single instruction-state point (one per instruction, keyed by instruction_id)."""
        if self._client is None:
            raise RuntimeError("Qdrant client not connected")

        point_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"instruction:{instruction_id}"))
        payload = dict(payload)
        payload["source"] = "instruction_state"
        payload["instruction_id"] = instruction_id

        self._client.upsert(
            collection_name=settings.qdrant_collection,
            points=[
                models.PointStruct(
                    id=point_id,
                    vector={
                        settings.qdrant_dense_vector_name: dense_vector,
                        settings.qdrant_bm25_vector_name: models.Document(
                            text=search_text,
                            model=settings.qdrant_bm25_model,
                        ),
                    },
                    payload=payload,
                )
            ],
        )

    def get_instruction_state_payload(self, instruction_id: str) -> dict[str, Any] | None:
        if self._client is None:
            return None
        point_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"instruction:{instruction_id}"))
        records = self._client.retrieve(
            collection_name=settings.qdrant_collection,
            ids=[point_id],
            with_payload=True,
        )
        if not records:
            return None
        return dict(records[0].payload or {})

    def patch_instruction_state_authorization(
        self,
        instruction_id: str,
        *,
        approved_at: str | None,
        authorization_summary: str | None,
        authorization_basis: list[str] | None,
    ) -> None:
        """Merge approval authorization onto an existing instruction-state point."""
        if self._client is None or not authorization_summary:
            return

        point_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"instruction:{instruction_id}"))
        records = self._client.retrieve(
            collection_name=settings.qdrant_collection,
            ids=[point_id],
            with_payload=True,
            with_vectors=True,
        )
        if not records:
            return

        record = records[0]
        payload = dict(record.payload or {})
        basis = list(authorization_basis or [])
        payload["approved_at"] = approved_at or payload.get("approved_at")
        payload["authorization_summary"] = authorization_summary
        payload["authorization_basis"] = basis
        extra = " ".join(
            part
            for part in [approved_at or "", authorization_summary, " ".join(basis)]
            if part
        )
        if extra:
            payload["search_text"] = f"{payload.get('search_text', '')} {extra}".strip()

        self._client.upsert(
            collection_name=settings.qdrant_collection,
            points=[
                models.PointStruct(
                    id=point_id,
                    vector=record.vector,
                    payload=payload,
                )
            ],
        )
