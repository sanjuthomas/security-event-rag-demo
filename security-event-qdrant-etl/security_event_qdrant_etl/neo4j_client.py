from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from neo4j import AsyncGraphDatabase, AsyncDriver

from security_event_qdrant_etl.config import settings
from security_event_qdrant_etl.enrichment import EnrichedSecurityEventDocument

logger = logging.getLogger(__name__)


class Neo4jGraphWriter:
    def __init__(self) -> None:
        self._driver: AsyncDriver | None = None
        self._schema_applied = False

    async def connect(self) -> None:
        self._driver = AsyncGraphDatabase.driver(
            settings.neo4j_uri,
            auth=(settings.neo4j_user, settings.neo4j_password),
        )
        await self._driver.verify_connectivity()
        await self._apply_schema()
        logger.info("Neo4j graph writer connected")

    async def close(self) -> None:
        if self._driver is not None:
            await self._driver.close()
            self._driver = None

    async def _apply_schema(self) -> None:
        if self._schema_applied or self._driver is None:
            return

        schema_path = Path(settings.graph_model_dir) / "schema.cypher"
        if not schema_path.is_file():
            logger.warning("Neo4j schema file not found: %s", schema_path)
            return

        statements = [
            chunk.strip()
            for chunk in schema_path.read_text(encoding="utf-8").split(";")
            if chunk.strip() and not chunk.strip().startswith("//")
        ]
        async with self._driver.session() as session:
            for statement in statements:
                await session.run(statement)
        self._schema_applied = True
        logger.info("applied %s Neo4j schema statement(s)", len(statements))

    async def upsert(self, document: EnrichedSecurityEventDocument) -> None:
        if self._driver is None:
            raise RuntimeError("Neo4j writer not connected")

        event = document.security_event
        actor = event.get("actor") or {}
        resource = event.get("resource") or {}
        event_ctx = event.get("event") or {}
        source = event.get("source") or {}
        merged = document.merged or {}
        instruction = document.instruction or {}
        created_by = instruction.get("created_by") or {}
        approved_by = instruction.get("approved_by") or {}
        rejected_by = instruction.get("rejected_by") or {}
        version_number = document.version_number or instruction.get("version_number")
        version_key = (
            f"{document.instruction_id}:{version_number}"
            if document.instruction_id and version_number is not None
            else None
        )
        owning_lob = merged.get("owning_lob") or resource.get("owning_lob")
        wire_scope = merged.get("wire_scope")
        instruction_type = merged.get("instruction_type")
        currency = merged.get("currency")

        async with self._driver.session() as session:
            await session.run(
                """
                MERGE (e:SecurityEvent {event_id: $event_id})
                SET e.timestamp = $timestamp,
                    e.severity = $severity,
                    e.message = $message,
                    e.action = $action,
                    e.outcome = $outcome,
                    e.reason = $reason,
                    e.source_application = $source_application,
                    e.wire_scope = $wire_scope,
                    e.instruction_type = $instruction_type,
                    e.owning_lob = $owning_lob
                """,
                event_id=document.event_id,
                timestamp=event.get("timestamp"),
                severity=event.get("severity"),
                message=event.get("message"),
                action=event_ctx.get("action"),
                outcome=event_ctx.get("outcome"),
                reason=event_ctx.get("reason"),
                source_application=source.get("application"),
                wire_scope=wire_scope,
                instruction_type=instruction_type,
                owning_lob=owning_lob,
            )

            if actor.get("user_id"):
                await session.run(
                    """
                    MERGE (u:User {user_id: $user_id})
                    SET u.title = coalesce($title, u.title),
                        u.lob = coalesce($lob, u.lob)
                    WITH u
                    MATCH (e:SecurityEvent {event_id: $event_id})
                    MERGE (u)-[:ACTED_AS]->(e)
                    """,
                    user_id=actor["user_id"],
                    title=actor.get("title"),
                    lob=actor.get("lob"),
                    event_id=document.event_id,
                )

            if document.instruction_id:
                await session.run(
                    """
                    MERGE (i:Instruction {instruction_id: $instruction_id})
                    WITH i
                    MATCH (e:SecurityEvent {event_id: $event_id})
                    MERGE (e)-[:TARGETS]->(i)
                    """,
                    instruction_id=document.instruction_id,
                    event_id=document.event_id,
                )

            if version_key and document.instruction_id:
                status = merged.get("status") or instruction.get("status")
                await session.run(
                    """
                    MERGE (i:Instruction {instruction_id: $instruction_id})
                    MERGE (v:InstructionVersion {version_key: $version_key})
                    SET v.instruction_id = $instruction_id,
                        v.version_number = $version_number,
                        v.status = $status,
                        v.instruction_type = $instruction_type,
                        v.wire_scope = $wire_scope,
                        v.owning_lob = $owning_lob,
                        v.currency = $currency,
                        v.creator_user_id = $creator_user_id,
                        v.approver_user_id = $approver_user_id,
                        v.rejector_user_id = $rejector_user_id
                    MERGE (i)-[:HAS_VERSION]->(v)
                    """,
                    instruction_id=document.instruction_id,
                    version_key=version_key,
                    version_number=version_number,
                    status=status,
                    instruction_type=instruction_type,
                    wire_scope=wire_scope,
                    owning_lob=owning_lob,
                    currency=currency,
                    creator_user_id=merged.get("creator_user_id"),
                    approver_user_id=merged.get("approver_user_id"),
                    rejector_user_id=merged.get("rejector_user_id"),
                )
                await session.run(
                    """
                    MATCH (i:Instruction {instruction_id: $instruction_id})-[r:CURRENT]->()
                    DELETE r
                    WITH i
                    MATCH (v:InstructionVersion {version_key: $version_key})
                    MERGE (i)-[:CURRENT]->(v)
                    """,
                    instruction_id=document.instruction_id,
                    version_key=version_key,
                )
                await session.run(
                    """
                    MATCH (e:SecurityEvent {event_id: $event_id})
                    MATCH (v:InstructionVersion {version_key: $version_key})
                    MERGE (e)-[:TARGETS_VERSION]->(v)
                    """,
                    event_id=document.event_id,
                    version_key=version_key,
                )

                if created_by.get("user_id"):
                    await session.run(
                        """
                        MERGE (u:User {user_id: $user_id})
                        SET u.title = coalesce($title, u.title)
                        WITH u
                        MATCH (v:InstructionVersion {version_key: $version_key})
                        MERGE (u)-[:CREATED]->(v)
                        """,
                        user_id=created_by["user_id"],
                        title=created_by.get("title"),
                        version_key=version_key,
                    )

                if approved_by.get("user_id"):
                    await session.run(
                        """
                        MERGE (u:User {user_id: $user_id})
                        SET u.title = coalesce($title, u.title),
                            u.lob = coalesce($lob, u.lob)
                        WITH u
                        MATCH (v:InstructionVersion {version_key: $version_key})
                        MERGE (u)-[:APPROVED]->(v)
                        """,
                        user_id=approved_by["user_id"],
                        title=approved_by.get("title"),
                        lob=approved_by.get("lob"),
                        version_key=version_key,
                    )

                if rejected_by.get("user_id"):
                    await session.run(
                        """
                        MERGE (u:User {user_id: $user_id})
                        SET u.title = coalesce($title, u.title),
                            u.lob = coalesce($lob, u.lob)
                        WITH u
                        MATCH (v:InstructionVersion {version_key: $version_key})
                        MERGE (u)-[:REJECTED]->(v)
                        """,
                        user_id=rejected_by["user_id"],
                        title=rejected_by.get("title"),
                        lob=rejected_by.get("lob"),
                        version_key=version_key,
                    )

                action = event_ctx.get("action")
                outcome = event_ctx.get("outcome")
                if actor.get("user_id") and action == "SUBMIT" and outcome == "success":
                    await session.run(
                        """
                        MATCH (u:User {user_id: $user_id})
                        MATCH (v:InstructionVersion {version_key: $version_key})
                        MERGE (u)-[:SUBMITTED]->(v)
                        """,
                        user_id=actor["user_id"],
                        version_key=version_key,
                    )

            if owning_lob:
                await session.run(
                    """
                    MATCH (e:SecurityEvent {event_id: $event_id})
                    MERGE (p:ProfitCenter {lob: $owning_lob})
                    MERGE (e)-[:INVOLVES_LOB]->(p)
                    """,
                    event_id=document.event_id,
                    owning_lob=owning_lob,
                )

    async def graph_stats(self) -> dict[str, int]:
        if self._driver is None:
            raise RuntimeError("Neo4j writer not connected")

        query = """
        MATCH (n)
        UNWIND labels(n) AS label
        RETURN label, count(*) AS count
        ORDER BY count DESC
        """
        stats: dict[str, int] = {}
        async with self._driver.session() as session:
            result = await session.run(query)
            async for record in result:
                stats[str(record["label"])] = int(record["count"])
        return stats

    async def search_events(
        self,
        *,
        text: str = "",
        action: str = "",
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        if self._driver is None:
            raise RuntimeError("Neo4j writer not connected")

        query = """
        MATCH (e:SecurityEvent)
        WHERE ($text = ''
               OR toLower(coalesce(e.message, '')) CONTAINS toLower($text)
               OR toLower(coalesce(e.action, '')) CONTAINS toLower($text))
          AND ($action = '' OR e.action = $action)
        RETURN e
        ORDER BY e.timestamp DESC
        LIMIT $limit
        """
        events: list[dict[str, Any]] = []
        async with self._driver.session() as session:
            result = await session.run(query, text=text, action=action, limit=limit)
            async for record in result:
                node = record["e"]
                events.append(dict(node))
        return events

    async def get_event_subgraph(self, event_id: str) -> dict[str, Any] | None:
        if self._driver is None:
            raise RuntimeError("Neo4j writer not connected")

        query = """
        MATCH (e:SecurityEvent {event_id: $event_id})
        OPTIONAL MATCH (actor:User)-[:ACTED_AS]->(e)
        OPTIONAL MATCH (e)-[:TARGETS]->(i:Instruction)
        OPTIONAL MATCH (e)-[:TARGETS_VERSION]->(v:InstructionVersion)
        OPTIONAL MATCH (e)-[:INVOLVES_LOB]->(p:ProfitCenter)
        OPTIONAL MATCH (creator:User)-[:CREATED]->(v)
        RETURN e,
               collect(DISTINCT actor) AS actors,
               i,
               v,
               p,
               collect(DISTINCT creator) AS creators
        """
        async with self._driver.session() as session:
            result = await session.run(query, event_id=event_id)
            record = await result.single()
            if record is None or record["e"] is None:
                return None

            return {
                "event": dict(record["e"]),
                "actors": [dict(node) for node in record["actors"] if node],
                "instruction": dict(record["i"]) if record["i"] else None,
                "version": dict(record["v"]) if record["v"] else None,
                "profit_center": dict(record["p"]) if record["p"] else None,
                "creators": [dict(node) for node in record["creators"] if node],
            }

    async def get_instruction_subgraph(self, instruction_id: str) -> dict[str, Any] | None:
        if self._driver is None:
            raise RuntimeError("Neo4j writer not connected")

        query = """
        MATCH (i:Instruction {instruction_id: $instruction_id})
        OPTIONAL MATCH (i)-[:HAS_VERSION]->(v:InstructionVersion)
        OPTIONAL MATCH (creator:User)-[:CREATED]->(v)
        OPTIONAL MATCH (e:SecurityEvent)-[:TARGETS]->(i)
        RETURN i,
               collect(DISTINCT v) AS versions,
               collect(DISTINCT creator) AS creators,
               collect(DISTINCT e) AS events
        """
        async with self._driver.session() as session:
            result = await session.run(query, instruction_id=instruction_id)
            record = await result.single()
            if record is None or record["i"] is None:
                return None

            return {
                "instruction": dict(record["i"]),
                "versions": [dict(node) for node in record["versions"] if node],
                "creators": [dict(node) for node in record["creators"] if node],
                "events": [dict(node) for node in record["events"] if node],
            }
