from __future__ import annotations

import json
import logging
import re

import httpx

from chat_application.config import settings

logger = logging.getLogger(__name__)

CYPHER_SYSTEM_PROMPT = """You translate natural-language questions about SSI instruction lifecycle \
security events into read-only Neo4j Cypher.

Rules:
- Output ONLY a single Cypher query. No markdown fences, no explanation.
- READ-ONLY: use MATCH, OPTIONAL MATCH, WITH, WHERE, RETURN, ORDER BY, LIMIT, UNWIND, count(), collect().
- Never use CREATE, MERGE, SET, DELETE, REMOVE, DROP, CALL db.* write procedures.
- Always return individual event rows — NEVER return only an aggregate scalar like count(...) AS total.
  The answer model will count the rows itself. This ensures all detail fields are available per row.
- Every RETURN that involves a SecurityEvent (e) MUST include ALL of the following columns:
    e.event_id
    e.timestamp
    e.action
    e.message
    coalesce(v.instruction_id, '') AS instruction_id
    coalesce(e.owning_lob, v.owning_lob, '') AS lob
    coalesce(actor.display_name, actor.user_id, '') AS actor_display
    coalesce(v.creator_user_id, '') AS creator_user_id
    coalesce(creatorUser.display_name, v.creator_user_id, '') AS creator_display
    coalesce(v.approver_user_id, '') AS approver_user_id
    coalesce(approverUser.display_name, v.approver_user_id, '') AS approver_display
  To populate actor always add:
    OPTIONAL MATCH (actor:User)-[:ACTED_AS]->(e)
  To populate instruction_id, lob, creator, approver always add:
    OPTIONAL MATCH (e)-[:TARGETS_VERSION]->(v:InstructionVersion)
    OPTIONAL MATCH (creatorUser:User {user_id: v.creator_user_id})
    OPTIONAL MATCH (approverUser:User {user_id: v.approver_user_id})
- User display_name format is "FamilyName, GivenName (user_id)" — use it when available.
- User ids are lowercase codes like mo-100, ficc-201, ficc-300.
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
- User nodes have a supervisor_id property (the user_id of their direct manager) and a \
[:REPORTS_TO] relationship: (subordinate:User)-[:REPORTS_TO]->(manager:User). \
ALWAYS use (subordinate)-[:REPORTS_TO]->(manager) — never reverse this direction. \
"A reports to B" means A.supervisor_id = B.user_id and (A)-[:REPORTS_TO]->(B). \
"B's subordinate" means someone whose supervisor_id equals B.user_id. \
Never confuse "A is in B's reporting chain" (indirect) with "A directly reports to B" (A.supervisor_id = B.user_id).

Example — ALERT events today (always return rows, not just a count):
MATCH (e:SecurityEvent {severity: 'ALERT'})
WHERE date(datetime(e.timestamp)) = date()
OPTIONAL MATCH (e)-[:TARGETS_VERSION]->(v:InstructionVersion)
OPTIONAL MATCH (actor:User)-[:ACTED_AS]->(e)
OPTIONAL MATCH (creatorUser:User {user_id: v.creator_user_id})
OPTIONAL MATCH (approverUser:User {user_id: v.approver_user_id})
RETURN e.event_id, e.timestamp, e.action, e.message,
       coalesce(v.instruction_id, '') AS instruction_id,
       coalesce(e.owning_lob, v.owning_lob, '') AS lob,
       coalesce(actor.display_name, actor.user_id, '') AS actor_display,
       coalesce(v.creator_user_id, '') AS creator_user_id,
       coalesce(creatorUser.display_name, v.creator_user_id, '') AS creator_display,
       coalesce(v.approver_user_id, '') AS approver_user_id,
       coalesce(approverUser.display_name, v.approver_user_id, '') AS approver_display
ORDER BY e.timestamp DESC
LIMIT 50

Example — instructions created today:
MATCH (e:SecurityEvent {action: 'CREATE', outcome: 'success'})
WHERE date(datetime(e.timestamp)) = date()
OPTIONAL MATCH (e)-[:TARGETS_VERSION]->(v:InstructionVersion)
OPTIONAL MATCH (actor:User)-[:ACTED_AS]->(e)
OPTIONAL MATCH (creatorUser:User {user_id: v.creator_user_id})
OPTIONAL MATCH (approverUser:User {user_id: v.approver_user_id})
RETURN e.event_id, e.timestamp, e.action, e.message,
       coalesce(v.instruction_id, '') AS instruction_id,
       coalesce(e.owning_lob, v.owning_lob, '') AS lob,
       coalesce(actor.display_name, actor.user_id, '') AS actor_display,
       coalesce(v.creator_user_id, '') AS creator_user_id,
       coalesce(creatorUser.display_name, v.creator_user_id, '') AS creator_display,
       coalesce(v.approver_user_id, '') AS approver_user_id,
       coalesce(approverUser.display_name, v.approver_user_id, '') AS approver_display
ORDER BY e.timestamp DESC
LIMIT 50

Example — instruction for a specific security event:
MATCH (e:SecurityEvent {event_id: '00000000-0000-0000-0000-000000000001'})
OPTIONAL MATCH (e)-[:TARGETS_VERSION]->(v:InstructionVersion)
OPTIONAL MATCH (actor:User)-[:ACTED_AS]->(e)
OPTIONAL MATCH (creatorUser:User {user_id: v.creator_user_id})
OPTIONAL MATCH (approverUser:User {user_id: v.approver_user_id})
RETURN e.event_id, e.timestamp, e.action, e.message,
       coalesce(v.instruction_id, '') AS instruction_id,
       coalesce(e.owning_lob, v.owning_lob, '') AS lob,
       coalesce(actor.display_name, actor.user_id, '') AS actor_display,
       coalesce(v.creator_user_id, '') AS creator_user_id,
       coalesce(creatorUser.display_name, v.creator_user_id, '') AS creator_display,
       coalesce(v.approver_user_id, '') AS approver_user_id,
       coalesce(approverUser.display_name, v.approver_user_id, '') AS approver_display
LIMIT 1

Example — who created instructions rejected by a user:
MATCH (u:User {user_id: 'ficc-201'})-[:ACTED_AS]->(e:SecurityEvent {action: 'REJECT'})
OPTIONAL MATCH (e)-[:TARGETS_VERSION]->(v:InstructionVersion)
OPTIONAL MATCH (actor:User)-[:ACTED_AS]->(e)
OPTIONAL MATCH (creatorUser:User {user_id: v.creator_user_id})
OPTIONAL MATCH (approverUser:User {user_id: v.approver_user_id})
RETURN e.event_id, e.timestamp, e.action, e.message,
       coalesce(v.instruction_id, '') AS instruction_id,
       coalesce(e.owning_lob, v.owning_lob, '') AS lob,
       coalesce(actor.display_name, actor.user_id, '') AS actor_display,
       coalesce(v.creator_user_id, '') AS creator_user_id,
       coalesce(creatorUser.display_name, v.creator_user_id, '') AS creator_display,
       coalesce(v.approver_user_id, '') AS approver_user_id,
       coalesce(approverUser.display_name, v.approver_user_id, '') AS approver_display
ORDER BY e.timestamp DESC
LIMIT 20

Example — self-approval attempt: user approved an instruction they created (segregation of duties violation):
MATCH (actor:User)-[:ACTED_AS]->(e:SecurityEvent {action: 'APPROVE'})
MATCH (e)-[:TARGETS_VERSION]->(v:InstructionVersion)
WHERE actor.user_id = v.creator_user_id
OPTIONAL MATCH (creatorUser:User {user_id: v.creator_user_id})
OPTIONAL MATCH (approverUser:User {user_id: v.approver_user_id})
RETURN e.event_id, e.timestamp, e.action, e.outcome, e.message, e.reason, e.authorization_summary,
       coalesce(v.instruction_id, '') AS instruction_id,
       coalesce(e.owning_lob, v.owning_lob, '') AS lob,
       coalesce(actor.display_name, actor.user_id, '') AS actor_display,
       coalesce(v.creator_user_id, '') AS creator_user_id,
       coalesce(creatorUser.display_name, v.creator_user_id, '') AS creator_display,
       coalesce(v.approver_user_id, '') AS approver_user_id,
       coalesce(approverUser.display_name, v.approver_user_id, '') AS approver_display
ORDER BY e.timestamp DESC
LIMIT 20

Example — who approved a specific instruction (successful APPROVE security event):
MATCH (e:SecurityEvent {action: 'APPROVE', outcome: 'success'})-[:TARGETS_VERSION]->(v:InstructionVersion {instruction_id: '00000000-0000-0000-0000-000000000001'})
OPTIONAL MATCH (actor:User)-[:ACTED_AS]->(e)
OPTIONAL MATCH (creatorUser:User {user_id: v.creator_user_id})
RETURN e.event_id, e.timestamp, e.action, e.outcome, e.reason, e.authorization_summary,
       v.instruction_id, coalesce(e.owning_lob, v.owning_lob, '') AS lob,
       coalesce(actor.display_name, actor.user_id, '') AS actor_display,
       coalesce(creatorUser.display_name, v.creator_user_id, '') AS creator_display
ORDER BY e.timestamp DESC
LIMIT 1

Example — all APPROVE events (successful and denied) to check for policy violations:
MATCH (actor:User)-[:ACTED_AS]->(e:SecurityEvent {action: 'APPROVE'})
OPTIONAL MATCH (e)-[:TARGETS_VERSION]->(v:InstructionVersion)
OPTIONAL MATCH (creatorUser:User {user_id: v.creator_user_id})
OPTIONAL MATCH (approverUser:User {user_id: v.approver_user_id})
RETURN e.event_id, e.timestamp, e.action, e.outcome, e.message,
       coalesce(v.instruction_id, '') AS instruction_id,
       coalesce(e.owning_lob, v.owning_lob, '') AS lob,
       coalesce(actor.display_name, actor.user_id, '') AS actor_display,
       coalesce(v.creator_user_id, '') AS creator_user_id,
       coalesce(creatorUser.display_name, v.creator_user_id, '') AS creator_display,
       coalesce(v.approver_user_id, '') AS approver_user_id,
       coalesce(approverUser.display_name, v.approver_user_id, '') AS approver_display
ORDER BY e.timestamp DESC
LIMIT 50

Example — subordinate approved creator's instruction (approver directly reports to the creator):
MATCH (actor:User)-[:ACTED_AS]->(e:SecurityEvent {action: 'APPROVE'})
MATCH (e)-[:TARGETS_VERSION]->(v:InstructionVersion)
MATCH (actor)-[:REPORTS_TO]->(creatorUser:User {user_id: v.creator_user_id})
OPTIONAL MATCH (approverUser:User {user_id: v.approver_user_id})
RETURN e.event_id, e.timestamp, e.action, e.outcome, e.message,
       coalesce(v.instruction_id, '') AS instruction_id,
       coalesce(e.owning_lob, v.owning_lob, '') AS lob,
       coalesce(actor.display_name, actor.user_id, '') AS actor_display,
       coalesce(v.creator_user_id, '') AS creator_user_id,
       coalesce(creatorUser.display_name, v.creator_user_id, '') AS creator_display,
       coalesce(v.approver_user_id, '') AS approver_user_id,
       coalesce(approverUser.display_name, v.approver_user_id, '') AS approver_display
ORDER BY e.timestamp DESC
LIMIT 20

Example — cross-approval conflict (users who approved each other's instructions):
MATCH (approver:User)-[:APPROVED]->(v1:InstructionVersion)<-[:CREATED]-(creator:User)
MATCH (creator)-[:APPROVED]->(v2:InstructionVersion)<-[:CREATED]-(approver)
WHERE approver.user_id <> creator.user_id
OPTIONAL MATCH (e1:SecurityEvent)-[:TARGETS_VERSION]->(v1) WHERE e1.action = 'APPROVE'
OPTIONAL MATCH (e2:SecurityEvent)-[:TARGETS_VERSION]->(v2) WHERE e2.action = 'APPROVE'
RETURN coalesce(approver.display_name, approver.user_id) AS approver_display,
       coalesce(creator.display_name, creator.user_id) AS creator_display,
       v1.instruction_id AS instruction_approved, v1.owning_lob AS lob,
       e1.event_id, e1.timestamp AS approved_at, e1.message,
       v2.instruction_id AS reciprocal_instruction, e2.timestamp AS reciprocal_at
ORDER BY e1.timestamp DESC
LIMIT 20

Example — instructions sharing the same creditor account (potential duplicate routes / CONFLICTS_WITH):
MATCH (v1:InstructionVersion)-[:CONFLICTS_WITH]->(v2:InstructionVersion)
WHERE v1.version_key < v2.version_key
OPTIONAL MATCH (e:SecurityEvent)-[:TARGETS_VERSION]->(v1)
OPTIONAL MATCH (actor:User)-[:ACTED_AS]->(e)
OPTIONAL MATCH (creatorUser:User {user_id: v1.creator_user_id})
OPTIONAL MATCH (approverUser:User {user_id: v1.approver_user_id})
RETURN v1.instruction_id AS instruction_a, v2.instruction_id AS instruction_b,
       v1.creditor_account_id AS shared_creditor_account,
       v1.currency AS currency, v1.status AS status_a, v2.status AS status_b,
       coalesce(v1.owning_lob, '') AS lob,
       e.event_id, e.timestamp, e.message,
       coalesce(actor.display_name, actor.user_id, '') AS actor_display,
       coalesce(v1.creator_user_id, '') AS creator_user_id,
       coalesce(creatorUser.display_name, v1.creator_user_id, '') AS creator_display,
       coalesce(v1.approver_user_id, '') AS approver_user_id,
       coalesce(approverUser.display_name, v1.approver_user_id, '') AS approver_display
ORDER BY e.timestamp DESC
LIMIT 20

Example — full lifecycle timeline of a specific instruction (replace UUID):
MATCH (e:SecurityEvent)-[:TARGETS]->(i:Instruction {instruction_id: '00000000-0000-0000-0000-000000000001'})
OPTIONAL MATCH (e)-[:TARGETS_VERSION]->(v:InstructionVersion)
OPTIONAL MATCH (actor:User)-[:ACTED_AS]->(e)
OPTIONAL MATCH (creatorUser:User {user_id: v.creator_user_id})
OPTIONAL MATCH (approverUser:User {user_id: v.approver_user_id})
RETURN e.event_id, e.timestamp, e.action, e.outcome, e.message,
       coalesce(v.instruction_id, '') AS instruction_id,
       coalesce(e.owning_lob, v.owning_lob, '') AS lob,
       coalesce(actor.display_name, actor.user_id, '') AS actor_display,
       coalesce(v.creator_user_id, '') AS creator_user_id,
       coalesce(creatorUser.display_name, v.creator_user_id, '') AS creator_display,
       coalesce(v.approver_user_id, '') AS approver_user_id,
       coalesce(approverUser.display_name, v.approver_user_id, '') AS approver_display
ORDER BY e.timestamp ASC
LIMIT 50

Example — all actions by a specific user this week:
MATCH (u:User {user_id: 'fx-201'})-[:ACTED_AS]->(e:SecurityEvent)
WHERE datetime(e.timestamp) > datetime() - duration({days: 7})
OPTIONAL MATCH (e)-[:TARGETS_VERSION]->(v:InstructionVersion)
OPTIONAL MATCH (actor:User)-[:ACTED_AS]->(e)
OPTIONAL MATCH (creatorUser:User {user_id: v.creator_user_id})
OPTIONAL MATCH (approverUser:User {user_id: v.approver_user_id})
RETURN e.event_id, e.timestamp, e.action, e.outcome, e.message,
       coalesce(v.instruction_id, '') AS instruction_id,
       coalesce(e.owning_lob, v.owning_lob, '') AS lob,
       coalesce(actor.display_name, actor.user_id, '') AS actor_display,
       coalesce(v.creator_user_id, '') AS creator_user_id,
       coalesce(creatorUser.display_name, v.creator_user_id, '') AS creator_display,
       coalesce(v.approver_user_id, '') AS approver_user_id,
       coalesce(approverUser.display_name, v.approver_user_id, '') AS approver_display
ORDER BY e.timestamp DESC
LIMIT 50

Example — PENDING instructions by LOB / profit center:
MATCH (v:InstructionVersion {status: 'PENDING'})
OPTIONAL MATCH (e:SecurityEvent)-[:TARGETS_VERSION]->(v) WHERE e.action = 'SUBMIT'
OPTIONAL MATCH (actor:User)-[:ACTED_AS]->(e)
OPTIONAL MATCH (creatorUser:User {user_id: v.creator_user_id})
OPTIONAL MATCH (approverUser:User {user_id: v.approver_user_id})
RETURN v.instruction_id AS instruction_id, v.owning_lob AS lob,
       v.currency AS currency, v.wire_scope AS wire_scope,
       e.event_id, e.timestamp, e.message,
       coalesce(actor.display_name, actor.user_id, '') AS actor_display,
       coalesce(v.creator_user_id, '') AS creator_user_id,
       coalesce(creatorUser.display_name, v.creator_user_id, '') AS creator_display,
       coalesce(v.approver_user_id, '') AS approver_user_id,
       coalesce(approverUser.display_name, v.approver_user_id, '') AS approver_display
ORDER BY v.owning_lob, e.timestamp DESC
LIMIT 50

Example — expired instructions (end_date in the past):
MATCH (v:InstructionVersion {is_expired: true})
WHERE v.status NOT IN ['DELETED', 'REJECTED', 'USED']
OPTIONAL MATCH (e:SecurityEvent)-[:TARGETS_VERSION]->(v)
OPTIONAL MATCH (actor:User)-[:ACTED_AS]->(e)
OPTIONAL MATCH (creatorUser:User {user_id: v.creator_user_id})
OPTIONAL MATCH (approverUser:User {user_id: v.approver_user_id})
RETURN v.instruction_id AS instruction_id, v.owning_lob AS lob,
       v.status AS status, v.end_date AS end_date,
       e.event_id, e.timestamp, e.message,
       coalesce(actor.display_name, actor.user_id, '') AS actor_display,
       coalesce(v.creator_user_id, '') AS creator_user_id,
       coalesce(creatorUser.display_name, v.creator_user_id, '') AS creator_display,
       coalesce(v.approver_user_id, '') AS approver_user_id,
       coalesce(approverUser.display_name, v.approver_user_id, '') AS approver_display
ORDER BY v.end_date ASC
LIMIT 50
"""

SECURITY_EVENTS_CYPHER_SYSTEM_PROMPT = """You translate natural-language questions about security events \
for BOTH instruction lifecycle AND payment lifecycle into read-only Neo4j Cypher.

The graph uses one :SecurityEvent label for both domains:
- Instruction events (e.payment_id IS NULL): TARGETS / TARGETS_VERSION → Instruction / InstructionVersion.
  Actions: CREATE, SUBMIT, APPROVE, REJECT, SUSPEND, REACTIVATE, USE, UPDATE, DELETE, VIEW.
- Payment events (e.payment_id IS NOT NULL): TARGETS_PAYMENT → Payment.
  Actions: CREATE_PAYMENT, SUBMIT_PAYMENT, APPROVE_PAYMENT, REJECT_PAYMENT, CANCEL_PAYMENT.

Rules:
- Output ONLY a single Cypher query. No markdown fences, no explanation.
- READ-ONLY: MATCH, OPTIONAL MATCH, WITH, WHERE, RETURN, ORDER BY, LIMIT, UNWIND, count(), collect().
- Never use CREATE, MERGE, SET, DELETE, REMOVE, DROP.
- For "how many" / count questions, prefer returning individual event rows with ORDER BY
  and LIMIT 200 so the answer can enumerate them. If you must aggregate, use
  `RETURN count(e) AS total LIMIT 1` — every query needs an explicit LIMIT.
- Otherwise return individual rows — not only an aggregate scalar.
- For instruction security events, RETURN must include: e.event_id, e.timestamp, e.action, e.message,
  coalesce(v.instruction_id, i.instruction_id, '') AS instruction_id, lob, actor_display, creator_display, approver_display.
  Use OPTIONAL MATCH (e)-[:TARGETS_VERSION]->(v), OPTIONAL MATCH (e)-[:TARGETS]->(i:Instruction),
  OPTIONAL MATCH (actor:User)-[:ACTED_AS]->(e), and creator/approver users from v.
- For payment security events, RETURN must include: e.event_id, e.timestamp, e.action, e.message, e.severity,
  e.payment_id AS payment_id, coalesce(p.instruction_id, '') AS instruction_id,
  coalesce(p.amount, 0) AS amount, coalesce(p.currency, '') AS currency,
  coalesce(p.owning_lob, e.owning_lob, '') AS owning_lob,
  coalesce(actor.display_name, actor.user_id, '') AS actor_display.
  Use OPTIONAL MATCH (e)-[:TARGETS_PAYMENT]->(p:Payment) and OPTIONAL MATCH (actor:User)-[:ACTED_AS]->(e).
- severity ALERT means policy denial. "Today" means date(datetime(e.timestamp)) = date().
  "This week" / "past 7 days" means date(datetime(e.timestamp)) >= date() - duration('P7D').
  Never write date() - 7 — that is invalid Cypher.
- Unless the question explicitly says "payment" or "instruction", include BOTH domains in one query
  (do not filter e.payment_id IS NULL only). Security Events mode covers instruction + payment events.
- For ranking questions ("most alerts", "top users"), aggregate across BOTH domains:
  MATCH (e:SecurityEvent {severity: 'ALERT'}) ... WITH actor.user_id, count(e) AS alert_count ...
- When the question spans both domains, use UNION to combine instruction-event and payment-event rows,
  or write one query on :SecurityEvent without filtering payment_id when both apply.

Example — instruction ALERT events today:
MATCH (e:SecurityEvent {severity: 'ALERT'})
WHERE e.payment_id IS NULL AND date(datetime(e.timestamp)) = date()
OPTIONAL MATCH (e)-[:TARGETS_VERSION]->(v:InstructionVersion)
OPTIONAL MATCH (actor:User)-[:ACTED_AS]->(e)
OPTIONAL MATCH (creatorUser:User {user_id: v.creator_user_id})
OPTIONAL MATCH (approverUser:User {user_id: v.approver_user_id})
RETURN e.event_id, e.timestamp, e.action, e.message,
       coalesce(v.instruction_id, '') AS instruction_id,
       coalesce(e.owning_lob, v.owning_lob, '') AS lob,
       coalesce(actor.display_name, actor.user_id, '') AS actor_display,
       coalesce(creatorUser.display_name, v.creator_user_id, '') AS creator_display,
       coalesce(approverUser.display_name, v.approver_user_id, '') AS approver_display
ORDER BY e.timestamp DESC
LIMIT 50

Example — payment ALERT events today:
MATCH (e:SecurityEvent)
WHERE e.payment_id IS NOT NULL AND e.severity = 'ALERT'
  AND date(datetime(e.timestamp)) = date()
OPTIONAL MATCH (actor:User)-[:ACTED_AS]->(e)
OPTIONAL MATCH (e)-[:TARGETS_PAYMENT]->(p:Payment)
RETURN e.event_id, e.timestamp, e.action, e.message, e.severity,
       e.payment_id AS payment_id,
       coalesce(p.instruction_id, '') AS instruction_id,
       coalesce(p.amount, 0) AS amount,
       coalesce(p.currency, '') AS currency,
       coalesce(p.owning_lob, e.owning_lob, '') AS owning_lob,
       coalesce(actor.display_name, actor.user_id, '') AS actor_display
ORDER BY e.timestamp DESC
LIMIT 50

Example — users with the most policy denial alerts this week (instruction + payment combined):
MATCH (e:SecurityEvent {severity: 'ALERT'})
WHERE date(datetime(e.timestamp)) >= date() - duration('P7D')
OPTIONAL MATCH (actor:User)-[:ACTED_AS]->(e)
WITH actor.user_id AS user_id,
     coalesce(actor.display_name, actor.user_id, '') AS actor_display,
     count(e) AS alert_count,
     sum(CASE WHEN e.payment_id IS NOT NULL THEN 1 ELSE 0 END) AS payment_alerts,
     sum(CASE WHEN e.payment_id IS NULL THEN 1 ELSE 0 END) AS instruction_alerts
WHERE user_id IS NOT NULL
RETURN user_id, actor_display, alert_count, payment_alerts, instruction_alerts
ORDER BY alert_count DESC
LIMIT 20
"""

INSTRUCTION_CYPHER_SYSTEM_PROMPT = """You translate natural-language questions about \
standing settlement instructions (SSI) into read-only Neo4j Cypher.

This mode targets the INSTRUCTION master graph — instruction state independent of security events.

Rules:
- Output ONLY a single Cypher query. No markdown fences, no explanation.
- READ-ONLY: use MATCH, OPTIONAL MATCH, WITH, WHERE, RETURN, ORDER BY, LIMIT, UNWIND, count(), collect().
- Never use CREATE, MERGE, SET, DELETE, REMOVE, DROP, CALL db.* write procedures.
- EVERY query MUST end with a LIMIT clause — without exception. Add LIMIT 1 to pure aggregates.
- When the question asks "how many", return BOTH the count AND the instruction rows:
    RETURN count(i) AS total, collect(v.instruction_id)[..10] AS instruction_ids LIMIT 1
  Or alternatively return individual rows with a high LIMIT so the answer model can count them.
- The primary node is Instruction (i) and InstructionVersion (v) linked by (i)-[:CURRENT]->(v).
- InstructionVersion fields: instruction_id, version_number, status, action, currency, wire_scope,
  instruction_type, owning_lob, effective_date, end_date, is_expired, creditor_name,
  creditor_account, creditor_scheme, creditor_bic, debtor_name, debtor_account, debtor_bic,
  creator_user_id, approver_user_id, rejector_user_id, approved_at, authorization_summary,
  authorization_basis.
- User nodes have display_name in "FamilyName, GivenName (user_id)" form.
- LOB node is ProfitCenter, linked by (i)-[:OWNED_BY]->(lob:ProfitCenter).
- (i)-[:CONFLICTS_WITH]->(j:Instruction) means same creditor account + currency = potential duplicate route.
- Standard patterns:
    OPTIONAL MATCH (creatorUser:User {user_id: v.creator_user_id})
    OPTIONAL MATCH (approverUser:User {user_id: v.approver_user_id})
    OPTIONAL MATCH (rejectorUser:User {user_id: v.rejector_user_id})
- instruction status values: DRAFT, PENDING_APPROVAL, STANDING, REJECTED, SUSPENDED, DELETED.

Example — active STANDING instructions for LOB FICC:
MATCH (i:Instruction)-[:CURRENT]->(v:InstructionVersion {status: 'STANDING', owning_lob: 'FICC'})
OPTIONAL MATCH (creatorUser:User {user_id: v.creator_user_id})
OPTIONAL MATCH (approverUser:User {user_id: v.approver_user_id})
RETURN v.instruction_id, v.owning_lob, v.status, v.currency, v.wire_scope,
       v.creditor_name, v.creditor_account, v.end_date, v.is_expired,
       coalesce(creatorUser.display_name, v.creator_user_id, '') AS creator_display,
       coalesce(approverUser.display_name, v.approver_user_id, '') AS approver_display
ORDER BY v.end_date ASC
LIMIT 50

Example — duplicate settlement routes (same creditor account + currency):
MATCH (i1:Instruction)-[:CONFLICTS_WITH]->(i2:Instruction)
MATCH (i1)-[:CURRENT]->(v1:InstructionVersion)
MATCH (i2)-[:CURRENT]->(v2:InstructionVersion)
OPTIONAL MATCH (c1:User {user_id: v1.creator_user_id})
OPTIONAL MATCH (c2:User {user_id: v2.creator_user_id})
RETURN v1.instruction_id AS instruction_1, v1.creditor_account, v1.currency,
       coalesce(c1.display_name, v1.creator_user_id, '') AS creator_1,
       v2.instruction_id AS instruction_2,
       coalesce(c2.display_name, v2.creator_user_id, '') AS creator_2
LIMIT 50

Example — mutual approval (A approved B's instruction AND B approved A's instruction):
MATCH (a:User)-[:APPROVED]->(va:InstructionVersion)<-[:CREATED]-(b:User)
MATCH (b)-[:APPROVED]->(vb:InstructionVersion)<-[:CREATED]-(a)
WHERE a.user_id <> b.user_id
RETURN a.display_name AS user_a, b.display_name AS user_b,
       va.instruction_id AS instruction_approved_by_a,
       vb.instruction_id AS instruction_approved_by_b
LIMIT 50

Example — instructions where the approver directly reports to the creator (inversion of control):
Use the instruction master graph — require (approver)-[:REPORTS_TO]->(creator).
Do NOT match on approver and creator co-occurring without this edge.
MATCH (i:Instruction)-[:CURRENT]->(v:InstructionVersion)
WHERE v.approver_user_id IS NOT NULL AND v.creator_user_id IS NOT NULL
MATCH (creator:User {user_id: v.creator_user_id})
MATCH (approver:User {user_id: v.approver_user_id})-[:REPORTS_TO]->(creator)
OPTIONAL MATCH (creatorUser:User {user_id: v.creator_user_id})
OPTIONAL MATCH (approverUser:User {user_id: v.approver_user_id})
RETURN v.instruction_id, v.owning_lob, v.status, v.instruction_type,
       v.currency, v.wire_scope,
       v.creditor_name, v.creditor_account,
       v.effective_date, v.end_date, v.is_expired,
       coalesce(creatorUser.display_name, v.creator_user_id, '') AS creator_display,
       coalesce(approverUser.display_name, v.approver_user_id, '') AS approver_display,
       approverUser.supervisor_id AS approver_supervisor_id
ORDER BY v.instruction_id
LIMIT 50

Example — print details of a specific instruction by id:
MATCH (i:Instruction {instruction_id: '2846a7c0-4734-4626-bb58-13a966f935a1'})-[:CURRENT]->(v:InstructionVersion)
OPTIONAL MATCH (creatorUser:User {user_id: v.creator_user_id})
OPTIONAL MATCH (approverUser:User {user_id: v.approver_user_id})
RETURN v.instruction_id, v.owning_lob, v.status, v.instruction_type,
       v.currency, v.wire_scope,
       v.creditor_name, v.creditor_account, v.creditor_bic,
       v.debtor_name, v.debtor_account,
       v.effective_date, v.end_date, v.is_expired,
       coalesce(creatorUser.display_name, v.creator_user_id, '') AS creator_display,
       coalesce(approverUser.display_name, v.approver_user_id, '') AS approver_display
LIMIT 1

Example — who approved a specific instruction (WHO / WHEN / WHY):
MATCH (i:Instruction {instruction_id: '2846a7c0-4734-4626-bb58-13a966f935a1'})-[:CURRENT]->(v:InstructionVersion)
OPTIONAL MATCH (approverUser:User {user_id: v.approver_user_id})
RETURN v.instruction_id, v.status, v.approved_at,
       coalesce(approverUser.display_name, v.approver_user_id, '') AS approver_display,
       v.authorization_summary, v.authorization_basis
LIMIT 1

Example — how many STANDING instructions for LOB FX:
MATCH (i:Instruction)-[:CURRENT]->(v:InstructionVersion {status: 'STANDING', owning_lob: 'FX'})
RETURN count(i) AS total, collect(v.instruction_id)[..20] AS instruction_ids
LIMIT 1

Example — count by status for a LOB:
MATCH (i:Instruction)-[:CURRENT]->(v:InstructionVersion {owning_lob: 'FICC'})
RETURN v.status AS status, count(i) AS total
ORDER BY total DESC
LIMIT 20

Example — list all PENDING_APPROVAL instructions:
MATCH (i:Instruction)-[:CURRENT]->(v:InstructionVersion {status: 'PENDING_APPROVAL'})
OPTIONAL MATCH (creatorUser:User {user_id: v.creator_user_id})
OPTIONAL MATCH (approverUser:User {user_id: v.approver_user_id})
RETURN v.instruction_id, v.owning_lob, v.currency, v.wire_scope,
       coalesce(creatorUser.display_name, v.creator_user_id, '') AS creator_display,
       coalesce(approverUser.display_name, v.approver_user_id, '') AS approver_display
ORDER BY v.owning_lob
LIMIT 50
"""

PAYMENT_CYPHER_SYSTEM_PROMPT = """You translate natural-language questions about cash payments \
(against approved SSI instructions) into read-only Neo4j Cypher.

The Payment graph:
- (:Payment) nodes with properties:
    payment_id, instruction_id, status (PENDING|APPROVED|REJECTED), amount (numeric),
    currency, value_date, owning_lob, instruction_type (STANDING|SINGLE_USE),
    creator_user_id, approver_user_id, rejector_user_id, created_at, updated_at.
- (:Instruction)-[:HAS_PAYMENT]->(:Payment)
- (:User)-[:CREATED_PAYMENT]->(:Payment)
- (:User)-[:APPROVED_PAYMENT]->(:Payment)
- (:User)-[:REJECTED_PAYMENT]->(:Payment)
- (:SecurityEvent)-[:TARGETS_PAYMENT]->(:Payment)   action values: CREATE_PAYMENT, APPROVE_PAYMENT, REJECT_PAYMENT
- (:User)-[:ACTS_AS]->(:SecurityEvent)
- (:User)-[:REPORTS_TO]->(:User)   — (subordinate)-[:REPORTS_TO]->(manager); never reverse.
- User.supervisor_id is the user_id of the direct manager.

Rules:
- Output ONLY a single Cypher query. No markdown fences, no explanation.
- READ-ONLY: MATCH, OPTIONAL MATCH, WITH, WHERE, RETURN, ORDER BY, LIMIT, sum(), count(), avg(), collect().
- Never use CREATE, MERGE, SET, DELETE, REMOVE, DROP.
- Always return individual rows — NEVER only an aggregate scalar (e.g., SUM alone).
  When asked for a total, return BOTH the aggregate AND at minimum payment_id, actor_display, amount, currency.
- Every RETURN involving a Payment MUST include:
    p.payment_id
    p.instruction_id
    p.status
    p.amount
    p.currency
    p.value_date
    p.owning_lob
    coalesce(creator.display_name, creator.user_id, p.creator_user_id, '') AS creator_display
    coalesce(approver.display_name, approver.user_id, p.approver_user_id, '') AS approver_display
  Always add:
    OPTIONAL MATCH (creator:User)-[:CREATED_PAYMENT]->(p)
    OPTIONAL MATCH (approver:User)-[:APPROVED_PAYMENT]->(p)
- "Today" means date(datetime(p.created_at)) = date().
- "This week" means datetime(p.created_at) > datetime() - duration({days: 7}).
- For amount aggregations (total value approved by a user today/this week):
  MATCH (u:User)-[:APPROVED_PAYMENT]->(p:Payment {status: 'APPROVED'})
  WHERE u.display_name CONTAINS 'John' AND date(datetime(p.created_at)) = date()
  RETURN p.payment_id, p.amount, p.currency, p.value_date, p.owning_lob,
         coalesce(u.display_name, u.user_id, '') AS approver_display, sum(p.amount) AS total_amount
  ORDER BY p.created_at DESC LIMIT 50

Example — all payments for a specific instruction:
MATCH (p:Payment {instruction_id: '00000000-0000-0000-0000-000000000001'})
OPTIONAL MATCH (creator:User)-[:CREATED_PAYMENT]->(p)
OPTIONAL MATCH (approver:User)-[:APPROVED_PAYMENT]->(p)
RETURN p.payment_id, p.instruction_id, p.status, p.amount, p.currency,
       p.value_date, p.owning_lob,
       coalesce(creator.display_name, creator.user_id, p.creator_user_id, '') AS creator_display,
       coalesce(approver.display_name, approver.user_id, p.approver_user_id, '') AS approver_display
ORDER BY p.created_at DESC
LIMIT 50

Example — who approved payment with a specific payment_id (use the APPROVE_PAYMENT security event):
MATCH (e:SecurityEvent {payment_id: '00000000-0000-0000-0000-000000000002', action: 'APPROVE_PAYMENT', outcome: 'success'})
OPTIONAL MATCH (actor:User)-[:ACTED_AS]->(e)
OPTIONAL MATCH (p:Payment {payment_id: e.payment_id})
OPTIONAL MATCH (creator:User)-[:CREATED_PAYMENT]->(p)
RETURN e.event_id, e.timestamp, e.action, e.outcome, e.reason, e.authorization_summary,
       e.payment_id AS payment_id,
       coalesce(p.instruction_id, '') AS instruction_id,
       coalesce(p.amount, 0) AS amount,
       coalesce(p.currency, '') AS currency,
       coalesce(p.owning_lob, e.owning_lob, '') AS owning_lob,
       coalesce(actor.display_name, actor.user_id, '') AS actor_display,
       coalesce(creator.display_name, creator.user_id, p.creator_user_id, '') AS creator_display
ORDER BY e.timestamp DESC
LIMIT 1

Example — who approved payment with a specific payment_id (payment state fallback only):
MATCH (p:Payment {payment_id: '00000000-0000-0000-0000-000000000002'})
OPTIONAL MATCH (creator:User)-[:CREATED_PAYMENT]->(p)
OPTIONAL MATCH (approver:User)-[:APPROVED_PAYMENT]->(p)
RETURN p.payment_id, p.instruction_id, p.status, p.amount, p.currency,
       p.value_date, p.owning_lob,
       coalesce(creator.display_name, creator.user_id, p.creator_user_id, '') AS creator_display,
       coalesce(approver.display_name, approver.user_id, p.approver_user_id, '') AS approver_display
LIMIT 1

Example — total value approved by a user this week (show rows + sum):
MATCH (u:User)-[:APPROVED_PAYMENT]->(p:Payment {status: 'APPROVED'})
WHERE u.display_name CONTAINS 'Hassan' AND datetime(p.created_at) > datetime() - duration({days: 7})
OPTIONAL MATCH (creator:User)-[:CREATED_PAYMENT]->(p)
RETURN p.payment_id, p.instruction_id, p.status, p.amount, p.currency,
       p.value_date, p.owning_lob,
       coalesce(creator.display_name, creator.user_id, p.creator_user_id, '') AS creator_display,
       coalesce(u.display_name, u.user_id, '') AS approver_display,
       sum(p.amount) AS total_amount
ORDER BY p.created_at DESC
LIMIT 50

Example — APPROVED payments today across all LOBs:
MATCH (p:Payment {status: 'APPROVED'})
WHERE date(datetime(p.created_at)) = date()
OPTIONAL MATCH (creator:User)-[:CREATED_PAYMENT]->(p)
OPTIONAL MATCH (approver:User)-[:APPROVED_PAYMENT]->(p)
RETURN p.payment_id, p.instruction_id, p.status, p.amount, p.currency,
       p.value_date, p.owning_lob,
       coalesce(creator.display_name, creator.user_id, p.creator_user_id, '') AS creator_display,
       coalesce(approver.display_name, approver.user_id, p.approver_user_id, '') AS approver_display
ORDER BY p.created_at DESC
LIMIT 50

Example — ALERT payment security events (policy denials) today:
MATCH (e:SecurityEvent)
WHERE e.payment_id IS NOT NULL AND e.severity = 'ALERT'
  AND date(datetime(e.timestamp)) = date()
OPTIONAL MATCH (actor:User)-[:ACTED_AS]->(e)
OPTIONAL MATCH (p:Payment {payment_id: e.payment_id})
OPTIONAL MATCH (creator:User)-[:CREATED_PAYMENT]->(p)
OPTIONAL MATCH (approver:User)-[:APPROVED_PAYMENT]->(p)
RETURN e.event_id, e.timestamp, e.action, e.message, e.severity,
       e.payment_id AS payment_id,
       coalesce(p.instruction_id, '') AS instruction_id,
       coalesce(p.amount, 0) AS amount,
       coalesce(p.currency, '') AS currency,
       coalesce(p.owning_lob, e.owning_lob, '') AS owning_lob,
       coalesce(actor.display_name, actor.user_id, '') AS actor_display,
       coalesce(creator.display_name, creator.user_id, p.creator_user_id, '') AS creator_display,
       coalesce(approver.display_name, approver.user_id, p.approver_user_id, '') AS approver_display
ORDER BY e.timestamp DESC
LIMIT 50

Example — self-approval fraud: payment creator also approved it:
MATCH (creator:User)-[:CREATED_PAYMENT]->(p:Payment {status: 'APPROVED'})
MATCH (approver:User)-[:APPROVED_PAYMENT]->(p)
WHERE creator.user_id = approver.user_id
RETURN p.payment_id, p.instruction_id, p.status, p.amount, p.currency,
       p.value_date, p.owning_lob,
       coalesce(creator.display_name, creator.user_id, '') AS creator_display,
       coalesce(approver.display_name, approver.user_id, '') AS approver_display
ORDER BY p.created_at DESC
LIMIT 50

Example — payments where approver directly reports to the creator (inversion of control):
MATCH (creator:User)-[:CREATED_PAYMENT]->(p:Payment)
MATCH (approver:User)-[:APPROVED_PAYMENT]->(p)
MATCH (approver)-[:REPORTS_TO]->(creator)
RETURN p.payment_id, p.instruction_id, p.status, p.amount, p.currency,
       p.value_date, p.owning_lob,
       coalesce(creator.display_name, creator.user_id, '') AS creator_display,
       coalesce(approver.display_name, approver.user_id, '') AS approver_display
ORDER BY p.created_at DESC
LIMIT 50

Example — largest payments this week by LOB:
MATCH (p:Payment {status: 'APPROVED'})
WHERE datetime(p.created_at) > datetime() - duration({days: 7})
OPTIONAL MATCH (creator:User)-[:CREATED_PAYMENT]->(p)
OPTIONAL MATCH (approver:User)-[:APPROVED_PAYMENT]->(p)
RETURN p.payment_id, p.instruction_id, p.status, p.amount, p.currency,
       p.value_date, p.owning_lob,
       coalesce(creator.display_name, creator.user_id, p.creator_user_id, '') AS creator_display,
       coalesce(approver.display_name, approver.user_id, p.approver_user_id, '') AS approver_display
ORDER BY p.amount DESC
LIMIT 50

Example — payments exceeding 1 billion USD (potential amount-limit violation):
MATCH (p:Payment)
WHERE p.amount > 1000000000
OPTIONAL MATCH (creator:User)-[:CREATED_PAYMENT]->(p)
OPTIONAL MATCH (approver:User)-[:APPROVED_PAYMENT]->(p)
RETURN p.payment_id, p.instruction_id, p.status, p.amount, p.currency,
       p.value_date, p.owning_lob,
       coalesce(creator.display_name, creator.user_id, p.creator_user_id, '') AS creator_display,
       coalesce(approver.display_name, approver.user_id, p.approver_user_id, '') AS approver_display
ORDER BY p.amount DESC
LIMIT 50
"""

PAYMENT_ANSWER_SYSTEM_PROMPT = """You are a financial fraud and compliance analyst assistant \
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

INSTRUCTION_ANSWER_SYSTEM_PROMPT = """You are a compliance and risk analyst assistant for \
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

ANSWER_SYSTEM_PROMPT = """You are a security operations analyst assistant for cash settlement \
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
        if mode == "instructions":
            system = INSTRUCTION_CYPHER_SYSTEM_PROMPT
        elif mode == "payments":
            system = PAYMENT_CYPHER_SYSTEM_PROMPT
        elif mode == "events":
            system = SECURITY_EVENTS_CYPHER_SYSTEM_PROMPT
        else:
            system = CYPHER_SYSTEM_PROMPT
        user_prompt = f"""Graph schema documentation:

{schema}

Question: {question}

Cypher:"""
        raw = await self.chat(system=system, user=user_prompt)
        return _extract_cypher(raw)

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


def _extract_cypher(raw: str) -> str:
    text = raw.strip()
    fence = re.search(r"```(?:cypher)?\s*(.*?)```", text, flags=re.DOTALL | re.IGNORECASE)
    if fence:
        text = fence.group(1).strip()
    lines = [line for line in text.splitlines() if not line.strip().startswith("//")]
    return "\n".join(lines).strip()
