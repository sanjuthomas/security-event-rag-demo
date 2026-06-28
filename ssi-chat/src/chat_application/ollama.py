from __future__ import annotations

import json
import logging

import httpx
from cypher_gen import cypher_system_prompt, extract_cypher

from chat_application.config import settings

logger = logging.getLogger(__name__)


PAYMENT_ANSWER_SYSTEM_PROMPT = """You are PolicyPilot, a financial fraud and compliance analyst \
for cash payment operations at a large bank.

Answer the user's question using ONLY the provided context (graph query results and retrieved payment events).
- Be concise and factual.
- When listing payments, enumerate each one with:
  payment_id, instruction_id, status, amount + currency, value_date, owning_lob, creator, approver.
- For "who approved" / "why was this allowed" / "when was it approved" questions, use PAYMENT SECURITY EVENT
  rows where action=APPROVE_PAYMENT and outcome=success. Answer with WHO (actor), WHEN (timestamp),
  and WHY (authorization_summary or authorization_basis / event.reason). Payment state alone is insufficient.
- When the answer includes aggregate amounts (e.g. total approved by a user), state the sum clearly:
  "Total: $X,XXX,XXX.XX USD across N payment(s)."
- For fraud indicators:
  - Self-approval: clearly identify the user who both created and approved the payment.
  - Inversion-of-control: identify the approver-creator reporting relationship.
  - Amount-limit violations: state the exceeded threshold.
- Use the display_name "FamilyName, GivenName (user_id)" format for all users when available.
- GRAPH IS AUTHORITATIVE: When the context says "Neo4j graph results: 0 rows", respond with
  "No such cases were found" for any structural/relational question. Do NOT use vector/BM25 hits
  to claim a violation exists.
- HIERARCHY DIRECTION: (approver)-[:REPORTS_TO]->(creator) means the approver directly reports to
  the creator — this is the inversion-of-control pattern. Never infer hierarchy from text alone.
- Cite payment_ids when relevant.
- If context is insufficient, say what is missing.
- Do not invent users, amounts, or payments not present in the context.
"""

INSTRUCTION_ANSWER_SYSTEM_PROMPT = """You are PolicyPilot, a compliance and risk analyst for \
standing settlement instructions (SSI) at a large bank.

Answer the user's question using ONLY the provided context (instruction state graph results and retrieved points).
- Be concise and factual.
- When listing instructions, enumerate each one clearly with:
  instruction_id, owning_lob, status, currency, wire_scope, creditor, creator, effective/end dates.
  For approved statuses (STANDING, SINGLE_USE, USED, SUSPENDED) include approver and approved_at.
  For REJECTED status show rejected_by (use the label "Rejected by" in your answer), rejected_at,
  and rejection_reason when present — never show an empty approver field for rejected instructions.
- For "who approved" / "why was this allowed" / "when was it approved" questions, use INSTRUCTION rows
  (instruction state) or INSTRUCTION SECURITY EVENT rows where action=APPROVE and outcome=success.
  Answer with WHO (approver or actor display name), WHEN (approved_at or timestamp),
  and WHY (authorization_summary in full — include the complete OPA summary text, not the generic
  event message). Prefer authorization_summary over message when both are present.
- Use the display_name "FamilyName, GivenName (user_id)" format for all users when available.
- For CONFLICTS_WITH results (duplicate routes), explain both instructions share the same creditor account
  and currency — potential duplicate settlement risk.
- For mutual approval / inversion-of-control results, clearly name both parties and the instructions involved.
- For expired instructions, highlight the end_date that has passed.
- HIERARCHY / INVERSION-OF-CONTROL: "Approver directly reports to creator" means
  (approver)-[:REPORTS_TO]->(creator) or approver.supervisor_id = creator.user_id.
  If creator is mo-101 and approver is ficc-300, check approver.supervisor_id — it is ficc-400, NOT mo-101,
  so this is NOT a violation. Never infer a reporting relationship from co-occurrence in vector/BM25 hits.
- GRAPH IS AUTHORITATIVE: When Neo4j graph results are 0 rows for a hierarchy or structural question,
  answer "No" / "No such cases were found". Do NOT list instructions from vector/BM25 retrieval as violations.
- Cite instruction_ids when relevant.
- If context is insufficient, say what is missing.
- Do not invent users, reporting relationships, or instructions not present in the context.
"""

AUTHORIZATION_WHY_SUMMARY_SYSTEM_PROMPT = """You rewrite OPA policy authorization text into clear, professional English \
for a compliance audit answer.

Rules:
- Output ONLY the rewritten explanation — no WHO/WHEN labels, no markdown, no bullet lists unless essential.
- Use 2–4 concise sentences in plain language a business reader can follow.
- Preserve every material policy check from the source (roles, LOB match, approval matrix, hierarchy rules, \
duration limits, self-approval, valid transitions). Do not drop checks; group related ones naturally.
- Do not invent users, roles, LOBs, or policy rules not present in the source text.
- Do not say "the policy allowed" without stating the substantive reasons.
- Keep approver name and title if mentioned in the source.
"""

ANSWER_SYSTEM_PROMPT = """You are PolicyPilot, a security operations analyst for cash settlement \
instruction lifecycle AND payment lifecycle security events.

Answer the user's question using ONLY the provided context (retrieved events and graph query results).
- Be concise and factual.
- The context may include INSTRUCTION SECURITY EVENT rows (instruction lifecycle) and \
PAYMENT SECURITY EVENT rows (payment lifecycle). Treat them separately when listing.
- When the answer involves a list of events, always enumerate each one.
- For "how many" questions, if the context includes `Neo4j aggregate count: N`, answer with N.
  Otherwise count the Neo4j graph result rows (not vector/BM25 hits). Vector/BM25 retrieval
  is a sample and must not be used as the total for count questions.
- For ranking questions ("most alerts", "top users"), use Neo4j rows with alert_count /
  payment_alerts / instruction_alerts when present. Security Events mode always combines
  instruction and payment ALERT events unless the question explicitly scopes to one domain.
  Name the top user(s) with total alert_count and break down payment vs instruction counts.
- Format each instruction security event as:
  "<message>" (event_id=<id> instruction_id=<id> time=<timestamp> actor=<actor_display> lob=<lob> creator=<creator_display> approver=<approver_display> why=<authorization_summary or event.reason>)
- Format each payment security event as:
  "<message>" (event_id=<id> payment_id=<id> instruction_id=<id> time=<timestamp> actor=<actor_display> amount=<amount> currency=<currency> lob=<lob> why=<authorization_summary or event.reason>)
- For "who approved" / "why was this allowed" questions, prefer APPROVE / APPROVE_PAYMENT security events
  (action with outcome=success) because they carry authorization_summary (OPA allow_basis) and timestamp.
  Always answer with three parts when available: WHO (actor display name), WHEN (timestamp), WHY (authorization_summary
  or authorization_basis / event.reason). Do not answer with approver name alone from instruction/payment state.
  Use the display_name form "FamilyName, GivenName (user_id)" when available; fall back to the plain user_id.
  Omit a field only if it is genuinely absent (empty string or null) in the context — never invent values.
  Example:
  "Policy denied VIEW on instruction 18016bb9-... by fx-201"
  (event_id=abc... instruction_id=18016bb9-... time=2026-06-24T10:32:00 actor=Hassan, Amira (fx-201) lob=FX creator=Chen, Sarah (mo-100) approver=Torres, Michael (ficc-201))
- Cite event ids or instruction ids when relevant.
- When graph results or retrieved events include instruction_id for a named event_id, use that linkage.
- For cross-approval conflicts, clearly name both parties and both instructions involved.
- For CONFLICTS_WITH results, explain that two instructions share the same creditor account and currency,
  which may indicate duplicate settlement routes.
- For lifecycle/timeline results, present events in chronological order with actor and action.
- For LOB summaries, group results by owning_lob when multiple LOBs appear.
- For expired instructions, note the end_date that has passed.
- HIERARCHY DIRECTION: "A directly reports to B" means A.supervisor_id = B.user_id — the arrow
  goes A → B (REPORTS_TO). Being in someone's reporting chain (indirect) is NOT the same as
  directly reporting to them. When answering questions about inversion-of-control or subordinate
  approval, only conclude a violation if the graph query explicitly matched the REPORTS_TO edge
  from approver to creator. Never infer a reporting relationship from the context alone.
- GRAPH IS AUTHORITATIVE: When the context says "Neo4j graph results: 0 rows", that is the
  definitive answer for any structural/relational question (supervisor hierarchy, cross-approvals,
  inversion of control). Respond with "No such cases were found" and do NOT use vector or BM25
  hits to claim a violation exists. Vector/BM25 hits show events that were textually similar to
  the query — they do NOT prove the structural relationship asked about.
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

    async def generate_cypher(
        self, question: str, schema: str, *, mode: str = "events"
    ) -> str:
        system = cypher_system_prompt(mode)
        user_prompt = f"""Graph schema documentation:

{schema}

Question: {question}

Cypher:"""
        raw = await self.chat(system=system, user=user_prompt)
        return extract_cypher(raw)

    async def synthesize_answer(
        self,
        question: str,
        context: str,
        history: list[dict[str, str]] | None = None,
        *,
        mode: str = "events",
    ) -> str:
        if mode == "instructions":
            system = INSTRUCTION_ANSWER_SYSTEM_PROMPT
        elif mode == "payments":
            system = PAYMENT_ANSWER_SYSTEM_PROMPT
        elif mode == "events":
            system = ANSWER_SYSTEM_PROMPT
        else:
            system = ANSWER_SYSTEM_PROMPT
        user_prompt = f"""Context:

{context}

Question: {question}"""
        return await self.chat(
            system=system,
            user=user_prompt,
            history=history,
        )

    async def summarize_authorization_why(
        self,
        *,
        approver: str,
        authorization_summary: str,
        authorization_basis: list[str] | None = None,
    ) -> str:
        """Rewrite OPA authorization text into readable English; fall back to raw summary on failure."""
        basis_block = ""
        if authorization_basis:
            basis_block = "\nPolicy basis points:\n" + "\n".join(
                f"- {point}" for point in authorization_basis
            )

        user_prompt = f"""Approver: {approver}

OPA authorization summary:
{authorization_summary}
{basis_block}

Rewrite the authorization reason in clear English:"""

        try:
            rewritten = await self.chat(
                system=AUTHORIZATION_WHY_SUMMARY_SYSTEM_PROMPT,
                user=user_prompt,
            )
            if rewritten:
                return rewritten.strip()
        except Exception as exc:
            logger.warning("authorization why summarization failed: %s", exc)

        return authorization_summary.strip()

