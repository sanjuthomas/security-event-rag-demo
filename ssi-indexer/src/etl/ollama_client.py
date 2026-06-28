from __future__ import annotations

import json
import logging

import httpx
from cypher_gen import cypher_system_prompt, extract_cypher

from etl.config import settings

logger = logging.getLogger(__name__)


class OllamaEmbeddingClient:
    def __init__(self) -> None:
        self._dimension: int | None = None
        self._http: httpx.AsyncClient | None = None

    @property
    def dimension(self) -> int:
        if self._dimension is None:
            raise RuntimeError("Ollama embedding dimension not initialized")
        return self._dimension

    async def _client(self) -> httpx.AsyncClient:
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(timeout=settings.ollama_timeout_seconds)
        return self._http

    async def close(self) -> None:
        if self._http is not None and not self._http.is_closed:
            await self._http.aclose()
        self._http = None

    async def embed(self, text: str) -> list[float]:
        if not text.strip():
            raise ValueError("cannot embed empty text")

        client = await self._client()
        response = await client.post(
            f"{settings.ollama_url.rstrip('/')}/api/embed",
            json={"model": settings.ollama_embedding_model, "input": text},
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
        return [float(v) for v in vector]

    async def warmup(self) -> None:
        await self.embed("warmup")
        logger.info(
            "Ollama embeddings ready model=%s dimension=%s",
            settings.ollama_embedding_model,
            self._dimension,
        )

    async def chat(
        self,
        *,
        system: str,
        user: str,
    ) -> str:
        client = await self._client()
        response = await client.post(
            f"{settings.ollama_url.rstrip('/')}/api/chat",
            json={
                "model": settings.ollama_chat_model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "stream": False,
            },
        )
        response.raise_for_status()
        body = response.json()
        message = body.get("message") or {}
        content = message.get("content") if isinstance(message, dict) else None
        if not content:
            raise RuntimeError(f"unexpected Ollama chat response: {json.dumps(body)[:300]}")
        return str(content).strip()

    async def generate_cypher(
        self,
        question: str,
        schema: str,
        *,
        mode: str = "events",
    ) -> str:
        system = cypher_system_prompt(mode)
        user_prompt = f"""Graph schema documentation:

{schema}

Question: {question}

Cypher:"""
        raw = await self.chat(system=system, user=user_prompt)
        return extract_cypher(raw)
