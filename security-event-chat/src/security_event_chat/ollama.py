from __future__ import annotations

import json
import logging
import re
from typing import Any

import httpx

from security_event_chat.config import settings

logger = logging.getLogger(__name__)

CYPHER_SYSTEM_PROMPT = """You translate natural-language questions about SSI instruction lifecycle \
security events into read-only Neo4j Cypher.

Rules:
- Output ONLY a single Cypher query. No markdown fences, no explanation.
- READ-ONLY: use MATCH, OPTIONAL MATCH, WITH, WHERE, RETURN, ORDER BY, LIMIT, UNWIND, count(), collect().
- Never use CREATE, MERGE, SET, DELETE, REMOVE, DROP, CALL db.* write procedures.
- Prefer returning SecurityEvent nodes as `e` plus related User/Instruction fields when listing events.
- For counts/aggregations, RETURN named scalars (e.g. total, count).
- User ids are lowercase codes like mo-100, ficc-201, ficc-300 (not display names).
- "Today" means date(datetime(e.timestamp)) = date().
- severity ALERT means policy denial; outcome failure on APPROVE/REJECT etc. means failed attempt.
- wire_scope is DOMESTIC or INTERNATIONAL on SecurityEvent and InstructionVersion.
- instruction_type is STANDING or SINGLE_USE.
- action values: CREATE, SUBMIT, APPROVE, REJECT, SUSPEND, REACTIVATE, USE, UPDATE, DELETE, VIEW.
- Relationship direction matters: (i:Instruction)-[:HAS_VERSION]->(v:InstructionVersion). \
Never traverse HAS_VERSION from InstructionVersion to Instruction.
- SecurityEvent links to Instruction via TARGETS, or to InstructionVersion via TARGETS_VERSION. \
InstructionVersion has instruction_id as a property.
- When the question names a specific event_id UUID, match that SecurityEvent directly. \
Prefer TARGETS_VERSION and return v.instruction_id, or TARGETS and return i.instruction_id. \
Do not chain HAS_VERSION after TARGETS_VERSION.

Example — instruction for a specific security event:
MATCH (e:SecurityEvent {event_id: '00000000-0000-0000-0000-000000000001'})
MATCH (e)-[:TARGETS_VERSION]->(v:InstructionVersion)
RETURN e.event_id, v.instruction_id AS instruction_id
LIMIT 1

Example — instructions created today:
MATCH (e:SecurityEvent {action: 'CREATE', outcome: 'success'})
WHERE date(datetime(e.timestamp)) = date()
RETURN count(DISTINCT e) AS total

Example — who created instructions rejected by a user:
MATCH (u:User {user_id: 'ficc-201'})-[:ACTED_AS]->(e:SecurityEvent {action: 'REJECT'})
MATCH (e)-[:TARGETS_VERSION]->(v:InstructionVersion)
MATCH (creator:User)-[:CREATED]->(v)
RETURN e.event_id, e.timestamp, creator.user_id AS creator_user_id, v.instruction_id
ORDER BY e.timestamp DESC
LIMIT 20
"""

ANSWER_SYSTEM_PROMPT = """You are a security operations analyst assistant for cash settlement instruction \
lifecycle events.

Answer the user's question using ONLY the provided context (retrieved events and graph query results).
- Be concise and factual.
- Cite event ids or instruction ids when relevant.
- If graph results include aggregate counts, use those numbers directly.
- When graph results or retrieved events include instruction_id for a named event_id, use that linkage.
- If context is insufficient, say what is missing.
- Do not invent users, amounts, or events not present in the context.
"""


class OllamaClient:
    def __init__(self) -> None:
        self._dimension: int | None = None

    @property
    def dimension(self) -> int:
        if self._dimension is None:
            raise RuntimeError("embedding dimension not initialized")
        return self._dimension

    async def embed(self, text: str) -> list[float]:
        payload = {"model": settings.ollama_embedding_model, "input": text}
        async with httpx.AsyncClient(timeout=settings.ollama_timeout_seconds) as client:
            response = await client.post(
                f"{settings.ollama_url.rstrip('/')}/api/embed",
                json=payload,
            )
            response.raise_for_status()
            body = response.json()

        embeddings = body.get("embeddings")
        vector = embeddings[0] if isinstance(embeddings, list) and embeddings else body.get("embedding")
        if not isinstance(vector, list) or not vector:
            raise RuntimeError(f"unexpected embed response: {json.dumps(body)[:300]}")

        self._dimension = len(vector)
        return [float(v) for v in vector]

    async def chat(
        self,
        *,
        system: str,
        user: str,
        history: list[dict[str, str]] | None = None,
    ) -> str:
        messages: list[dict[str, str]] = [{"role": "system", "content": system}]
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": user})

        async with httpx.AsyncClient(timeout=settings.ollama_timeout_seconds) as client:
            response = await client.post(
                f"{settings.ollama_url.rstrip('/')}/api/chat",
                json={
                    "model": settings.ollama_chat_model,
                    "messages": messages,
                    "stream": False,
                },
            )
            response.raise_for_status()
            body = response.json()

        message = body.get("message") or {}
        content = message.get("content") if isinstance(message, dict) else None
        if not content:
            raise RuntimeError(f"unexpected chat response: {json.dumps(body)[:300]}")
        return str(content).strip()

    async def generate_cypher(self, question: str, schema: str) -> str:
        user_prompt = f"""Graph schema documentation:

{schema}

Question: {question}

Cypher:"""
        raw = await self.chat(system=CYPHER_SYSTEM_PROMPT, user=user_prompt)
        return _extract_cypher(raw)

    async def synthesize_answer(
        self,
        question: str,
        context: str,
        history: list[dict[str, str]] | None = None,
    ) -> str:
        user_prompt = f"""Context:

{context}

Question: {question}"""
        return await self.chat(
            system=ANSWER_SYSTEM_PROMPT,
            user=user_prompt,
            history=history,
        )


def _extract_cypher(raw: str) -> str:
    text = raw.strip()
    fence = re.search(r"```(?:cypher)?\s*(.*?)```", text, flags=re.DOTALL | re.IGNORECASE)
    if fence:
        text = fence.group(1).strip()
    lines = [line for line in text.splitlines() if not line.strip().startswith("//")]
    return "\n".join(lines).strip()
