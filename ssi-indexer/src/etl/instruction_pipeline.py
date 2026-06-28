"""Processes InstructionFact events from the ssi-instructions Kafka topic.

Maintains the instruction master graph in Neo4j (InstructionVersion nodes,
CONFLICTS_WITH, APPROVED_FOR, BELONGS_TO) and one instruction-state Qdrant
point per instruction — updated in place on every mutation.

No API calls — the fact event is self-contained.
"""

from __future__ import annotations

import logging
from typing import Any

from etl.authorization_context import authorization_merged_from_fact
from etl.neo4j_client import Neo4jGraphWriter
from etl.ollama_client import OllamaEmbeddingClient
from etl.qdrant_store import QdrantHybridStore
from etl.search_text.builder import build_search_text_from_profile
from etl.search_text.context import instruction_state_context

logger = logging.getLogger(__name__)


def build_instruction_state_search_text(fact: dict[str, Any]) -> str:
    return build_search_text_from_profile("instruction_state", instruction_state_context(fact))


class InstructionPipeline:
    def __init__(
        self,
        *,
        neo4j_writer: Neo4jGraphWriter,
        ollama_client: OllamaEmbeddingClient,
        qdrant_store: QdrantHybridStore,
    ) -> None:
        self.neo4j_writer = neo4j_writer
        self.ollama_client = ollama_client
        self.qdrant_store = qdrant_store
        self._qdrant_ready = False

    async def process_instruction_fact(self, fact: dict[str, Any]) -> None:
        instruction_id = fact.get("instruction_id")
        if not instruction_id:
            logger.warning("instruction fact missing instruction_id — skipping")
            return

        await self.neo4j_writer.upsert_instruction_fact(fact)

        if not self._qdrant_ready:
            await self.ollama_client.warmup()
            self.qdrant_store.ensure_collection(self.ollama_client.dimension)
            self._qdrant_ready = True

        search_text = build_instruction_state_search_text(fact)
        dense_vector = await self.ollama_client.embed(search_text)

        snap = fact.get("instruction_snapshot") or {}
        auth_merged = authorization_merged_from_fact(fact)
        payload = {
            "instruction_id": instruction_id,
            "version_number": fact.get("version_number"),
            "action": fact.get("action"),
            "timestamp": fact.get("timestamp"),
            "status": snap.get("status"),
            "instruction_type": snap.get("instruction_type"),
            "owning_lob": snap.get("owning_lob"),
            "wire_scope": snap.get("wire_scope"),
            "currency": snap.get("currency"),
            "effective_date": snap.get("effective_date"),
            "end_date": snap.get("end_date"),
            "approved_at": auth_merged.get("approved_at") or snap.get("approved_at"),
            "submitted_at": auth_merged.get("submitted_at") or snap.get("submitted_at"),
            "creditor_name": (snap.get("creditor") or {}).get("name"),
            "creditor_account_id": (snap.get("creditor_account") or {}).get("identification"),
            "debtor_name": (snap.get("debtor") or {}).get("name"),
            "creator_user_id": (snap.get("created_by") or {}).get("user_id"),
            "creator_display": _display_name(snap.get("created_by") or {}),
            "approver_user_id": (snap.get("approved_by") or {}).get("user_id"),
            "approver_display": _display_name(snap.get("approved_by") or {}),
            "rejector_user_id": (snap.get("rejected_by") or {}).get("user_id"),
            "rejector_display": _display_name(snap.get("rejected_by") or {}),
            "rejected_at": auth_merged.get("rejected_at") or snap.get("rejected_at"),
            "rejection_reason": auth_merged.get("rejection_reason") or snap.get("rejection_reason"),
            "authorization_summary": auth_merged.get("authorization_summary"),
            "authorization_basis": auth_merged.get("authorization_basis") or [],
            "actor_user_id": fact.get("actor_user_id"),
            "actor_display": _display_name(fact, prefix="actor_"),
            "search_text": search_text,
            "instruction_snapshot": snap,
        }

        existing = self.qdrant_store.get_instruction_state_payload(instruction_id)
        if existing:
            if fact.get("action") != "APPROVE":
                if not payload.get("authorization_summary"):
                    payload["authorization_summary"] = existing.get("authorization_summary")
                    payload["authorization_basis"] = existing.get("authorization_basis") or []
                if not payload.get("approved_at"):
                    payload["approved_at"] = existing.get("approved_at")
                if not payload.get("approver_display"):
                    payload["approver_display"] = existing.get("approver_display")
                    payload["approver_user_id"] = existing.get("approver_user_id")
            if fact.get("action") != "REJECT":
                if not payload.get("rejector_display"):
                    payload["rejector_display"] = existing.get("rejector_display")
                    payload["rejector_user_id"] = existing.get("rejector_user_id")
                if not payload.get("rejected_at"):
                    payload["rejected_at"] = existing.get("rejected_at")
                if not payload.get("rejection_reason"):
                    payload["rejection_reason"] = existing.get("rejection_reason")

        self.qdrant_store.upsert_instruction_state(
            instruction_id=instruction_id,
            search_text=search_text,
            payload=payload,
            dense_vector=dense_vector,
        )

        logger.info(
            "processed instruction fact instruction_id=%s action=%s version=%s",
            instruction_id,
            fact.get("action"),
            fact.get("version_number"),
        )


def _display_name(user: dict[str, Any], prefix: str = "") -> str:
    fn = user.get(f"{prefix}family_name") or user.get("family_name")
    gn = user.get(f"{prefix}given_name") or user.get("given_name")
    uid = user.get(f"{prefix}user_id") or user.get("user_id") or ""
    if fn and gn:
        return f"{fn}, {gn} ({uid})"
    return uid
