from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any

from chat_application.authorization_client import (
    EligibilityClient,
    EligibilityClientError,
    format_eligible_approvers_answer,
    format_instruction_eligible_approvers_answer,
)
from chat_application.config import settings
from chat_application.cypher import (
    extract_entity_ids,
    extract_uuids,
    instruction_id_from_list_payments_question,
    is_alert_ranking_question,
    is_max_payments_per_instruction_question,
    is_payments_for_instruction_question,
    load_graph_schema,
    normalize_read_only_cypher,
    plan_graph_queries,
    ranking_period_label,
    validate_read_only_cypher,
)
from chat_application.eligibility import eligible_approver_target
from chat_application.formatting import format_markdown_table
from chat_application.models import ChatMessage, ChatResponse, SearchMode, SourceHit
from chat_application.neo4j import Neo4jClient
from chat_application.ollama import OllamaClient
from chat_application.qdrant import QdrantSearchClient
from chat_application.reranker import RankedHit, graph_rows_to_hits, rrf_merge

logger = logging.getLogger(__name__)

_AMOUNT_IN_BASIS = re.compile(
    r"amount\s+([\d.eE+-]+)\s+(within subject and absolute limits)",
    re.IGNORECASE,
)


def _format_usd_amount(amount: float) -> str:
    """Format a USD amount for compliance-facing policy text."""
    abs_amount = abs(amount)
    if abs_amount >= 1_000_000_000:
        value = abs_amount / 1_000_000_000
        if value.is_integer():
            return f"${int(value):,} billion"
        trimmed = f"{value:.1f}".rstrip("0").rstrip(".")
        return f"${trimmed} billion"
    if abs_amount >= 1_000_000:
        value = abs_amount / 1_000_000
        if value.is_integer():
            return f"${int(value):,} million"
        trimmed = f"{value:.1f}".rstrip("0").rstrip(".")
        return f"${trimmed} million"
    if abs_amount >= 1_000:
        return f"${abs_amount:,.0f}"
    if abs_amount.is_integer():
        return f"${int(abs_amount)}"
    return f"${abs_amount:,.2f}"


def _humanize_policy_basis_point(point: str) -> str:
    def _replace(match: re.Match[str]) -> str:
        try:
            amount = float(match.group(1))
        except ValueError:
            return match.group(0)
        return f"amount {_format_usd_amount(amount)} {match.group(2)}"

    return _AMOUNT_IN_BASIS.sub(_replace, point)


def _humanize_policy_basis(basis: list[str]) -> list[str]:
    return [_humanize_policy_basis_point(point) for point in basis]


def _format_basis_join(basis: list[str] | None) -> str:
    if not basis:
        return ""
    return " | ".join(_humanize_policy_basis(basis))


def _humanize_authorization_text(text: str) -> str:
    if not text:
        return text
    return _AMOUNT_IN_BASIS.sub(
        lambda match: _humanize_policy_basis_point(match.group(0)),
        text,
    )


def _parse_authorization_basis(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if item]
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return [str(item) for item in parsed if item]
        except json.JSONDecodeError:
            pass
    return []


def _append_policy_basis(why: str, basis: list[str]) -> str:
    if not basis:
        return why
    readable = _humanize_policy_basis(basis)
    table_rows = [[index, point] for index, point in enumerate(readable, start=1)]
    table = format_markdown_table(["#", "Policy check"], table_rows)
    return f"{why.rstrip()}\n\nPolicy basis ({len(readable)} checks):\n\n{table}"


def _is_instruction_approval_question(message: str, mode: SearchMode) -> bool:
    q = message.lower()
    if "approv" not in q:
        return False
    if "payment" in q and "instruction" not in q:
        return False
    return mode == "instructions" or "instruction" in q


def _is_payment_approval_question(message: str, mode: SearchMode) -> bool:
    q = message.lower()
    if "approv" not in q:
        return False
    if "instruction" in q and "payment" not in q:
        return False
    return mode == "payments" or "payment" in q


def _dedupe_payment_graph_rows(graph_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen_payment_ids: set[str] = set()
    for row in graph_rows:
        payment_id = row.get("payment_id")
        if not payment_id or payment_id in seen_payment_ids:
            continue
        seen_payment_ids.add(payment_id)
        deduped.append(row)
    return deduped


def _format_max_payments_per_instruction_answer(
    graph_rows: list[dict[str, Any]],
) -> str | None:
    if not graph_rows:
        return "No instruction payment counts were found in the graph."

    instruction_id = graph_rows[0].get("instruction_id")
    if not instruction_id:
        return None

    payment_rows = _dedupe_payment_graph_rows(graph_rows)
    table_rows = [
        [
            row.get("payment_id"),
            row.get("created_at") or "—",
            row.get("creator_display") or "—",
            row.get("approver_display") or "—",
        ]
        for row in payment_rows
    ]

    lines = [
        f"Instruction: {instruction_id}",
        f"Total payments: {len(table_rows)}",
        "",
    ]
    if table_rows:
        lines.append(
            format_markdown_table(
                ["Payment ID", "Created At", "Creator", "Approver"],
                table_rows,
            )
        )
    else:
        lines.append("_No payments found._")
    return "\n".join(lines)


def _format_payment_amount_display(amount: Any, currency: Any) -> str:
    if amount is None:
        return "N/A"
    try:
        value = float(amount)
    except (TypeError, ValueError):
        return str(amount)
    formatted = f"{value:,.2f}"
    if currency:
        return f"{formatted} {currency}"
    return formatted


def _format_payments_for_instruction_answer(
    instruction_id: str,
    graph_rows: list[dict[str, Any]],
) -> str:
    payment_rows = _dedupe_payment_graph_rows(graph_rows)
    table_rows = [
        [
            row.get("payment_id"),
            row.get("status") or "N/A",
            _format_payment_amount_display(row.get("amount"), row.get("currency")),
            row.get("value_date") or "N/A",
            row.get("owning_lob") or "N/A",
            row.get("creator_display") or "N/A",
            row.get("approver_display") or "N/A",
        ]
        for row in payment_rows
    ]

    summary = f"There are {len(table_rows)} payments in total for instruction {instruction_id}."
    if not table_rows:
        return f"{summary}\n\n_No payments found._"

    return (
        f"{summary}\n\n"
        f"{format_markdown_table(['Payment ID', 'Status', 'Amount', 'Value Date', 'LOB', 'Creator', 'Approver'], table_rows)}"
    )


def _extract_alert_ranking_rows(graph_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        row
        for row in graph_rows
        if "alert_count" in row and "actor_display" in row
    ]


def _alert_ranking_domain_label(message: str) -> str:
    q = message.lower()
    if "payment" in q and "instruction" not in q:
        return "payment policy denial alerts"
    if "instruction" in q and "payment" not in q:
        return "instruction policy denial alerts"
    return "policy denial alerts"


def _format_alert_ranking_answer(message: str, graph_rows: list[dict[str, Any]]) -> str:
    ranking_rows = _extract_alert_ranking_rows(graph_rows)
    if not ranking_rows:
        return "No policy denial alert rankings were found in the graph."

    period = ranking_period_label(message)
    domain = _alert_ranking_domain_label(message)
    table_rows = [
        [
            row.get("actor_display") or "—",
            row.get("user_id") or "—",
            row.get("alert_count", 0),
            row.get("payment_alerts", 0),
            row.get("instruction_alerts", 0),
        ]
        for row in ranking_rows
    ]

    if len(ranking_rows) == 1:
        top = ranking_rows[0]
        summary = (
            f"The user with the most {domain} ({period}) is "
            f"{top.get('actor_display')} with {top.get('alert_count')} alert(s)."
        )
    else:
        summary = f"User ranking by {domain} ({period}): {len(table_rows)} user(s)."

    return (
        f"{summary}\n\n"
        f"{format_markdown_table(['User', 'User ID', 'Total Alerts', 'Payment Alerts', 'Instruction Alerts'], table_rows)}"
    )


def _should_lookup_payment_ids(message: str, uuids: list[str], mode: SearchMode) -> bool:
    if not uuids:
        return False
    if is_payments_for_instruction_question(message):
        return False
    if mode == "payments":
        return True
    return "payment" in message.lower()


def _display_from_snap_user(snap: dict[str, Any], field: str) -> str:
    user = snap.get(field) or {}
    family_name = user.get("family_name")
    given_name = user.get("given_name")
    user_id = user.get("user_id") or ""
    if family_name and given_name:
        return f"{family_name}, {given_name} ({user_id})"
    return user_id


def _instruction_lifecycle_party_lines(payload: dict[str, Any], snap: dict[str, Any]) -> str:
    status = (snap.get("status") or payload.get("status") or "").upper()
    lines: list[str] = []

    if status == "REJECTED":
        rejected_by = payload.get("rejector_display") or _display_from_snap_user(snap, "rejected_by")
        if rejected_by:
            lines.append(f"rejected_by={rejected_by}")
        rejected_at = payload.get("rejected_at") or snap.get("rejected_at")
        if rejected_at:
            lines.append(f"rejected_at={rejected_at}")
        reason = payload.get("rejection_reason") or snap.get("rejection_reason")
        if reason:
            lines.append(f"rejection_reason={reason}")
    elif status in ("STANDING", "SINGLE_USE", "USED", "SUSPENDED"):
        approver = payload.get("approver_display") or _display_from_snap_user(snap, "approved_by")
        if approver:
            lines.append(f"approver={approver}")
        approved_at = payload.get("approved_at") or snap.get("approved_at")
        if approved_at:
            lines.append(f"approved_at={approved_at}")

    return "\n  ".join(lines)


class RagService:
    def __init__(
        self,
        *,
        ollama: OllamaClient,
        qdrant: QdrantSearchClient,
        neo4j: Neo4jClient,
    ) -> None:
        self.ollama = ollama
        self.qdrant = qdrant
        self.neo4j = neo4j
        self._schema = load_graph_schema()
        self._eligibility = EligibilityClient()

    async def ask(
        self,
        message: str,
        history: list[ChatMessage],
        *,
        mode: SearchMode = "events",
        bearer_token: str | None = None,
        session_id: str | None = None,
    ) -> ChatResponse:
        """Run the full RAG pipeline.

        mode="events"       — instruction + payment security events in Qdrant
        mode="instructions" — instruction_state points only
        mode="payments"     — payment_fact points only
        mode="all"          — all points (no source filter)
        """
        started = time.perf_counter()

        eligibility_target = eligible_approver_target(message, mode=mode)
        if eligibility_target == "payment":
            eligibility_answer = await self._answer_payment_eligible_approvers(
                message,
                bearer_token=bearer_token,
                session_id=session_id,
            )
            if eligibility_answer is not None:
                elapsed = (time.perf_counter() - started) * 1000
                return ChatResponse(
                    answer=eligibility_answer,
                    sources=[],
                    cypher=None,
                    graph_rows=[],
                    retrieval_ms=0.0,
                    generation_ms=round(elapsed, 1),
                )
        elif eligibility_target == "instruction":
            eligibility_answer = await self._answer_instruction_eligible_approvers(
                message,
                bearer_token=bearer_token,
                session_id=session_id,
            )
            if eligibility_answer is not None:
                elapsed = (time.perf_counter() - started) * 1000
                return ChatResponse(
                    answer=eligibility_answer,
                    sources=[],
                    cypher=None,
                    graph_rows=[],
                    retrieval_ms=0.0,
                    generation_ms=round(elapsed, 1),
                )

        limit = settings.retrieval_limit

        # Map mode to Qdrant source filter tag
        qdrant_source: str | None
        if mode == "events":
            qdrant_source = "security_events"
        elif mode == "instructions":
            qdrant_source = "instruction_state"
        elif mode == "payments":
            qdrant_source = "payment"
        else:
            qdrant_source = None  # no filter — search everything

        event_ids = extract_uuids(message)
        entity_ids = extract_entity_ids(message)

        vector_task = asyncio.create_task(
            self._search_vector(message, limit, source=qdrant_source)
        )
        bm25_task = asyncio.create_task(
            asyncio.to_thread(self._search_bm25, message, limit, qdrant_source)
        )
        cypher_task = asyncio.create_task(self._search_graph(message, mode=mode))
        exact_task = (
            asyncio.create_task(self._lookup_exact_event_ids(event_ids))
            if event_ids and mode != "instructions"
            else None
        )
        instruction_exact_task = (
            asyncio.create_task(self._lookup_exact_instruction_ids(entity_ids, message))
            if entity_ids and mode == "instructions"
            else None
        )
        payment_exact_task = (
            asyncio.create_task(self._lookup_exact_payment_ids(entity_ids, message))
            if entity_ids and _should_lookup_payment_ids(message, entity_ids, mode)
            else None
        )

        vector_hits, bm25_hits, graph_result = await asyncio.gather(
            vector_task, bm25_task, cypher_task
        )

        exact_hits: list[dict[str, Any]] = []
        exact_graph_rows: list[dict[str, Any]] = []
        if exact_task is not None:
            exact_hits, exact_graph_rows = await exact_task
        if instruction_exact_task is not None:
            exact_hits.extend(await instruction_exact_task)
        if payment_exact_task is not None:
            exact_hits.extend(await payment_exact_task)

        graph_rows = list(exact_graph_rows)
        seen_graph = {json.dumps(row, sort_keys=True, default=str) for row in graph_rows}
        for row in graph_result["rows"]:
            key = json.dumps(row, sort_keys=True, default=str)
            if key not in seen_graph:
                graph_rows.append(row)
                seen_graph.add(key)
        graph_result = {**graph_result, "rows": graph_rows}

        graph_hits = graph_rows_to_hits(graph_result["rows"])
        merged = self._merge_with_exact(exact_hits, vector_hits, bm25_hits, graph_hits)
        retrieval_ms = (time.perf_counter() - started) * 1000

        context = self._build_context(
            merged,
            graph_result["rows"],
            graph_result.get("cypher"),
            graph_unavailable=graph_result.get("graph_unavailable", False),
            mode=mode,
        )
        chat_history = [{"role": m.role, "content": m.content} for m in history[-8:]]

        gen_started = time.perf_counter()
        answer = None
        if _is_instruction_approval_question(message, mode):
            answer = await self._synthesize_instruction_approval_answer(
                message, event_ids, merged, graph_result["rows"]
            )
        if answer is None and _is_payment_approval_question(message, mode):
            answer = await self._synthesize_payment_approval_answer(
                message, event_ids, merged, graph_result["rows"]
            )
        if answer is None and is_max_payments_per_instruction_question(message):
            answer = _format_max_payments_per_instruction_answer(graph_result["rows"])
        if answer is None and is_payments_for_instruction_question(message):
            instruction_id = instruction_id_from_list_payments_question(message)
            if instruction_id:
                answer = _format_payments_for_instruction_answer(
                    instruction_id,
                    graph_result["rows"],
                )
        if answer is None and is_alert_ranking_question(message, mode=mode):
            answer = _format_alert_ranking_answer(message, graph_result["rows"])
        if answer is None:
            answer = await self.ollama.synthesize_answer(
                message, context, chat_history, mode=mode
            )
        generation_ms = (time.perf_counter() - gen_started) * 1000

        return ChatResponse(
            answer=answer,
            sources=[self._to_source(hit) for hit in merged],
            cypher=graph_result.get("cypher"),
            graph_rows=graph_result["rows"][:20],
            retrieval_ms=round(retrieval_ms, 1),
            generation_ms=round(generation_ms, 1),
        )

    async def _search_vector(
        self, query: str, limit: int, source: str | None = None
    ) -> list[dict[str, Any]]:
        try:
            vector = await self.ollama.embed(query)
            return await asyncio.to_thread(
                self.qdrant.search_vector, vector, limit=limit, source=source
            )
        except Exception as exc:
            logger.warning("vector search failed: %s", exc)
            return []

    def _search_bm25(
        self, query: str, limit: int, source: str | None = None
    ) -> list[dict[str, Any]]:
        try:
            return self.qdrant.search_bm25(query, limit=limit, source=source)
        except Exception as exc:
            logger.warning("BM25 search failed: %s", exc)
            return []

    async def _lookup_exact_event_ids(
        self, event_ids: list[str]
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        hits: list[dict[str, Any]] = []
        graph_rows: list[dict[str, Any]] = []

        for event_id in event_ids:
            qdrant_hit = await asyncio.to_thread(self.qdrant.fetch_by_event_id, event_id)
            if qdrant_hit is not None:
                hits.append(qdrant_hit)

            try:
                rows = await self.neo4j.lookup_instruction_for_event(event_id)
            except Exception as exc:
                logger.warning("exact graph lookup failed for %s: %s", event_id, exc)
                rows = []

            for row in rows:
                if row.get("instruction_id"):
                    graph_rows.append(row)

        return hits, graph_rows

    async def _lookup_exact_instruction_ids(
        self, instruction_ids: list[str], message: str
    ) -> list[dict[str, Any]]:
        hits: list[dict[str, Any]] = []
        approval_question = "approv" in message.lower()

        for instruction_id in instruction_ids:
            state_hit = await asyncio.to_thread(
                self.qdrant.fetch_by_instruction_id, instruction_id
            )
            if state_hit is not None:
                hits.append(state_hit)

            if approval_question:
                approve_hits = await asyncio.to_thread(
                    self.qdrant.fetch_instruction_approve_events, instruction_id
                )
                hits.extend(approve_hits)

        return hits

    async def _lookup_exact_payment_ids(
        self, payment_ids: list[str], message: str
    ) -> list[dict[str, Any]]:
        hits: list[dict[str, Any]] = []
        approval_question = "approv" in message.lower()

        for payment_id in payment_ids:
            fact_hit = await asyncio.to_thread(
                self.qdrant.fetch_by_payment_id, payment_id
            )
            if fact_hit is not None:
                hits.append(fact_hit)

            if approval_question:
                approve_hits = await asyncio.to_thread(
                    self.qdrant.fetch_payment_approve_events, payment_id
                )
                hits.extend(approve_hits)

        return hits

    @staticmethod
    def _merge_with_exact(
        exact_hits: list[dict[str, Any]],
        vector_hits: list[dict[str, Any]],
        bm25_hits: list[dict[str, Any]],
        graph_hits: list[dict[str, Any]],
    ) -> list[RankedHit]:
        merged = rrf_merge([vector_hits, bm25_hits, graph_hits])
        if not exact_hits:
            return merged[: settings.max_context_hits]

        exact_ranked = rrf_merge([exact_hits])
        pinned_keys = {hit.key for hit in exact_ranked}
        remainder = [hit for hit in merged if hit.key not in pinned_keys]
        return (exact_ranked + remainder)[: settings.max_context_hits]

    async def _search_graph(
        self, question: str, *, mode: SearchMode = "events"
    ) -> dict[str, Any]:
        planned = plan_graph_queries(question, mode=mode)
        if planned is not None:
            try:
                return await self._run_planned_graph_queries(planned)
            except Exception as exc:
                logger.warning("planned graph query failed: %s", exc)

        cypher: str | None = None
        try:
            cypher = await self.ollama.generate_cypher(question, self._schema, mode=mode)
            cypher = normalize_read_only_cypher(cypher)
            rows = await self.neo4j.run_cypher(cypher)
            return {"cypher": cypher, "rows": rows}
        except ValueError as exc:
            logger.warning(
                "Cypher validation rejected LLM query — %s | query=%r", exc, cypher
            )
            fallback = (
                plan_graph_queries(question, mode=mode) if planned is None else None
            )
            if fallback is not None:
                try:
                    return await self._run_planned_graph_queries(fallback)
                except Exception as fallback_exc:
                    logger.warning("planned graph fallback failed: %s", fallback_exc)
            return {"cypher": cypher, "rows": [], "graph_unavailable": True}
        except Exception as exc:
            logger.warning("graph search failed: %s", exc)
            return {"cypher": None, "rows": [], "graph_unavailable": True}

    async def _run_planned_graph_queries(
        self, planned: list[tuple[str, str]]
    ) -> dict[str, Any]:
        cyphers: list[str] = []
        rows: list[dict[str, Any]] = []
        for _label, query in planned:
            normalized = normalize_read_only_cypher(query)
            validate_read_only_cypher(normalized)
            cyphers.append(normalized)
            rows.extend(await self.neo4j.run_cypher(normalized))
        return {"cypher": "\n\n".join(cyphers), "rows": rows}

    async def _answer_payment_eligible_approvers(
        self,
        message: str,
        *,
        bearer_token: str | None,
        session_id: str | None,
    ) -> str | None:
        if not bearer_token:
            return (
                "This question requires a live OPA policy check. "
                "Log in as a compliance analyst (comp-001 or comp-002) using the sign-in "
                "panel above, then ask again with a payment ID."
            )

        payment_ids = extract_entity_ids(message)
        if not payment_ids:
            return (
                "Please include the payment ID in your question, e.g. "
                "\"Who can approve payment <payment-id>?\""
            )

        try:
            data = await self._eligibility.eligible_approvers_for_payment(
                payment_ids[0],
                bearer_token=bearer_token,
                session_id=session_id,
            )
        except EligibilityClientError as exc:
            return str(exc)

        return format_eligible_approvers_answer(data)

    async def _answer_instruction_eligible_approvers(
        self,
        message: str,
        *,
        bearer_token: str | None,
        session_id: str | None,
    ) -> str | None:
        if not bearer_token:
            return (
                "This question requires a live OPA policy check. "
                "Log in as a compliance analyst (comp-001 or comp-002) using the sign-in "
                "panel above, then ask again with an instruction ID."
            )

        instruction_ids = extract_entity_ids(message)
        if not instruction_ids:
            return (
                "Please include the instruction ID in your question, e.g. "
                "\"Who can approve instruction <instruction-id>?\""
            )

        try:
            data = await self._eligibility.eligible_approvers_for_instruction(
                instruction_ids[0],
                bearer_token=bearer_token,
                session_id=session_id,
            )
        except EligibilityClientError as exc:
            return str(exc)

        return format_instruction_eligible_approvers_answer(data)

    async def _synthesize_instruction_approval_answer(
        self,
        message: str,
        instruction_ids: list[str],
        hits: list[RankedHit],
        graph_rows: list[dict[str, Any]],
    ) -> str | None:
        """Return Who/When/Why when OPA authorization is in context; LLM rewrites WHY for readability."""
        if "approv" not in message.lower() or not instruction_ids:
            return None

        target_id = instruction_ids[0]
        approver: str | None = None
        when: str | None = None
        summary: str | None = None
        basis: list[str] = []

        for row in graph_rows:
            row_id = row.get("v.instruction_id") or row.get("instruction_id")
            if str(row_id) != target_id:
                continue
            summary = row.get("v.authorization_summary") or row.get("authorization_summary")
            approver = row.get("approver_display")
            when = row.get("v.approved_at") or row.get("approved_at")
            basis = _parse_authorization_basis(
                row.get("v.authorization_basis") or row.get("authorization_basis")
            )
            break

        for hit in hits:
            payload = hit.merged or {}
            payload_id = hit.instruction_id or payload.get("instruction_id")
            if str(payload_id) != target_id:
                continue
            summary = summary or payload.get("authorization_summary")
            approver = approver or payload.get("approver_display") or payload.get("actor_display")
            when = when or payload.get("approved_at") or payload.get("timestamp")
            if not basis:
                basis = _parse_authorization_basis(payload.get("authorization_basis"))
            if summary:
                break

        if not approver or not summary:
            return None

        why = await self.ollama.summarize_authorization_why(
            approver=approver,
            authorization_summary=_humanize_authorization_text(summary),
            authorization_basis=_humanize_policy_basis(basis) if basis else None,
        )
        why = _append_policy_basis(why, basis)

        when_line = f"WHEN: {when}" if when else None
        return "\n".join(
            line
            for line in (
                f"WHO: {approver}",
                when_line,
                f"WHY: {why}",
            )
            if line
        )

    async def _synthesize_payment_approval_answer(
        self,
        message: str,
        payment_ids: list[str],
        hits: list[RankedHit],
        graph_rows: list[dict[str, Any]],
    ) -> str | None:
        """Return Who/When/Why for payment approval using OPA authorization from indexed events."""
        if "approv" not in message.lower() or not payment_ids:
            return None

        target_id = payment_ids[0]
        approver: str | None = None
        when: str | None = None
        summary: str | None = None
        basis: list[str] = []

        for row in graph_rows:
            row_id = row.get("payment_id")
            if str(row_id) != target_id:
                continue
            summary = row.get("authorization_summary")
            approver = row.get("approver_display")
            when = row.get("approved_at")
            basis = _parse_authorization_basis(row.get("authorization_basis"))
            break

        for hit in hits:
            payload = hit.merged or {}
            payload_id = payload.get("payment_id")
            if str(payload_id) != target_id:
                continue

            is_approve_event = payload.get("action") == "APPROVE_PAYMENT" or payload.get(
                "source"
            ) in {"exact_approve_payment_event", "payment_security_event"}

            if is_approve_event:
                summary = payload.get("authorization_summary") or summary
                approver = (
                    payload.get("actor_display")
                    or payload.get("approver_display")
                    or approver
                )
                when = payload.get("timestamp") or payload.get("approved_at") or when
                if not basis:
                    basis = _parse_authorization_basis(payload.get("authorization_basis"))
            else:
                approver = approver or payload.get("approver_display")
                when = when or payload.get("approved_at")

            if summary and approver and basis:
                break

        if not approver:
            return None
        if not summary and not basis:
            return None

        summary = summary or f"{approver} was allowed to APPROVE_PAYMENT"
        readable_basis = _humanize_policy_basis(basis) if basis else None
        why = await self.ollama.summarize_authorization_why(
            approver=approver,
            authorization_summary=_humanize_authorization_text(summary),
            authorization_basis=readable_basis,
        )
        why = _append_policy_basis(why, basis)

        when_line = f"WHEN: {when}" if when else None
        return "\n".join(
            line
            for line in (
                f"WHO: {approver}",
                when_line,
                f"WHY: {why}",
            )
            if line
        )

    @staticmethod
    def _build_context(
        hits: list[RankedHit],
        graph_rows: list[dict[str, Any]],
        cypher: str | None,
        *,
        graph_unavailable: bool = False,
        mode: SearchMode = "events",
    ) -> str:
        sections: list[str] = []

        if mode == "instructions":
            sections.append("Search mode: INSTRUCTIONS (instruction master graph — independent of security events)")
        elif mode == "all":
            sections.append(
                "Search mode: ALL ENTITIES (instructions, payments, and all security events)"
            )
        elif mode == "payments":
            sections.append("Search mode: PAYMENTS (payment records only)")
        elif mode == "events":
            sections.append(
                "Search mode: SECURITY EVENTS (instruction + payment security event log)"
            )

        if graph_unavailable:
            if mode == "instructions":
                sections.append(
                    "Note: instruction graph search was unavailable. "
                    "Do not infer hierarchy or structural relationships from vector/BM25 hits."
                )
            else:
                sections.append(
                    "Note: graph search was unavailable for this question. "
                    "Answer using the retrieved vector and BM25 results below only."
                )

        if cypher:
            sections.append(f"Neo4j Cypher executed:\n{cypher}")

        if graph_rows:
            ranking_rows = [
                row
                for row in graph_rows
                if "alert_count" in row and "actor_display" in row
            ]
            if ranking_rows:
                sections.append(
                    "Neo4j user ranking by policy alerts (instruction + payment combined):\n"
                    + json.dumps(ranking_rows[:20], indent=2, default=str)
                )
            aggregate = next(
                (
                    row
                    for row in graph_rows
                    if any(key in row for key in ("total", "count"))
                    and "alert_count" not in row
                    and len(row) <= 3
                ),
                None,
            )
            if aggregate is not None:
                total = aggregate.get("total", aggregate.get("count"))
                sections.append(f"Neo4j aggregate count: {total}")
            detail_rows = [
                row for row in graph_rows if row not in ranking_rows and row is not aggregate
            ] if ranking_rows or aggregate else graph_rows
            if detail_rows:
                sections.append(
                    "Neo4j graph results:\n"
                    + json.dumps(detail_rows[:20], indent=2, default=str)
                )
        elif cypher and not graph_unavailable:
            sections.append(
                "Neo4j graph results: 0 rows — the graph query found no matching records. "
                "For structural questions (supervisor relationships, hierarchy violations, "
                "cross-approvals) this means no such case exists in the data. "
                "Do NOT use vector/BM25 hits to contradict this finding."
            )

        if hits:
            lines: list[str] = []
            for index, hit in enumerate(hits, start=1):
                payload = hit.merged or {}
                snap = payload.get("instruction_snapshot") or {}
                src = payload.get("source") or (sorted(hit.sources)[0] if hit.sources else "?")
                if src in {"vector", "bm25", "exact"} and payload.get("source"):
                    src = payload.get("source")

                if src == "instruction_state" or src == "exact_instruction":
                    party_lines = _instruction_lifecycle_party_lines(payload, snap)
                    lines.append(
                        f"[{index}] INSTRUCTION instruction_id={hit.instruction_id} "
                        f"score={hit.score:.4f}\n"
                        f"  status={snap.get('status')} type={snap.get('instruction_type')} "
                        f"lob={snap.get('owning_lob')} currency={snap.get('currency')} "
                        f"scope={snap.get('wire_scope')}\n"
                        f"  creditor={snap.get('creditor_name')} "
                        f"creditor_acct={snap.get('creditor_account_id')}\n"
                        f"  creator={payload.get('creator_display')}\n"
                        f"  {party_lines}\n"
                        f"  why={payload.get('authorization_summary') or ''}\n"
                        f"  basis={_format_basis_join(payload.get('authorization_basis'))}\n"
                        f"  effective={snap.get('effective_date')} end={snap.get('end_date')} "
                        f"expired={snap.get('is_expired', False)}"
                    )
                elif src == "payment_fact":
                    psnap = payload.get("payment_snapshot") or {}
                    lines.append(
                        f"[{index}] PAYMENT payment_id={payload.get('payment_id')} "
                        f"instruction_id={payload.get('instruction_id')} score={hit.score:.4f}\n"
                        f"  status={payload.get('status', psnap.get('status'))} "
                        f"amount={payload.get('amount', psnap.get('amount'))} "
                        f"currency={payload.get('currency', psnap.get('currency'))} "
                        f"lob={payload.get('owning_lob', psnap.get('owning_lob'))}\n"
                        f"  value_date={payload.get('value_date', psnap.get('value_date'))}\n"
                        f"  creator={payload.get('creator_display')} "
                        f"approver={payload.get('approver_display')}"
                    )
                elif src == "payment_security_event":
                    psnap = payload.get("payment_snapshot") or {}
                    lines.append(
                        f"[{index}] PAYMENT SECURITY EVENT event_id={hit.event_id} "
                        f"payment_id={payload.get('payment_id')} "
                        f"instruction_id={payload.get('instruction_id')} score={hit.score:.4f}\n"
                        f"  time={payload.get('timestamp')} action={payload.get('action')} "
                        f"severity={payload.get('severity')} outcome={payload.get('outcome')} "
                        f"actor={payload.get('actor_display')}\n"
                        f"  amount={payload.get('amount', psnap.get('amount'))} "
                        f"currency={payload.get('currency', psnap.get('currency'))} "
                        f"lob={payload.get('owning_lob', psnap.get('owning_lob'))}\n"
                        f"  why={payload.get('authorization_summary') or payload.get('reason') or payload.get('message', hit.summary)}\n"
                        f"  basis={_format_basis_join(payload.get('authorization_basis'))}"
                    )
                elif src in ("instruction_security_event", "security_event", "exact_approve_event"):
                    merged = hit.merged or {}
                    event_snap = merged.get("instruction_snapshot") or {}
                    status = (merged.get("status") or event_snap.get("status") or "").upper()
                    action = (merged.get("action") or "").upper()
                    if status == "REJECTED" or action == "REJECT":
                        party_lines = (
                            f"rejected_by={merged.get('rejector_display') or merged.get('actor_display', merged.get('actor_user_id'))}\n"
                            f"  rejected_at={merged.get('rejected_at') or event_snap.get('rejected_at') or merged.get('timestamp')}\n"
                            f"  rejection_reason={merged.get('rejection_reason') or event_snap.get('rejection_reason') or ''}"
                        )
                    else:
                        party_lines = (
                            f"approver={merged.get('approver_display', merged.get('approver_user_id'))}\n"
                            f"  approved_at={merged.get('approved_at') or event_snap.get('approved_at') or ''}"
                        )
                    lines.append(
                        f"[{index}] INSTRUCTION SECURITY EVENT event_id={hit.event_id} "
                        f"instruction_id={hit.instruction_id} "
                        f"sources={sorted(hit.sources)} score={hit.score:.4f}\n"
                        f"  time={merged.get('timestamp')} action={merged.get('action')} "
                        f"severity={merged.get('severity')} outcome={merged.get('outcome')} "
                        f"actor={merged.get('actor_display', merged.get('actor_user_id'))} "
                        f"lob={merged.get('owning_lob')}\n"
                        f"  creator={merged.get('creator_display', merged.get('creator_user_id'))}\n"
                        f"  {party_lines}\n"
                        f"  why={merged.get('authorization_summary') or merged.get('event_reason') or merged.get('reason') or hit.summary}\n"
                        f"  basis={_format_basis_join(merged.get('authorization_basis'))}"
                    )
                else:
                    merged = hit.merged or {}
                    lines.append(
                        f"[{index}] UNKNOWN source={src} event_id={hit.event_id} "
                        f"instruction_id={hit.instruction_id} score={hit.score:.4f}\n"
                        f"  summary: {hit.summary or merged.get('message', '')}"
                    )
            label = {
                "events": "Retrieved security events (instruction + payment)",
                "instructions": "Retrieved instruction states",
                "payments": "Retrieved payment records",
                "all": "Retrieved results across all entity types",
            }.get(mode, "Retrieved results")
            sections.append(f"{label} (vector + BM25 + graph):\n" + "\n".join(lines))

        if not sections:
            return "No indexed data was found."
        return "\n\n".join(sections)

    @staticmethod
    def _to_source(hit: RankedHit) -> SourceHit:
        return SourceHit(
            event_id=hit.event_id,
            instruction_id=hit.instruction_id,
            score=round(hit.score, 4),
            sources=sorted(hit.sources),
            summary=hit.summary,
            merged=hit.merged,
            security_event=hit.security_event,
        )
