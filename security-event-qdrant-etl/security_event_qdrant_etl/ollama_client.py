from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import httpx

from security_event_qdrant_etl.config import settings

logger = logging.getLogger(__name__)


class OllamaEmbeddingClient:
    def __init__(self) -> None:
        self._dimension: int | None = None

    @property
    def dimension(self) -> int:
        if self._dimension is None:
            raise RuntimeError("Ollama embedding dimension not initialized")
        return self._dimension

    async def embed(self, text: str) -> list[float]:
        if not text.strip():
            raise ValueError("cannot embed empty text")

        payload = {
            "model": settings.ollama_embedding_model,
            "input": text,
        }
        async with httpx.AsyncClient(timeout=settings.ollama_timeout_seconds) as client:
            response = await client.post(
                f"{settings.ollama_url.rstrip('/')}/api/embed",
                json=payload,
            )
            response.raise_for_status()
            body = response.json()

        embeddings = body.get("embeddings")
        if isinstance(embeddings, list) and embeddings:
            vector = embeddings[0]
        else:
            vector = body.get("embedding")

        if not isinstance(vector, list) or not vector:
            raise RuntimeError(f"unexpected Ollama embed response: {json.dumps(body)[:300]}")

        self._dimension = len(vector)
        return [float(value) for value in vector]

    async def warmup(self) -> None:
        await self.embed("warmup")
        logger.info(
            "Ollama embeddings ready model=%s dimension=%s",
            settings.ollama_embedding_model,
            self._dimension,
        )
