from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from neo4j import AsyncDriver, AsyncGraphDatabase

from etl.config import settings
from etl.enrichment import EnrichedSecurityEventDocument

logger = logging.getLogger(__name__)


def _roles_json(roles: list | None) -> str | None:
    if not roles:
        return None
    return json.dumps(roles)


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
        prev_version_key = (
            f"{document.instruction_id}:{version_number - 1}"
            if document.instruction_id and version_number and version_number > 1
            else None
        )

        owning_lob = merged.get("owning_lob") or resource.get("owning_lob")
        wire_scope = merged.get("wire_scope")
        instruction_type = merged.get("instruction_type")
        currency = merged.get("currency")
        action = event_ctx.get("action")
        outcome = event_ctx.get("outcome")

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
                        e.owning_lob       = $owning_lob
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
