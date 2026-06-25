from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from security_event_qdrant_etl.config import settings
from security_event_qdrant_etl.kafka_consumer import SecurityEventKafkaConsumer
from security_event_qdrant_etl.neo4j_client import Neo4jGraphWriter
from security_event_qdrant_etl.ollama_client import OllamaEmbeddingClient
from security_event_qdrant_etl.qdrant_store import QdrantHybridStore

logger = logging.getLogger(__name__)

ComponentStatus = dict[str, Any]


def _status(ok: bool, status: str, **extra: Any) -> ComponentStatus:
    return {"ok": ok, "status": status, **extra}


async def check_kafka(kafka_consumer: SecurityEventKafkaConsumer) -> ComponentStatus:
    base = {
        "bootstrap_servers": settings.kafka_bootstrap_servers,
        "topic": settings.kafka_security_events_topic,
        "consumer_group": settings.kafka_consumer_group,
    }
    if not settings.kafka_enabled:
        return _status(True, "disabled", detail="Kafka consumer disabled", **base)

    if kafka_consumer._consumer is None or kafka_consumer._task is None:
        return _status(False, "down", detail="consumer not started", **base)

    if kafka_consumer._task.done():
        exc = kafka_consumer._task.exception()
        return _status(
            False,
            "down",
            detail=str(exc) if exc else "consumer task stopped",
            **base,
        )

    try:
        cluster = kafka_consumer._consumer._client.cluster
        broker_count = len(cluster.brokers()) if cluster else 0
    except Exception as exc:
        logger.warning("kafka cluster metadata unavailable: %s", exc)
        broker_count = None

    return _status(
        True,
        "up",
        consumer="running",
        brokers=broker_count,
        **base,
    )


def _qdrant_vector_names(info: Any) -> tuple[set[str], set[str]]:
    dense_names: set[str] = set()
    sparse_names: set[str] = set()

    params = getattr(info.config, "params", None)
    if params is None:
        return dense_names, sparse_names

    vectors = getattr(params, "vectors", None)
    if vectors is None:
        pass
    elif isinstance(vectors, dict):
        dense_names = set(vectors.keys())
    else:
        dense_names = {settings.qdrant_dense_vector_name}

    sparse_vectors = getattr(params, "sparse_vectors", None)
    if isinstance(sparse_vectors, dict):
        sparse_names = set(sparse_vectors.keys())

    return dense_names, sparse_names


def check_qdrant_vector(qdrant_store: QdrantHybridStore) -> ComponentStatus:
    base = {
        "url": settings.qdrant_url,
        "collection": settings.qdrant_collection,
        "vector": settings.qdrant_dense_vector_name,
    }
    if qdrant_store._client is None:
        return _status(False, "down", detail="client not connected", **base)

    try:
        qdrant_store._client.get_collections()
        if not qdrant_store.has_collection():
            return _status(
                False,
                "empty",
                detail="collection not created yet",
                points_count=0,
                **base,
            )

        info = qdrant_store._client.get_collection(settings.qdrant_collection)
        dense_names, _ = _qdrant_vector_names(info)
        if settings.qdrant_dense_vector_name not in dense_names:
            return _status(
                False,
                "down",
                detail=f"dense vector {settings.qdrant_dense_vector_name!r} missing",
                points_count=info.points_count,
                **base,
            )

        return _status(
            True,
            "up",
            points_count=info.points_count,
            **base,
        )
    except Exception as exc:
        logger.warning("qdrant vector health check failed: %s", exc)
        return _status(False, "down", detail=str(exc), **base)


def check_qdrant_bm25(qdrant_store: QdrantHybridStore) -> ComponentStatus:
    base = {
        "url": settings.qdrant_url,
        "collection": settings.qdrant_collection,
        "vector": settings.qdrant_bm25_vector_name,
        "model": settings.qdrant_bm25_model,
    }
    if qdrant_store._client is None:
        return _status(False, "down", detail="client not connected", **base)

    try:
        qdrant_store._client.get_collections()
        if not qdrant_store.has_collection():
            return _status(
                False,
                "empty",
                detail="collection not created yet",
                points_count=0,
                **base,
            )

        info = qdrant_store._client.get_collection(settings.qdrant_collection)
        _, sparse_names = _qdrant_vector_names(info)
        if settings.qdrant_bm25_vector_name not in sparse_names:
            return _status(
                False,
                "down",
                detail=f"BM25 vector {settings.qdrant_bm25_vector_name!r} missing",
                points_count=info.points_count,
                **base,
            )

        return _status(
            True,
            "up",
            points_count=info.points_count,
            **base,
        )
    except Exception as exc:
        logger.warning("qdrant bm25 health check failed: %s", exc)
        return _status(False, "down", detail=str(exc), **base)


async def check_ollama(ollama_client: OllamaEmbeddingClient) -> ComponentStatus:
    base = {
        "url": settings.ollama_url,
        "model": settings.ollama_embedding_model,
    }
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(f"{settings.ollama_url.rstrip('/')}/api/tags")
            response.raise_for_status()
            body = response.json()

        available_names = [
            model.get("name", "")
            for model in body.get("models", [])
            if isinstance(model, dict) and model.get("name")
        ]
        requested = settings.ollama_embedding_model
        requested_base = requested.split(":")[0]
        model_available = any(
            name == requested
            or name.startswith(f"{requested_base}:")
            or name.split(":")[0] == requested_base
            for name in available_names
        )

        dimension = ollama_client._dimension
        extra: dict[str, Any] = {}
        if dimension is not None:
            extra["dimension"] = dimension
            extra["embeddings"] = "ready"

        if not model_available:
            return _status(
                False,
                "down",
                detail=f"model {settings.ollama_embedding_model!r} not found",
                models=available_names,
                **base,
                **extra,
            )

        return _status(
            True,
            "up",
            models_available=len(body.get("models", [])),
            **base,
            **extra,
        )
    except Exception as exc:
        logger.warning("ollama health check failed: %s", exc)
        return _status(False, "down", detail=str(exc), **base)


async def check_neo4j(neo4j_writer: Neo4jGraphWriter) -> ComponentStatus:
    base = {"uri": settings.neo4j_uri}
    if neo4j_writer._driver is None:
        return _status(False, "down", detail="driver not connected", **base)

    try:
        await neo4j_writer._driver.verify_connectivity()
        async with neo4j_writer._driver.session() as session:
            result = await session.run("RETURN 1 AS ok")
            await result.single()

        stats = await neo4j_writer.graph_stats()
        total_nodes = sum(stats.values())
        return _status(
            True,
            "up",
            total_nodes=total_nodes,
            labels=stats,
            **base,
        )
    except Exception as exc:
        logger.warning("neo4j health check failed: %s", exc)
        return _status(False, "down", detail=str(exc), **base)


async def component_status(
    *,
    kafka_consumer: SecurityEventKafkaConsumer,
    qdrant_store: QdrantHybridStore,
    neo4j_writer: Neo4jGraphWriter,
    ollama_client: OllamaEmbeddingClient,
) -> dict[str, ComponentStatus]:
    kafka_status, neo4j_status, ollama_status = await asyncio.gather(
        check_kafka(kafka_consumer),
        check_neo4j(neo4j_writer),
        check_ollama(ollama_client),
    )
    return {
        "kafka": kafka_status,
        "ollama": ollama_status,
        "qdrant_vector": await asyncio.to_thread(check_qdrant_vector, qdrant_store),
        "qdrant_bm25": await asyncio.to_thread(check_qdrant_bm25, qdrant_store),
        "neo4j": neo4j_status,
    }
