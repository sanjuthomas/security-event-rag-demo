from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from neo4j import AsyncDriver, AsyncGraphDatabase

from etl.authorization_context import (
    authorization_fact_neo4j_params,
    authorization_neo4j_params,
)
from etl.config import settings
from etl.enrichment import EnrichedSecurityEventDocument

logger = logging.getLogger(__name__)


def _roles_json(roles: list | None) -> str | None:
    if not roles:
        return None
    return json.dumps(roles)


def _instruction_version_key(instruction_id: str, version_number: int) -> str:
    return f"{instruction_id}:{version_number}"


def _payment_version_key(payment_id: str, version_number: int) -> str:
    return f"{payment_id}:{version_number}"


def _payment_version_number(payload: dict[str, Any]) -> int:
    snap = payload.get("payment_snapshot") or {}
    if snap.get("version_number") is not None:
        return int(snap["version_number"])
    if payload.get("version_number") is not None:
        return int(payload["version_number"])
    lifecycle = snap.get("lifecycle_events") or payload.get("lifecycle_events") or []
    if lifecycle:
        return len(lifecycle)
    return 1


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
        await self._repair_graph_duplicates()
        async with self._driver.session() as session:
            for statement in statements:
                try:
                    await session.run(statement)
                except Exception as exc:
                    logger.warning("Neo4j schema statement failed: %s | %s", exc, statement[:120])
        self._schema_applied = True
        logger.info("applied %s Neo4j schema statement(s)", len(statements))

    async def _repair_graph_duplicates(self) -> None:
        """Normalize version keys and remove duplicate nodes before composite constraints."""
        if self._driver is None:
            return

        repairs = [
            """
            MATCH (v:InstructionVersion)
            WHERE v.version_key IS NULL
              AND v.instruction_id IS NOT NULL
              AND v.version_number IS NOT NULL
            MATCH (keeper:InstructionVersion {
                instruction_id: v.instruction_id,
                version_number: v.version_number
            })
            WHERE keeper.version_key IS NOT NULL AND keeper <> v
            DETACH DELETE v
            """,
            """
            MATCH (v:InstructionVersion)
            WHERE v.instruction_id IS NOT NULL AND v.version_number IS NOT NULL
            WITH v.instruction_id AS iid, v.version_number AS vn, collect(v) AS nodes
            WHERE size(nodes) > 1
            UNWIND nodes[1..] AS dup
            DETACH DELETE dup
            """,
            """
            MATCH (v:InstructionVersion)
            WHERE v.version_key IS NULL
              AND v.instruction_id IS NOT NULL
              AND v.version_number IS NOT NULL
            SET v.version_key = v.instruction_id + ':' + toString(v.version_number)
            """,
            """
            MATCH (i:Instruction)
            WITH i.instruction_id AS iid, collect(i) AS nodes
            WHERE size(nodes) > 1
            UNWIND nodes[1..] AS dup
            DETACH DELETE dup
            """,
            """
            MATCH (p:Payment) WHERE p.version_number IS NULL SET p.version_number = 1
            """,
            """
            MATCH (p:Payment)
            WHERE p.version_key IS NULL
              AND p.payment_id IS NOT NULL
              AND p.version_number IS NOT NULL
            MATCH (keeper:Payment {
                payment_id: p.payment_id,
                version_number: p.version_number
            })
            WHERE keeper.version_key IS NOT NULL AND keeper <> p
            DETACH DELETE p
            """,
            """
            MATCH (p:Payment)
            WHERE p.payment_id IS NOT NULL AND p.version_number IS NOT NULL
            WITH p.payment_id AS pid, p.version_number AS vn, collect(p) AS nodes
            WHERE size(nodes) > 1
            UNWIND nodes[1..] AS dup
            DETACH DELETE dup
            """,
            """
            MATCH (p:Payment)
            WHERE p.version_key IS NULL
              AND p.payment_id IS NOT NULL
              AND p.version_number IS NOT NULL
            SET p.version_key = p.payment_id + ':' + toString(p.version_number)
            """,
            """
            MATCH (p:Payment)
            WITH p.payment_id AS pid, collect(p) AS nodes
            WHERE size(nodes) > 1
            UNWIND nodes[1..] AS dup
            DETACH DELETE dup
            """,
        ]
        async with self._driver.session() as session:
            for query in repairs:
                try:
                    await session.run(query)
                except Exception as exc:
                    logger.warning("Neo4j graph repair step failed: %s", exc)

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
            _instruction_version_key(document.instruction_id, version_number)
            if document.instruction_id and version_number is not None
            else None
        )
        prev_version_key = (
            _instruction_version_key(document.instruction_id, version_number - 1)
            if document.instruction_id and version_number and version_number > 1
            else None
        )

        owning_lob = merged.get("owning_lob") or resource.get("owning_lob")
        wire_scope = merged.get("wire_scope")
        instruction_type = merged.get("instruction_type")
        currency = merged.get("currency")
        action = event_ctx.get("action")
        outcome = event_ctx.get("outcome")
        auth_params = authorization_neo4j_params(event)

        # All writes in a single transaction for atomicity
        async with self._driver.session() as session:
            tx = await session.begin_transaction()
            try:

                # --- SecurityEvent node ---
                await tx.run(
                    """
                    MERGE (e:SecurityEvent {event_id: $event_id})
                    SET e.timestamp        = $timestamp,
                        e.severity         = $severity,
                        e.message          = $message,
                        e.action           = $action,
                        e.outcome          = $outcome,
                        e.reason           = $reason,
                        e.event_type       = $event_type,
                        e.source_application = $source_application,
                        e.source_version   = $source_version,
                        e.wire_scope       = $wire_scope,
                        e.instruction_type = $instruction_type,
                        e.owning_lob       = $owning_lob,
                        e.authorization_summary = $authorization_summary,
                        e.authorization_decision = $authorization_decision,
                        e.authorization_basis = $authorization_basis,
                        e.authorization_violations = $authorization_violations
                    """,
                    event_id=document.event_id,
                    timestamp=event.get("timestamp"),
                    severity=event.get("severity"),
                    message=event.get("message"),
                    action=action,
                    outcome=outcome,
                    reason=event_ctx.get("reason"),
                    event_type=json.dumps(event.get("event_type") or []),
                    source_application=source.get("application"),
                    source_version=source.get("version"),
                    wire_scope=wire_scope,
                    instruction_type=instruction_type,
                    owning_lob=owning_lob,
                    **auth_params,
                )

                # --- Actor User node + ACTED_AS + BELONGS_TO ---
                if actor.get("user_id"):
                    await tx.run(
                        """
                        MERGE (u:User {user_id: $user_id})
                        SET u.given_name    = coalesce($given_name, u.given_name),
                            u.family_name   = coalesce($family_name, u.family_name),
                            u.display_name  = coalesce(
                                CASE WHEN $family_name IS NOT NULL AND $given_name IS NOT NULL
                                     THEN $family_name + ', ' + $given_name + ' (' + $user_id + ')'
                                     ELSE null END,
                                u.display_name),
                            u.title         = coalesce($title, u.title),
                            u.lob           = coalesce($lob, u.lob),
                            u.roles         = coalesce($roles, u.roles),
                            u.supervisor_id = coalesce($supervisor_id, u.supervisor_id)
                        WITH u
                        MATCH (e:SecurityEvent {event_id: $event_id})
                        MERGE (u)-[:ACTED_AS]->(e)
                        WITH u
                        WHERE u.lob IS NOT NULL
                        MERGE (p:ProfitCenter {lob: u.lob})
                        MERGE (u)-[:BELONGS_TO]->(p)
                        """,
                        user_id=actor["user_id"],
                        given_name=actor.get("given_name"),
                        family_name=actor.get("family_name"),
                        title=actor.get("title"),
                        lob=actor.get("lob"),
                        roles=_roles_json(actor.get("roles")),
                        supervisor_id=actor.get("supervisor_id"),
                        event_id=document.event_id,
                    )

                    # REPORTS_TO for actor
                    if actor.get("supervisor_id"):
                        await tx.run(
                            """
                            MERGE (u:User {user_id: $user_id})
                            MERGE (s:User {user_id: $supervisor_id})
                            MERGE (u)-[:REPORTS_TO]->(s)
                            """,
                            user_id=actor["user_id"],
                            supervisor_id=actor["supervisor_id"],
                        )

                # --- Instruction node + TARGETS ---
                if document.instruction_id:
                    await tx.run(
                        """
                        MERGE (i:Instruction {instruction_id: $instruction_id})
                        WITH i
                        MATCH (e:SecurityEvent {event_id: $event_id})
                        MERGE (e)-[:TARGETS]->(i)
                        """,
                        instruction_id=document.instruction_id,
                        event_id=document.event_id,
                    )

                # --- InstructionVersion node + relationships ---
                if version_key and document.instruction_id:
                    end_date = merged.get("end_date")
                    await tx.run(
                        """
                        MERGE (i:Instruction {instruction_id: $instruction_id})
                        MERGE (v:InstructionVersion {version_key: $version_key})
                        SET v.instruction_id      = $instruction_id,
                            v.version_number      = $version_number,
                            v.status              = $status,
                            v.instruction_type    = $instruction_type,
                            v.wire_scope          = $wire_scope,
                            v.owning_lob          = $owning_lob,
                            v.currency            = $currency,
                            v.effective_date      = $effective_date,
                            v.end_date            = $end_date,
                            v.usage_count         = $usage_count,
                            v.creator_user_id     = $creator_user_id,
                            v.approver_user_id    = $approver_user_id,
                            v.rejector_user_id    = $rejector_user_id,
                            v.creditor_name       = $creditor_name,
                            v.creditor_account_id = $creditor_account_id,
                            v.debtor_name         = $debtor_name,
                            v.debtor_account_id   = $debtor_account_id,
                            v.creditor_agent_bic  = $creditor_agent_bic,
                            v.is_expired          = CASE
                                WHEN $end_date IS NOT NULL AND datetime($end_date) < datetime()
                                THEN true ELSE false END
                        MERGE (i)-[:HAS_VERSION]->(v)
                        """,
                        instruction_id=document.instruction_id,
                        version_key=version_key,
                        version_number=version_number,
                        status=merged.get("status") or instruction.get("status"),
                        instruction_type=instruction_type,
                        wire_scope=wire_scope,
                        owning_lob=owning_lob,
                        currency=currency,
                        effective_date=merged.get("effective_date"),
                        end_date=end_date,
                        usage_count=merged.get("usage_count"),
                        creator_user_id=merged.get("creator_user_id"),
                        approver_user_id=merged.get("approver_user_id"),
                        rejector_user_id=merged.get("rejector_user_id"),
                        creditor_name=merged.get("creditor_name"),
                        creditor_account_id=merged.get("creditor_account_id"),
                        debtor_name=merged.get("debtor_name"),
                        debtor_account_id=merged.get("debtor_account_id"),
                        creditor_agent_bic=merged.get("creditor_agent_bic"),
                    )

                    # CURRENT — only advance if this version is newer
                    await tx.run(
                        """
                        MATCH (i:Instruction {instruction_id: $instruction_id})
                        MATCH (v:InstructionVersion {version_key: $version_key})
                        OPTIONAL MATCH (i)-[r:CURRENT]->(cur:InstructionVersion)
                        WITH i, v, r, cur,
                             coalesce(cur.version_number, -1) AS cur_num
                        WHERE $version_number > cur_num
                        DELETE r
                        WITH i, v
                        MERGE (i)-[:CURRENT]->(v)
                        """,
                        instruction_id=document.instruction_id,
                        version_key=version_key,
                        version_number=version_number,
                    )

                    # TARGETS_VERSION
                    await tx.run(
                        """
                        MATCH (e:SecurityEvent {event_id: $event_id})
                        MATCH (v:InstructionVersion {version_key: $version_key})
                        MERGE (e)-[:TARGETS_VERSION]->(v)
                        """,
                        event_id=document.event_id,
                        version_key=version_key,
                    )

                    # SUPERSEDES — link to previous version if it exists
                    if prev_version_key:
                        await tx.run(
                            """
                            MATCH (v:InstructionVersion {version_key: $version_key})
                            MATCH (prev:InstructionVersion {version_key: $prev_version_key})
                            MERGE (v)-[:SUPERSEDES]->(prev)
                            """,
                            version_key=version_key,
                            prev_version_key=prev_version_key,
                        )

                    # OWNED_BY — InstructionVersion → ProfitCenter
                    if owning_lob:
                        await tx.run(
                            """
                            MATCH (v:InstructionVersion {version_key: $version_key})
                            MERGE (p:ProfitCenter {lob: $owning_lob})
                            MERGE (v)-[:OWNED_BY]->(p)
                            """,
                            version_key=version_key,
                            owning_lob=owning_lob,
                        )

                    # CONFLICTS_WITH — versions sharing same creditor account + currency
                    if merged.get("creditor_account_id") and currency:
                        await tx.run(
                            """
                            MATCH (v:InstructionVersion {version_key: $version_key})
                            MATCH (other:InstructionVersion)
                            WHERE other.creditor_account_id = $creditor_account_id
                              AND other.currency = $currency
                              AND other.instruction_id <> $instruction_id
                              AND other.status IN ['STANDING', 'SINGLE_USE', 'PENDING']
                            MERGE (v)-[:CONFLICTS_WITH]->(other)
                            MERGE (other)-[:CONFLICTS_WITH]->(v)
                            """,
                            version_key=version_key,
                            creditor_account_id=merged["creditor_account_id"],
                            currency=currency,
                            instruction_id=document.instruction_id,
                        )

                    # Creator User + CREATED + BELONGS_TO
                    if created_by.get("user_id"):
                        await tx.run(
                            """
                            MERGE (u:User {user_id: $user_id})
                            SET u.given_name    = coalesce($given_name, u.given_name),
                                u.family_name   = coalesce($family_name, u.family_name),
                                u.display_name  = coalesce(
                                    CASE WHEN $family_name IS NOT NULL AND $given_name IS NOT NULL
                                         THEN $family_name + ', ' + $given_name + ' (' + $user_id + ')'
                                         ELSE null END,
                                    u.display_name),
                                u.title         = coalesce($title, u.title),
                                u.lob           = coalesce($lob, u.lob),
                                u.supervisor_id = coalesce($supervisor_id, u.supervisor_id)
                            WITH u
                            MATCH (v:InstructionVersion {version_key: $version_key})
                            MERGE (u)-[:CREATED]->(v)
                            WITH u
                            WHERE u.lob IS NOT NULL
                            MERGE (p:ProfitCenter {lob: u.lob})
                            MERGE (u)-[:BELONGS_TO]->(p)
                            """,
                            user_id=created_by["user_id"],
                            given_name=created_by.get("given_name"),
                            family_name=created_by.get("family_name"),
                            title=created_by.get("title"),
                            lob=created_by.get("lob"),
                            supervisor_id=created_by.get("supervisor_id"),
                            version_key=version_key,
                        )
                        if created_by.get("supervisor_id"):
                            await tx.run(
                                """
                                MERGE (u:User {user_id: $user_id})
                                MERGE (s:User {user_id: $supervisor_id})
                                MERGE (u)-[:REPORTS_TO]->(s)
                                """,
                                user_id=created_by["user_id"],
                                supervisor_id=created_by["supervisor_id"],
                            )

                    # Approver User + APPROVED + APPROVED_FOR + BELONGS_TO
                    if approved_by.get("user_id"):
                        await tx.run(
                            """
                            MERGE (u:User {user_id: $user_id})
                            SET u.given_name    = coalesce($given_name, u.given_name),
                                u.family_name   = coalesce($family_name, u.family_name),
                                u.display_name  = coalesce(
                                    CASE WHEN $family_name IS NOT NULL AND $given_name IS NOT NULL
                                         THEN $family_name + ', ' + $given_name + ' (' + $user_id + ')'
                                         ELSE null END,
                                    u.display_name),
                                u.title         = coalesce($title, u.title),
                                u.lob           = coalesce($lob, u.lob),
                                u.supervisor_id = coalesce($supervisor_id, u.supervisor_id)
                            WITH u
                            MATCH (v:InstructionVersion {version_key: $version_key})
                            MERGE (u)-[:APPROVED]->(v)
                            WITH u
                            WHERE u.lob IS NOT NULL
                            MERGE (p:ProfitCenter {lob: u.lob})
                            MERGE (u)-[:BELONGS_TO]->(p)
                            """,
                            user_id=approved_by["user_id"],
                            given_name=approved_by.get("given_name"),
                            family_name=approved_by.get("family_name"),
                            title=approved_by.get("title"),
                            lob=approved_by.get("lob"),
                            supervisor_id=approved_by.get("supervisor_id"),
                            version_key=version_key,
                        )
                        if approved_by.get("supervisor_id"):
                            await tx.run(
                                """
                                MERGE (u:User {user_id: $user_id})
                                MERGE (s:User {user_id: $supervisor_id})
                                MERGE (u)-[:REPORTS_TO]->(s)
                                """,
                                user_id=approved_by["user_id"],
                                supervisor_id=approved_by["supervisor_id"],
                            )
                        # APPROVED_FOR — approver → creator (cross-approval detection)
                        if created_by.get("user_id") and approved_by["user_id"] != created_by["user_id"]:
                            await tx.run(
                                """
                                MATCH (approver:User {user_id: $approver_id})
                                MATCH (creator:User {user_id: $creator_id})
                                MERGE (approver)-[:APPROVED_FOR]->(creator)
                                """,
                                approver_id=approved_by["user_id"],
                                creator_id=created_by["user_id"],
                            )

                    # Rejector User + REJECTED + BELONGS_TO
                    if rejected_by.get("user_id"):
                        await tx.run(
                            """
                            MERGE (u:User {user_id: $user_id})
                            SET u.given_name    = coalesce($given_name, u.given_name),
                                u.family_name   = coalesce($family_name, u.family_name),
                                u.display_name  = coalesce(
                                    CASE WHEN $family_name IS NOT NULL AND $given_name IS NOT NULL
                                         THEN $family_name + ', ' + $given_name + ' (' + $user_id + ')'
                                         ELSE null END,
                                    u.display_name),
                                u.title         = coalesce($title, u.title),
                                u.lob           = coalesce($lob, u.lob),
                                u.supervisor_id = coalesce($supervisor_id, u.supervisor_id)
                            WITH u
                            MATCH (v:InstructionVersion {version_key: $version_key})
                            MERGE (u)-[:REJECTED]->(v)
                            WITH u
                            WHERE u.lob IS NOT NULL
                            MERGE (p:ProfitCenter {lob: u.lob})
                            MERGE (u)-[:BELONGS_TO]->(p)
                            """,
                            user_id=rejected_by["user_id"],
                            given_name=rejected_by.get("given_name"),
                            family_name=rejected_by.get("family_name"),
                            title=rejected_by.get("title"),
                            lob=rejected_by.get("lob"),
                            supervisor_id=rejected_by.get("supervisor_id"),
                            version_key=version_key,
                        )
                        if rejected_by.get("supervisor_id"):
                            await tx.run(
                                """
                                MERGE (u:User {user_id: $user_id})
                                MERGE (s:User {user_id: $supervisor_id})
                                MERGE (u)-[:REPORTS_TO]->(s)
                                """,
                                user_id=rejected_by["user_id"],
                                supervisor_id=rejected_by["supervisor_id"],
                            )

                    # Submitter — SUBMITTED
                    if actor.get("user_id") and action == "SUBMIT" and outcome == "success":
                        await tx.run(
                            """
                            MATCH (u:User {user_id: $user_id})
                            MATCH (v:InstructionVersion {version_key: $version_key})
                            MERGE (u)-[:SUBMITTED]->(v)
                            """,
                            user_id=actor["user_id"],
                            version_key=version_key,
                        )

                # --- SecurityEvent → ProfitCenter (INVOLVES_LOB) ---
                if owning_lob:
                    await tx.run(
                        """
                        MATCH (e:SecurityEvent {event_id: $event_id})
                        MERGE (p:ProfitCenter {lob: $owning_lob})
                        MERGE (e)-[:INVOLVES_LOB]->(p)
                        """,
                        event_id=document.event_id,
                        owning_lob=owning_lob,
                    )

                await tx.commit()
            except Exception:
                await tx.rollback()
                raise

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
                events.append(dict(record["e"]))
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
        OPTIONAL MATCH (approver:User)-[:APPROVED]->(v)
        OPTIONAL MATCH (rejector:User)-[:REJECTED]->(v)
        OPTIONAL MATCH (submitter:User)-[:SUBMITTED]->(v)
        RETURN e,
               collect(DISTINCT actor)    AS actors,
               i,
               v,
               p,
               collect(DISTINCT creator)   AS creators,
               collect(DISTINCT approver)  AS approvers,
               collect(DISTINCT rejector)  AS rejectors,
               collect(DISTINCT submitter) AS submitters
        """
        async with self._driver.session() as session:
            result = await session.run(query, event_id=event_id)
            record = await result.single()
            if record is None or record["e"] is None:
                return None

            return {
                "event":      dict(record["e"]),
                "actors":     [dict(n) for n in record["actors"]    if n],
                "instruction": dict(record["i"]) if record["i"]     else None,
                "version":    dict(record["v"]) if record["v"]      else None,
                "profit_center": dict(record["p"]) if record["p"]   else None,
                "creators":   [dict(n) for n in record["creators"]  if n],
                "approvers":  [dict(n) for n in record["approvers"] if n],
                "rejectors":  [dict(n) for n in record["rejectors"] if n],
                "submitters": [dict(n) for n in record["submitters"] if n],
            }

    async def get_instruction_subgraph(self, instruction_id: str) -> dict[str, Any] | None:
        if self._driver is None:
            raise RuntimeError("Neo4j writer not connected")

        query = """
        MATCH (i:Instruction {instruction_id: $instruction_id})
        OPTIONAL MATCH (i)-[:HAS_VERSION]->(v:InstructionVersion)
        OPTIONAL MATCH (creator:User)-[:CREATED]->(v)
        OPTIONAL MATCH (approver:User)-[:APPROVED]->(v)
        OPTIONAL MATCH (rejector:User)-[:REJECTED]->(v)
        OPTIONAL MATCH (e:SecurityEvent)-[:TARGETS]->(i)
        RETURN i,
               collect(DISTINCT v)        AS versions,
               collect(DISTINCT creator)  AS creators,
               collect(DISTINCT approver) AS approvers,
               collect(DISTINCT rejector) AS rejectors,
               collect(DISTINCT e)        AS events
        """
        async with self._driver.session() as session:
            result = await session.run(query, instruction_id=instruction_id)
            record = await result.single()
            if record is None or record["i"] is None:
                return None

            return {
                "instruction": dict(record["i"]),
                "versions":  [dict(n) for n in record["versions"]  if n],
                "creators":  [dict(n) for n in record["creators"]  if n],
                "approvers": [dict(n) for n in record["approvers"] if n],
                "rejectors": [dict(n) for n in record["rejectors"] if n],
                "events":    [dict(n) for n in record["events"]    if n],
            }

    async def upsert_instruction_fact(self, fact: dict[str, Any]) -> None:
        """Upsert instruction state from an InstructionFact event (ssi-instructions topic).

        Maintains:
          • Instruction node (with is_expired flag from dates)
          • InstructionVersion node (with status, financial details, expiry)
          • CURRENT relationship on the latest version
          • Creator, approver, rejector User nodes + CREATED/APPROVED/REJECTED rels
          • LOB / ProfitCenter node + OWNED_BY, BELONGS_TO rels
          • Actor User node + mutation relationship
          • CONFLICTS_WITH and APPROVED_FOR cross-instruction rels
        """
        if self._driver is None:
            raise RuntimeError("Neo4j writer not connected")

        snap = fact.get("instruction_snapshot") or {}
        instruction_id = fact.get("instruction_id") or snap.get("instruction_id")
        if not instruction_id:
            return

        version_number = fact.get("version_number", 0)
        version_key = _instruction_version_key(instruction_id, version_number)
        action = fact.get("action", "")
        timestamp = fact.get("timestamp")

        owning_lob = snap.get("owning_lob") or ""
        status = snap.get("status") or ""
        instruction_type = snap.get("instruction_type") or ""
        wire_scope = snap.get("wire_scope") or ""
        currency = snap.get("currency") or ""
        effective_date = snap.get("effective_date") or ""
        end_date = snap.get("end_date") or ""

        creditor = snap.get("creditor") or {}
        creditor_account = snap.get("creditor_account") or {}
        creditor_agent_fi = (snap.get("creditor_agent") or {}).get("financial_institution") or {}
        debtor = snap.get("debtor") or {}
        debtor_account = snap.get("debtor_account") or {}
        debtor_agent_fi = (snap.get("debtor_agent") or {}).get("financial_institution") or {}

        created_by = snap.get("created_by") or {}
        approved_by = snap.get("approved_by") or {}
        rejected_by = snap.get("rejected_by") or {}

        creator_user_id = created_by.get("user_id")
        approver_user_id = approved_by.get("user_id")
        rejector_user_id = rejected_by.get("user_id")

        actor_user_id = fact.get("actor_user_id")
        auth_params = authorization_fact_neo4j_params(fact)

        query = """
        // ── Instruction root node ────────────────────────────────────────────────
        MERGE (i:Instruction {instruction_id: $instruction_id})
        SET   i.owning_lob       = $owning_lob,
              i.instruction_type = $instruction_type,
              i.wire_scope       = $wire_scope,
              i.currency         = $currency

        // ── InstructionVersion node (canonical key matches security-event ETL) ───
        MERGE (v:InstructionVersion {version_key: $version_key})
        SET   v.instruction_id     = $instruction_id,
              v.version_number     = $version_number,
              v.status             = $status,
              v.action             = $action,
              v.timestamp          = $timestamp,
              v.owning_lob         = $owning_lob,
              v.instruction_type   = $instruction_type,
              v.wire_scope         = $wire_scope,
              v.currency           = $currency,
              v.effective_date     = $effective_date,
              v.end_date           = $end_date,
              v.creditor_name      = $creditor_name,
              v.creditor_account   = $creditor_account,
              v.creditor_scheme    = $creditor_scheme,
              v.creditor_bic       = $creditor_bic,
              v.debtor_name        = $debtor_name,
              v.debtor_account     = $debtor_account,
              v.debtor_bic         = $debtor_bic,
              v.creator_user_id    = $creator_user_id,
              v.approver_user_id   = $approver_user_id,
              v.rejector_user_id   = $rejector_user_id,
              v.approved_at        = coalesce($approved_at, v.approved_at),
              v.authorization_summary = coalesce($authorization_summary, v.authorization_summary),
              v.authorization_basis   = coalesce($authorization_basis, v.authorization_basis),
              v.is_expired         = (
                  $end_date IS NOT NULL AND $end_date <> '' AND
                  date(substring($end_date, 0, 10)) < date()
              )
        MERGE (i)-[:HAS_VERSION]->(v)

        // ── Mark CURRENT version (only advance, never go back) ──────────────────
        WITH i, v
        OPTIONAL MATCH (i)-[:CURRENT]->(existing:InstructionVersion)
        WITH i, v, existing
        WHERE existing IS NULL OR v.version_number >= existing.version_number
        OPTIONAL MATCH (i)-[old:CURRENT]->(:InstructionVersion)
        DELETE old
        MERGE  (i)-[:CURRENT]->(v)

        // ── LOB / ProfitCenter ──────────────────────────────────────────────────
        WITH i, v
        MERGE (lob:ProfitCenter {name: $owning_lob})
        MERGE (i)-[:OWNED_BY]->(lob)
        MERGE (v)-[:BELONGS_TO]->(lob)

        // ── Creator user ─────────────────────────────────────────────────────────
        WITH i, v, lob
        FOREACH (_ IN CASE WHEN $creator_user_id IS NOT NULL THEN [1] ELSE [] END |
            MERGE (cu:User {user_id: $creator_user_id})
            SET   cu.given_name    = coalesce($creator_given_name,    cu.given_name),
                  cu.family_name   = coalesce($creator_family_name,   cu.family_name),
                  cu.display_name  = coalesce(
                      CASE WHEN $creator_family_name IS NOT NULL AND $creator_given_name IS NOT NULL
                           THEN $creator_family_name + ', ' + $creator_given_name + ' (' + $creator_user_id + ')'
                           ELSE null END,
                      cu.display_name),
                  cu.title         = coalesce($creator_title,   cu.title),
                  cu.lob           = coalesce($creator_lob,     cu.lob),
                  cu.roles         = coalesce($creator_roles,   cu.roles),
                  cu.supervisor_id = coalesce($creator_supervisor_id, cu.supervisor_id)
            MERGE (cu)-[:CREATED]->(v)
        )

        // ── Approver user ────────────────────────────────────────────────────────
        WITH i, v, lob
        FOREACH (_ IN CASE WHEN $approver_user_id IS NOT NULL THEN [1] ELSE [] END |
            MERGE (au:User {user_id: $approver_user_id})
            SET   au.given_name    = coalesce($approver_given_name,    au.given_name),
                  au.family_name   = coalesce($approver_family_name,   au.family_name),
                  au.display_name  = coalesce(
                      CASE WHEN $approver_family_name IS NOT NULL AND $approver_given_name IS NOT NULL
                           THEN $approver_family_name + ', ' + $approver_given_name + ' (' + $approver_user_id + ')'
                           ELSE null END,
                      au.display_name),
                  au.title         = coalesce($approver_title,   au.title),
                  au.lob           = coalesce($approver_lob,     au.lob),
                  au.roles         = coalesce($approver_roles,   au.roles),
                  au.supervisor_id = coalesce($approver_supervisor_id, au.supervisor_id)
            MERGE (au)-[:APPROVED]->(v)
            MERGE (au)-[:APPROVED_FOR]->(i)
        )

        // ── Rejector user ────────────────────────────────────────────────────────
        WITH i, v, lob
        FOREACH (_ IN CASE WHEN $rejector_user_id IS NOT NULL THEN [1] ELSE [] END |
            MERGE (ru:User {user_id: $rejector_user_id})
            SET   ru.given_name    = coalesce($rejector_given_name,    ru.given_name),
                  ru.family_name   = coalesce($rejector_family_name,   ru.family_name),
                  ru.display_name  = coalesce(
                      CASE WHEN $rejector_family_name IS NOT NULL AND $rejector_given_name IS NOT NULL
                           THEN $rejector_family_name + ', ' + $rejector_given_name + ' (' + $rejector_user_id + ')'
                           ELSE null END,
                      ru.display_name),
                  ru.title         = coalesce($rejector_title,   ru.title),
                  ru.lob           = coalesce($rejector_lob,     ru.lob),
                  ru.roles         = coalesce($rejector_roles,   ru.roles),
                  ru.supervisor_id = coalesce($rejector_supervisor_id, ru.supervisor_id)
            MERGE (ru)-[:REJECTED]->(v)
        )

        // ── Actor (the user who performed this mutation) ─────────────────────────
        WITH i, v, lob
        FOREACH (_ IN CASE WHEN $actor_user_id IS NOT NULL THEN [1] ELSE [] END |
            MERGE (actor:User {user_id: $actor_user_id})
            SET   actor.given_name    = coalesce($actor_given_name,    actor.given_name),
                  actor.family_name   = coalesce($actor_family_name,   actor.family_name),
                  actor.display_name  = coalesce(
                      CASE WHEN $actor_family_name IS NOT NULL AND $actor_given_name IS NOT NULL
                           THEN $actor_family_name + ', ' + $actor_given_name + ' (' + $actor_user_id + ')'
                           ELSE null END,
                      actor.display_name),
                  actor.title         = coalesce($actor_title,   actor.title),
                  actor.lob           = coalesce($actor_lob,     actor.lob),
                  actor.roles         = coalesce($actor_roles,   actor.roles),
                  actor.supervisor_id = coalesce($actor_supervisor_id, actor.supervisor_id)
            MERGE (actor)-[:MUTATED {action: $action, timestamp: $timestamp}]->(v)
        )
        """

        params: dict[str, Any] = {
            "instruction_id": instruction_id,
            "version_key": version_key,
            "version_number": version_number,
            "action": action,
            "timestamp": timestamp,
            "owning_lob": owning_lob,
            "status": status,
            "instruction_type": instruction_type,
            "wire_scope": wire_scope,
            "currency": currency,
            "effective_date": effective_date,
            "end_date": end_date,
            "creditor_name": creditor.get("name"),
            "creditor_account": creditor_account.get("identification"),
            "creditor_scheme": creditor_account.get("identification_scheme"),
            "creditor_bic": creditor_agent_fi.get("identification"),
            "debtor_name": debtor.get("name"),
            "debtor_account": debtor_account.get("identification"),
            "debtor_bic": debtor_agent_fi.get("identification"),
            "creator_user_id": creator_user_id,
            "creator_given_name": created_by.get("given_name"),
            "creator_family_name": created_by.get("family_name"),
            "creator_title": created_by.get("title"),
            "creator_lob": created_by.get("lob"),
            "creator_roles": _roles_json(created_by.get("roles")),
            "creator_supervisor_id": created_by.get("supervisor_id"),
            "approver_user_id": approver_user_id,
            "approver_given_name": approved_by.get("given_name"),
            "approver_family_name": approved_by.get("family_name"),
            "approver_title": approved_by.get("title"),
            "approver_lob": approved_by.get("lob"),
            "approver_roles": _roles_json(approved_by.get("roles")),
            "approver_supervisor_id": approved_by.get("supervisor_id"),
            "rejector_user_id": rejector_user_id,
            "rejector_given_name": rejected_by.get("given_name"),
            "rejector_family_name": rejected_by.get("family_name"),
            "rejector_title": rejected_by.get("title"),
            "rejector_lob": rejected_by.get("lob"),
            "rejector_roles": _roles_json(rejected_by.get("roles")),
            "rejector_supervisor_id": rejected_by.get("supervisor_id"),
            "actor_user_id": actor_user_id,
            "actor_given_name": fact.get("actor_given_name"),
            "actor_family_name": fact.get("actor_family_name"),
            "actor_title": fact.get("actor_title"),
            "actor_lob": fact.get("actor_lob"),
            "actor_roles": _roles_json(fact.get("actor_roles")),
            "actor_supervisor_id": fact.get("actor_supervisor_id"),
            **auth_params,
        }

        async with self._driver.session() as session:
            await session.run(query, **params)

        # ── CONFLICTS_WITH: same creditor_account + currency across instructions ──
        if creditor_account.get("identification") and currency:
            conflict_query = """
            MATCH (v1:InstructionVersion {
                creditor_account: $creditor_account,
                currency:         $currency
            })
            WHERE v1.instruction_id <> $instruction_id
            MATCH (i1:Instruction {instruction_id: $instruction_id})-[:CURRENT]->(cv1:InstructionVersion)
            MATCH (i2:Instruction)-[:CURRENT]->(v1)
            WHERE i2.instruction_id <> $instruction_id
              AND cv1.status IN ['STANDING', 'SUBMITTED', 'PENDING_APPROVAL']
              AND v1.status  IN ['STANDING', 'SUBMITTED', 'PENDING_APPROVAL']
            MERGE (i1)-[:CONFLICTS_WITH]->(i2)
            MERGE (i2)-[:CONFLICTS_WITH]->(i1)
            """
            async with self._driver.session() as session:
                await session.run(
                    conflict_query,
                    creditor_account=creditor_account["identification"],
                    currency=currency,
                    instruction_id=instruction_id,
                )

        logger.debug(
            "upserted instruction fact instruction_id=%s action=%s version=%s",
            instruction_id,
            action,
            version_number,
        )

    async def upsert_payment_security_event(self, event: dict[str, Any]) -> None:
        """Write a PaymentSecurityEvent into Neo4j.

        Creates/merges:
          - SecurityEvent node (with payment_id property)
          - Payment node (stub — full data comes from ssi-payments)
          - User actor node + ACTED_AS relationship
          - (SecurityEvent)-[:TARGETS_PAYMENT]->(Payment)
          - (SecurityEvent)-[:INVOLVES_LOB]->(ProfitCenter)
        """
        if self._driver is None:
            raise RuntimeError("Neo4j writer not connected")

        actor = event.get("actor") or {}
        resource = event.get("resource") or {}
        event_ctx = event.get("event") or {}
        source = event.get("source") or {}

        event_id = event.get("event_id", "")
        payment_id = resource.get("id", "")
        instruction_id = resource.get("instruction_id", "")
        owning_lob = resource.get("owning_lob", "")
        payment_version = _payment_version_number(event)
        payment_version_key = _payment_version_key(payment_id, payment_version)
        auth_params = authorization_neo4j_params(event)

        async with self._driver.session() as session:
            tx = await session.begin_transaction()
            try:
                # SecurityEvent node
                await tx.run(
                    """
                    MERGE (e:SecurityEvent {event_id: $event_id})
                    SET e.timestamp        = $timestamp,
                        e.severity         = $severity,
                        e.message          = $message,
                        e.action           = $action,
                        e.outcome          = $outcome,
                        e.reason           = $reason,
                        e.payment_id       = $payment_id,
                        e.source_application = $source_application,
                        e.source_version   = $source_version,
                        e.owning_lob       = $owning_lob,
                        e.authorization_summary = $authorization_summary,
                        e.authorization_decision = $authorization_decision,
                        e.authorization_basis = $authorization_basis,
                        e.authorization_violations = $authorization_violations
                    """,
                    event_id=event_id,
                    timestamp=event.get("timestamp"),
                    severity=event.get("severity"),
                    message=event.get("message"),
                    action=event_ctx.get("action"),
                    outcome=event_ctx.get("outcome"),
                    reason=event_ctx.get("reason"),
                    payment_id=payment_id,
                    source_application=source.get("application"),
                    source_version=source.get("version"),
                    owning_lob=owning_lob,
                    **auth_params,
                )

                # Actor + ACTED_AS
                if actor.get("user_id"):
                    await tx.run(
                        """
                        MERGE (u:User {user_id: $user_id})
                        SET u.given_name    = coalesce($given_name, u.given_name),
                            u.family_name   = coalesce($family_name, u.family_name),
                            u.display_name  = coalesce(
                                CASE WHEN $family_name IS NOT NULL AND $given_name IS NOT NULL
                                     THEN $family_name + ', ' + $given_name + ' (' + $user_id + ')'
                                     ELSE null END,
                                u.display_name),
                            u.title         = coalesce($title, u.title),
                            u.lob           = coalesce($lob, u.lob),
                            u.roles         = coalesce($roles, u.roles),
                            u.supervisor_id = coalesce($supervisor_id, u.supervisor_id)
                        WITH u
                        MATCH (e:SecurityEvent {event_id: $event_id})
                        MERGE (u)-[:ACTED_AS]->(e)
                        """,
                        user_id=actor["user_id"],
                        given_name=actor.get("given_name"),
                        family_name=actor.get("family_name"),
                        title=actor.get("title"),
                        lob=actor.get("lob"),
                        roles=_roles_json(actor.get("roles")),
                        supervisor_id=actor.get("supervisor_id"),
                        event_id=event_id,
                    )
                    if actor.get("supervisor_id"):
                        await tx.run(
                            """
                            MERGE (u:User {user_id: $user_id})
                            MERGE (s:User {user_id: $supervisor_id})
                            MERGE (u)-[:REPORTS_TO]->(s)
                            """,
                            user_id=actor["user_id"],
                            supervisor_id=actor["supervisor_id"],
                        )

                # Payment stub + TARGETS_PAYMENT
                if payment_id:
                    await tx.run(
                        """
                        MERGE (p:Payment {payment_id: $payment_id})
                        SET p.version_number = $version_number,
                            p.version_key    = $version_key,
                            p.instruction_id = coalesce($instruction_id, p.instruction_id),
                            p.owning_lob     = coalesce($owning_lob, p.owning_lob)
                        WITH p
                        MATCH (e:SecurityEvent {event_id: $event_id})
                        MERGE (e)-[:TARGETS_PAYMENT]->(p)
                        """,
                        payment_id=payment_id,
                        version_number=payment_version,
                        version_key=payment_version_key,
                        instruction_id=instruction_id,
                        owning_lob=owning_lob,
                        event_id=event_id,
                    )

                # INVOLVES_LOB
                if owning_lob:
                    await tx.run(
                        """
                        MATCH (e:SecurityEvent {event_id: $event_id})
                        MERGE (pc:ProfitCenter {lob: $owning_lob})
                        MERGE (e)-[:INVOLVES_LOB]->(pc)
                        """,
                        event_id=event_id,
                        owning_lob=owning_lob,
                    )

                await tx.commit()
            except Exception:
                await tx.rollback()
                raise

    async def upsert_payment_fact(self, fact: dict[str, Any]) -> None:
        """Write a Payment fact (from ssi-payments topic) into Neo4j.

        Creates/merges:
          - Payment node with all fields
          - (Instruction)-[:HAS_PAYMENT]->(Payment)
          - (User creator)-[:CREATED_PAYMENT]->(Payment)
          - (User approver)-[:APPROVED_PAYMENT]->(Payment)   when present
          - (User rejector)-[:REJECTED_PAYMENT]->(Payment)   when present
        """
        if self._driver is None:
            raise RuntimeError("Neo4j writer not connected")

        payment_id = fact.get("payment_id", "")
        if not payment_id:
            return

        instruction_id = fact.get("instruction_id", "")
        created_by = fact.get("created_by") or {}
        approved_by = fact.get("approved_by") or {}
        rejected_by = fact.get("rejected_by") or {}
        payment_version = _payment_version_number(fact)
        payment_version_key = _payment_version_key(payment_id, payment_version)

        async with self._driver.session() as session:
            tx = await session.begin_transaction()
            try:
                # Payment node
                await tx.run(
                    """
                    MERGE (p:Payment {payment_id: $payment_id})
                    SET p.version_number   = $version_number,
                        p.version_key      = $version_key,
                        p.instruction_id   = $instruction_id,
                        p.status           = $status,
                        p.amount           = $amount,
                        p.currency         = $currency,
                        p.value_date       = $value_date,
                        p.owning_lob       = $owning_lob,
                        p.instruction_type = $instruction_type,
                        p.creator_user_id  = $creator_user_id,
                        p.approver_user_id = $approver_user_id,
                        p.rejector_user_id = $rejector_user_id,
                        p.created_at       = $created_at,
                        p.updated_at       = $updated_at
                    """,
                    payment_id=payment_id,
                    version_number=payment_version,
                    version_key=payment_version_key,
                    instruction_id=instruction_id,
                    status=fact.get("status"),
                    amount=fact.get("amount"),
                    currency=fact.get("currency"),
                    value_date=fact.get("value_date"),
                    owning_lob=fact.get("owning_lob"),
                    instruction_type=fact.get("instruction_type"),
                    creator_user_id=created_by.get("user_id"),
                    approver_user_id=approved_by.get("user_id") if approved_by else None,
                    rejector_user_id=rejected_by.get("user_id") if rejected_by else None,
                    created_at=fact.get("created_at"),
                    updated_at=fact.get("updated_at"),
                )

                # HAS_PAYMENT — Instruction → Payment
                if instruction_id:
                    await tx.run(
                        """
                        MERGE (i:Instruction {instruction_id: $instruction_id})
                        WITH i
                        MATCH (p:Payment {payment_id: $payment_id})
                        MERGE (i)-[:HAS_PAYMENT]->(p)
                        """,
                        instruction_id=instruction_id,
                        payment_id=payment_id,
                    )

                # Creator User + CREATED_PAYMENT
                if created_by.get("user_id"):
                    await tx.run(
                        """
                        MERGE (u:User {user_id: $user_id})
                        SET u.given_name    = coalesce($given_name, u.given_name),
                            u.family_name   = coalesce($family_name, u.family_name),
                            u.display_name  = coalesce(
                                CASE WHEN $family_name IS NOT NULL AND $given_name IS NOT NULL
                                     THEN $family_name + ', ' + $given_name + ' (' + $user_id + ')'
                                     ELSE null END,
                                u.display_name),
                            u.title         = coalesce($title, u.title),
                            u.lob           = coalesce($lob, u.lob),
                            u.supervisor_id = coalesce($supervisor_id, u.supervisor_id)
                        WITH u
                        MATCH (p:Payment {payment_id: $payment_id})
                        MERGE (u)-[:CREATED_PAYMENT]->(p)
                        """,
                        user_id=created_by["user_id"],
                        given_name=created_by.get("given_name"),
                        family_name=created_by.get("family_name"),
                        title=created_by.get("title"),
                        lob=created_by.get("lob"),
                        supervisor_id=created_by.get("supervisor_id"),
                        payment_id=payment_id,
                    )
                    if created_by.get("supervisor_id"):
                        await tx.run(
                            """
                            MERGE (u:User {user_id: $user_id})
                            MERGE (s:User {user_id: $supervisor_id})
                            MERGE (u)-[:REPORTS_TO]->(s)
                            """,
                            user_id=created_by["user_id"],
                            supervisor_id=created_by["supervisor_id"],
                        )

                # Approver User + APPROVED_PAYMENT
                if approved_by.get("user_id"):
                    await tx.run(
                        """
                        MERGE (u:User {user_id: $user_id})
                        SET u.given_name    = coalesce($given_name, u.given_name),
                            u.family_name   = coalesce($family_name, u.family_name),
                            u.display_name  = coalesce(
                                CASE WHEN $family_name IS NOT NULL AND $given_name IS NOT NULL
                                     THEN $family_name + ', ' + $given_name + ' (' + $user_id + ')'
                                     ELSE null END,
                                u.display_name),
                            u.title         = coalesce($title, u.title),
                            u.lob           = coalesce($lob, u.lob),
                            u.supervisor_id = coalesce($supervisor_id, u.supervisor_id)
                        WITH u
                        MATCH (p:Payment {payment_id: $payment_id})
                        MERGE (u)-[:APPROVED_PAYMENT]->(p)
                        """,
                        user_id=approved_by["user_id"],
                        given_name=approved_by.get("given_name"),
                        family_name=approved_by.get("family_name"),
                        title=approved_by.get("title"),
                        lob=approved_by.get("lob"),
                        supervisor_id=approved_by.get("supervisor_id"),
                        payment_id=payment_id,
                    )
                    if approved_by.get("supervisor_id"):
                        await tx.run(
                            """
                            MERGE (u:User {user_id: $user_id})
                            MERGE (s:User {user_id: $supervisor_id})
                            MERGE (u)-[:REPORTS_TO]->(s)
                            """,
                            user_id=approved_by["user_id"],
                            supervisor_id=approved_by["supervisor_id"],
                        )

                # Rejector User + REJECTED_PAYMENT
                if rejected_by.get("user_id"):
                    await tx.run(
                        """
                        MERGE (u:User {user_id: $user_id})
                        SET u.given_name    = coalesce($given_name, u.given_name),
                            u.family_name   = coalesce($family_name, u.family_name),
                            u.display_name  = coalesce(
                                CASE WHEN $family_name IS NOT NULL AND $given_name IS NOT NULL
                                     THEN $family_name + ', ' + $given_name + ' (' + $user_id + ')'
                                     ELSE null END,
                                u.display_name),
                            u.title         = coalesce($title, u.title),
                            u.lob           = coalesce($lob, u.lob),
                            u.supervisor_id = coalesce($supervisor_id, u.supervisor_id)
                        WITH u
                        MATCH (p:Payment {payment_id: $payment_id})
                        MERGE (u)-[:REJECTED_PAYMENT]->(p)
                        """,
                        user_id=rejected_by["user_id"],
                        given_name=rejected_by.get("given_name"),
                        family_name=rejected_by.get("family_name"),
                        title=rejected_by.get("title"),
                        lob=rejected_by.get("lob"),
                        supervisor_id=rejected_by.get("supervisor_id"),
                        payment_id=payment_id,
                    )

                await tx.commit()
            except Exception:
                await tx.rollback()
                raise

        logger.debug(
            "upserted payment fact payment_id=%s status=%s",
            payment_id,
            fact.get("status"),
        )

    async def run_read_cypher(self, cypher: str) -> list[dict[str, Any]]:
        """Execute a validated read-only Cypher query and return rows as plain dicts.

        The caller is responsible for pre-validating the query with
        ``validate_read_only_cypher`` before passing it here.  This method opens
        a read-access session so the database layer also enforces read-only mode.
        """
        if self._driver is None:
            raise RuntimeError("Neo4j writer not connected")

        rows: list[dict[str, Any]] = []
        async with self._driver.session(default_access_mode="READ") as session:
            result = await session.run(cypher)
            async for record in result:
                row: dict[str, Any] = {}
                for key in record.keys():
                    value = record[key]
                    if hasattr(value, "items"):
                        row[key] = dict(value.items())
                    elif isinstance(value, list):
                        row[key] = [
                            dict(item.items()) if hasattr(item, "items") else item
                            for item in value
                        ]
                    else:
                        row[key] = value
                rows.append(row)
        return rows
