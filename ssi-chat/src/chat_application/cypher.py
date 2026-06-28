from __future__ import annotations

import logging
import re
from typing import Any

from chat_application.config import settings

logger = logging.getLogger(__name__)

# ── Cypher validation patterns ─────────────────────────────────────────────

# Strip comment styles before keyword analysis
_LINE_COMMENT = re.compile(r"//[^\n]*", re.MULTILINE)
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)

# Replace string literal content with empty placeholders so keywords inside
# quoted values (e.g. WHERE n.name = 'DELETE') don't trigger false positives
_STRING_LITERAL = re.compile(r"'(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\"")

# Cypher DML/DDL keywords that must never appear in a read query
_WRITE_KEYWORD = re.compile(
    r"\b(CREATE|MERGE|SET|DELETE|REMOVE|DROP|DETACH|FOREACH|LOAD)\b",
    re.IGNORECASE,
)

# CALL to built-in or APOC write-capable procedures
_WRITE_PROCEDURE = re.compile(
    r"\bCALL\s+(db\.\w+|apoc\.create\.|apoc\.periodic\.|apoc\.merge\.|apoc\.refactor\.)",
    re.IGNORECASE,
)

# Valid first clause for a read-only query
_READ_START = re.compile(
    r"^\s*(MATCH|OPTIONAL\s+MATCH|WITH|RETURN|UNWIND)\b",
    re.IGNORECASE,
)

# Require an explicit upper bound
_LIMIT_CLAUSE = re.compile(r"\bLIMIT\s+\d+\b", re.IGNORECASE)

_AGGREGATE_RETURN = re.compile(
    r"\bRETURN\b.*\b(count|sum|avg|min|max)\s*\(",
    re.IGNORECASE | re.DOTALL,
)

_COUNT_QUESTION = re.compile(
    r"\b(how many|number of|count of|total number)\b",
    re.IGNORECASE,
)

_RANKING_QUESTION = re.compile(
    r"\b(most|top|highest|greatest|largest|biggest|who triggered|which user|which users)\b",
    re.IGNORECASE,
)

_MAX_PAYMENTS_PER_INSTRUCTION = re.compile(
    r"\bwhich instruction\b.*\bpayments?\b|"
    r"\binstruction\b.*\b(maximum|max|most|highest|largest|greatest|biggest)\b.*\bpayments?\b|"
    r"\b(maximum|max|most|highest|largest|greatest|biggest)\b.*\bpayments?\b.*\binstruction\b",
    re.IGNORECASE,
)

_LIST_PAYMENTS_FOR_INSTRUCTION = re.compile(
    r"\b(list|show|enumerate|display)\b.*\bpayments?\b|"
    r"\bpayments?\s+for\s+instruction\b",
    re.IGNORECASE,
)

_INSTRUCTION_UUID_IN_QUESTION = re.compile(
    r"instruction\s+([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})",
    re.IGNORECASE,
)

_PAYMENT_STATUSES = ("APPROVED", "SUBMITTED", "REJECTED", "DRAFT", "CANCELLED", "PENDING")

_DENIAL_QUESTION = re.compile(
    r"\b(policy denial|denials?|denied|alert|alerts)\b",
    re.IGNORECASE,
)

_WEEK_QUESTION = re.compile(
    r"\b(this week|past week|last week|last 7 days|past 7 days)\b",
    re.IGNORECASE,
)

_HIERARCHY_QUESTION = re.compile(
    r"\b(reports?\s+to|reporting\s+to|directly\s+reports?|subordinate|supervisor|"
    r"inversion\s+of\s+control|reporting\s+chain|hierarchy)\b",
    re.IGNORECASE,
)

# UUID pattern for exact-lookup detection
_UUID_PATTERN = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    re.IGNORECASE,
)

_MAX_CYPHER_LEN = 4096

# ── Fixed parametric queries ───────────────────────────────────────────────

LOOKUP_INSTRUCTION_BY_EVENT_CYPHER = """MATCH (e:SecurityEvent {event_id: $event_id})
OPTIONAL MATCH (e)-[:TARGETS]->(i:Instruction)
OPTIONAL MATCH (e)-[:TARGETS_VERSION]->(v:InstructionVersion)
RETURN e.event_id AS event_id,
       coalesce(i.instruction_id, v.instruction_id) AS instruction_id
LIMIT 1"""


def load_graph_schema() -> str:
    path = settings.graph_schema_path
    if path.is_file():
        return path.read_text(encoding="utf-8")
    logger.warning("graph schema file not found: %s", path)
    return ""


def normalize_read_only_cypher(cypher: str) -> str:
    """Append LIMIT 1 to aggregate-only queries that omit an explicit LIMIT."""
    stripped = cypher.strip()
    if not stripped:
        return stripped

    normalized = _LINE_COMMENT.sub("", stripped)
    normalized = _BLOCK_COMMENT.sub("", normalized)
    no_strings = _STRING_LITERAL.sub("''", normalized)

    if _LIMIT_CLAUSE.search(no_strings):
        return stripped
    if _AGGREGATE_RETURN.search(no_strings):
        return stripped.rstrip(";") + "\nLIMIT 1"
    return stripped


def is_count_question(question: str) -> bool:
    return bool(_COUNT_QUESTION.search(question))


def is_max_payments_per_instruction_question(question: str) -> bool:
    q = question.lower()
    return "instruction" in q and "payment" in q and bool(_MAX_PAYMENTS_PER_INSTRUCTION.search(question))


def is_payments_for_instruction_question(question: str) -> bool:
    q = question.lower()
    if not extract_uuids(question):
        return False
    if is_max_payments_per_instruction_question(question):
        return False
    if "approv" in q and ("who" in q or "when" in q or "why" in q):
        return False
    if "payment" not in q or "instruction" not in q:
        return False
    return bool(_LIST_PAYMENTS_FOR_INSTRUCTION.search(question))


def instruction_id_from_list_payments_question(question: str) -> str | None:
    match = _INSTRUCTION_UUID_IN_QUESTION.search(question)
    if match:
        return match.group(1)
    uuids = extract_uuids(question)
    return uuids[0] if uuids else None


def payment_status_filter_from_question(question: str) -> str | None:
    upper = question.upper()
    for status in _PAYMENT_STATUSES:
        if status in upper:
            return status
    return None


def is_alert_ranking_question(question: str, *, mode: str) -> bool:
    if mode != "events":
        return False
    flags = _question_flags(question)
    return (
        flags["ranking"]
        and flags["denial"]
        and (flags["alerts"] or flags["denial"])
    )


def ranking_period_label(question: str) -> str:
    flags = _question_flags(question)
    if flags["today"]:
        return "today"
    if flags["week"]:
        return "this week"
    return "all time"


def _question_flags(question: str) -> dict[str, bool]:
    q = question.lower()
    return {
        "count": is_count_question(question),
        "ranking": bool(_RANKING_QUESTION.search(question)),
        "denial": bool(_DENIAL_QUESTION.search(question)),
        "today": "today" in q,
        "week": bool(_WEEK_QUESTION.search(question)),
        "alerts": "alert" in q,
        "payments": "payment" in q,
        "instructions": "instruction" in q and "payment" not in q,
    }


def _time_filter_cypher(flags: dict[str, bool]) -> str:
    if flags["today"]:
        return "AND date(datetime(e.timestamp)) = date()"
    if flags["week"]:
        return "AND date(datetime(e.timestamp)) >= date() - duration('P7D')"
    return ""


def _alert_ranking_queries(
    *,
    time_filter: str,
    payments_only: bool = False,
    instructions_only: bool = False,
) -> list[tuple[str, str]]:
    domain_filter = ""
    if payments_only:
        domain_filter = "AND e.payment_id IS NOT NULL"
    elif instructions_only:
        domain_filter = "AND e.payment_id IS NULL"

    return [
        (
            "ranking",
            f"""MATCH (e:SecurityEvent {{severity: 'ALERT'}})
WHERE true {domain_filter} {time_filter}
OPTIONAL MATCH (actor:User)-[:ACTED_AS]->(e)
WITH actor.user_id AS user_id,
     coalesce(actor.display_name, actor.user_id, '') AS actor_display,
     count(e) AS alert_count,
     sum(CASE WHEN e.payment_id IS NOT NULL THEN 1 ELSE 0 END) AS payment_alerts,
     sum(CASE WHEN e.payment_id IS NULL THEN 1 ELSE 0 END) AS instruction_alerts
WHERE user_id IS NOT NULL
RETURN user_id, actor_display, alert_count, payment_alerts, instruction_alerts
ORDER BY alert_count DESC
LIMIT 20""",
        ),
        (
            "details",
            f"""MATCH (e:SecurityEvent {{severity: 'ALERT'}})
WHERE true {domain_filter} {time_filter}
OPTIONAL MATCH (actor:User)-[:ACTED_AS]->(e)
OPTIONAL MATCH (e)-[:TARGETS_PAYMENT]->(p:Payment)
OPTIONAL MATCH (e)-[:TARGETS_VERSION]->(v:InstructionVersion)
RETURN e.event_id, e.timestamp, e.action, e.message, e.severity,
       CASE WHEN e.payment_id IS NOT NULL THEN 'payment' ELSE 'instruction' END AS domain,
       coalesce(e.payment_id, '') AS payment_id,
       coalesce(p.instruction_id, v.instruction_id, '') AS instruction_id,
       coalesce(p.amount, 0) AS amount,
       coalesce(p.currency, '') AS currency,
       coalesce(p.owning_lob, e.owning_lob, v.owning_lob, '') AS owning_lob,
       coalesce(actor.display_name, actor.user_id, '') AS actor_display
ORDER BY e.timestamp DESC
LIMIT 200""",
        ),
    ]


def _is_subordinate_approver_question(question: str) -> bool:
    """Approver directly reports to the instruction creator (inversion-of-control)."""
    q = question.lower()
    if "approv" not in q:
        return False
    if not _HIERARCHY_QUESTION.search(question):
        return False
    return any(
        token in q
        for token in (
            "creator",
            "created",
            "supervisor",
            "subordinate",
            "reports to",
            "reporting to",
            "directly report",
        )
    )


def _is_instruction_approval_lookup(question: str) -> bool:
    q = question.lower()
    if "approv" not in q:
        return False
    if "payment" in q and "instruction" not in q:
        return False
    return bool(_UUID_PATTERN.search(question)) or "instruction" in q


def _is_payment_approval_lookup(question: str, *, mode: str) -> bool:
    q = question.lower()
    if "approv" not in q:
        return False
    if "instruction" in q and "payment" not in q:
        return False
    if not _UUID_PATTERN.search(question):
        return False
    return "payment" in q or mode == "payments"


def _instruction_approval_lookup_queries(instruction_id: str) -> list[tuple[str, str]]:
    return [
        (
            "approval_lookup",
            f"""MATCH (i:Instruction {{instruction_id: '{instruction_id}'}})-[:CURRENT]->(v:InstructionVersion)
OPTIONAL MATCH (approverUser:User {{user_id: v.approver_user_id}})
RETURN v.instruction_id, v.status, v.approved_at,
       coalesce(approverUser.display_name, v.approver_user_id, '') AS approver_display,
       v.authorization_summary, v.authorization_basis
LIMIT 1""",
        ),
    ]


def _payments_for_instruction_queries(
    instruction_id: str,
    *,
    status: str | None = None,
) -> list[tuple[str, str]]:
    status_filter = f"AND p.status = '{status}'" if status else ""
    return [
        (
            "payments_for_instruction",
            f"""MATCH (i:Instruction {{instruction_id: '{instruction_id}'}})-[:HAS_PAYMENT]->(p:Payment)
WHERE true {status_filter}
WITH collect(DISTINCT p) AS payments
UNWIND payments AS p
OPTIONAL MATCH (creator:User)-[:CREATED_PAYMENT]->(p)
OPTIONAL MATCH (approver:User)-[:APPROVED_PAYMENT]->(p)
WITH p,
     head(collect(DISTINCT creator)) AS creator,
     head(collect(DISTINCT approver)) AS approver
RETURN p.payment_id AS payment_id,
       p.instruction_id AS instruction_id,
       p.status AS status,
       p.amount AS amount,
       p.currency AS currency,
       p.value_date AS value_date,
       p.owning_lob AS owning_lob,
       p.created_at AS created_at,
       coalesce(creator.display_name, creator.user_id, p.creator_user_id, '') AS creator_display,
       coalesce(approver.display_name, approver.user_id, p.approver_user_id, '') AS approver_display
ORDER BY p.created_at ASC
LIMIT 200""",
        ),
    ]


def _max_payments_per_instruction_queries() -> list[tuple[str, str]]:
    """Instruction with the most payments, plus one row per payment."""
    return [
        (
            "max_payments_per_instruction",
            """MATCH (i:Instruction)-[:HAS_PAYMENT]->(p:Payment)
WITH i.instruction_id AS instruction_id, count(DISTINCT p) AS payment_count
ORDER BY payment_count DESC, instruction_id ASC
LIMIT 1
WITH instruction_id, payment_count
MATCH (i:Instruction {instruction_id: instruction_id})-[:HAS_PAYMENT]->(p:Payment)
WITH instruction_id, payment_count, p
ORDER BY p.created_at ASC
WITH instruction_id, payment_count, collect(DISTINCT p) AS payments
UNWIND payments AS p
OPTIONAL MATCH (creator:User)-[:CREATED_PAYMENT]->(p)
OPTIONAL MATCH (approver:User)-[:APPROVED_PAYMENT]->(p)
WITH instruction_id,
     payment_count,
     p,
     head(collect(DISTINCT creator)) AS creator,
     head(collect(DISTINCT approver)) AS approver
RETURN instruction_id,
       payment_count,
       p.payment_id AS payment_id,
       p.created_at AS created_at,
       coalesce(creator.display_name, creator.user_id, p.creator_user_id, '') AS creator_display,
       coalesce(approver.display_name, approver.user_id, p.approver_user_id, '') AS approver_display
ORDER BY created_at ASC
LIMIT 200""",
        ),
    ]


def _payment_approval_lookup_queries(payment_id: str) -> list[tuple[str, str]]:
    return [
        (
            "payment_approval_lookup",
            f"""MATCH (e:SecurityEvent)
WHERE e.payment_id = '{payment_id}'
  AND e.action = 'APPROVE_PAYMENT'
  AND e.outcome = 'success'
OPTIONAL MATCH (actor:User)-[:ACTED_AS]->(e)
RETURN e.payment_id AS payment_id,
       e.timestamp AS approved_at,
       coalesce(actor.display_name, actor.user_id, '') AS approver_display,
       e.authorization_summary AS authorization_summary,
       e.authorization_basis AS authorization_basis
ORDER BY e.timestamp DESC
LIMIT 1""",
        ),
    ]


def _instruction_subordinate_approver_queries() -> list[tuple[str, str]]:
    """Instructions where approver-[:REPORTS_TO]->creator on the current version."""
    return [
        (
            "hierarchy_violations",
            """MATCH (i:Instruction)-[:CURRENT]->(v:InstructionVersion)
WHERE v.approver_user_id IS NOT NULL AND v.creator_user_id IS NOT NULL
MATCH (creator:User {user_id: v.creator_user_id})
MATCH (approver:User {user_id: v.approver_user_id})-[:REPORTS_TO]->(creator)
RETURN v.instruction_id, v.owning_lob, v.status, v.instruction_type,
       v.currency, v.wire_scope,
       v.creditor_name, v.creditor_account,
       v.effective_date, v.end_date, v.is_expired,
       creator.user_id AS creator_user_id,
       coalesce(creator.display_name, creator.user_id, '') AS creator_display,
       approver.user_id AS approver_user_id,
       coalesce(approver.display_name, approver.user_id, '') AS approver_display,
       approver.supervisor_id AS approver_supervisor_id
ORDER BY v.instruction_id
LIMIT 50""",
        ),
    ]


def plan_graph_queries(question: str, *, mode: str) -> list[tuple[str, str]] | None:
    """Deterministic read-only Cypher for common aggregate questions."""
    flags = _question_flags(question)
    time_filter = _time_filter_cypher(flags)

    if mode == "instructions" and _is_subordinate_approver_question(question):
        return _instruction_subordinate_approver_queries()

    if mode == "instructions" and _is_instruction_approval_lookup(question):
        uuids = extract_uuids(question)
        if uuids:
            return _instruction_approval_lookup_queries(uuids[0])

    if mode in ("payments", "events") and _is_payment_approval_lookup(question, mode=mode):
        if not is_payments_for_instruction_question(question):
            uuids = extract_uuids(question)
            if uuids:
                return _payment_approval_lookup_queries(uuids[0])

    if mode in ("payments", "all") and is_payments_for_instruction_question(question):
        instruction_id = instruction_id_from_list_payments_question(question)
        if instruction_id:
            return _payments_for_instruction_queries(
                instruction_id,
                status=payment_status_filter_from_question(question),
            )

    if mode in ("payments", "all") and is_max_payments_per_instruction_question(question):
        return _max_payments_per_instruction_queries()

    if (
        mode == "events"
        and flags["ranking"]
        and flags["denial"]
        and (flags["alerts"] or flags["denial"])
    ):
        if flags["payments"]:
            return _alert_ranking_queries(time_filter=time_filter, payments_only=True)
        if flags["instructions"]:
            return _alert_ranking_queries(time_filter=time_filter, instructions_only=True)
        return _alert_ranking_queries(time_filter=time_filter)

    if not flags["count"]:
        return None

    if mode == "events" and flags["alerts"] and flags["payments"]:
        return [
            (
                "count",
                f"""MATCH (e:SecurityEvent)
WHERE e.payment_id IS NOT NULL AND e.severity = 'ALERT' {time_filter}
RETURN count(e) AS total LIMIT 1""",
            ),
            (
                "details",
                f"""MATCH (e:SecurityEvent)
WHERE e.payment_id IS NOT NULL AND e.severity = 'ALERT' {time_filter}
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
LIMIT 200""",
            ),
        ]

    if mode == "events" and flags["alerts"] and flags["instructions"]:
        return [
            (
                "count",
                f"""MATCH (e:SecurityEvent)
WHERE e.payment_id IS NULL AND e.severity = 'ALERT' {time_filter}
RETURN count(e) AS total LIMIT 1""",
            ),
            (
                "details",
                f"""MATCH (e:SecurityEvent)
WHERE e.payment_id IS NULL AND e.severity = 'ALERT' {time_filter}
OPTIONAL MATCH (e)-[:TARGETS_VERSION]->(v:InstructionVersion)
OPTIONAL MATCH (actor:User)-[:ACTED_AS]->(e)
RETURN e.event_id, e.timestamp, e.action, e.message, e.severity,
       coalesce(v.instruction_id, '') AS instruction_id,
       coalesce(e.owning_lob, v.owning_lob, '') AS lob,
       coalesce(actor.display_name, actor.user_id, '') AS actor_display
ORDER BY e.timestamp DESC
LIMIT 200""",
            ),
        ]

    if mode == "events" and flags["alerts"] and not flags["payments"] and not flags["instructions"]:
        return [
            (
                "count",
                f"""MATCH (e:SecurityEvent {{severity: 'ALERT'}})
WHERE true {time_filter}
RETURN count(e) AS total LIMIT 1""",
            ),
            (
                "details",
                f"""MATCH (e:SecurityEvent {{severity: 'ALERT'}})
WHERE true {time_filter}
OPTIONAL MATCH (actor:User)-[:ACTED_AS]->(e)
OPTIONAL MATCH (e)-[:TARGETS_PAYMENT]->(p:Payment)
OPTIONAL MATCH (e)-[:TARGETS_VERSION]->(v:InstructionVersion)
RETURN e.event_id, e.timestamp, e.action, e.message, e.severity,
       CASE WHEN e.payment_id IS NOT NULL THEN 'payment' ELSE 'instruction' END AS domain,
       coalesce(e.payment_id, '') AS payment_id,
       coalesce(p.instruction_id, v.instruction_id, '') AS instruction_id,
       coalesce(actor.display_name, actor.user_id, '') AS actor_display
ORDER BY e.timestamp DESC
LIMIT 200""",
            ),
        ]

    return None


def validate_read_only_cypher(cypher: str) -> None:
    """
    Multi-layer read-only guard for LLM-generated Cypher.

    Layers (innermost protection is the Neo4j READ_ACCESS session in neo4j.py):
    1. Reject empty or oversized query
    2. Reject multi-statement injection (embedded semicolons)
    3. Strip comments and string literal content before keyword analysis
    4. Require query to start with a read clause (MATCH / WITH / RETURN / UNWIND)
    5. Reject write DML/DDL keywords (CREATE, MERGE, SET, DELETE, …)
    6. Reject CALL to write-capable built-in or APOC procedures
    7. Require an explicit LIMIT clause to prevent full-graph scans
    """
    stripped = cypher.strip()

    # Layer 1 — empty / oversized
    if not stripped:
        raise ValueError("Cypher validation failed: empty query")
    if len(stripped) > _MAX_CYPHER_LEN:
        raise ValueError(
            f"Cypher validation failed: query exceeds {_MAX_CYPHER_LEN} characters"
        )

    # Layer 2 — multi-statement injection
    if ";" in stripped.rstrip(";"):
        raise ValueError(
            "Cypher validation failed: multiple statements are not allowed"
        )

    # Layer 3 — normalize: strip comments then string literals
    normalized = _LINE_COMMENT.sub("", stripped)
    normalized = _BLOCK_COMMENT.sub("", normalized)
    no_strings = _STRING_LITERAL.sub("''", normalized)

    # Layer 4 — must start with a read clause
    if not _READ_START.match(no_strings):
        raise ValueError(
            "Cypher validation failed: query must begin with "
            "MATCH, OPTIONAL MATCH, WITH, RETURN, or UNWIND"
        )

    # Layer 5 — write DML/DDL keywords
    m = _WRITE_KEYWORD.search(no_strings)
    if m:
        raise ValueError(
            f"Cypher validation failed: disallowed write keyword '{m.group(0).upper()}'"
        )

    # Layer 6 — write-capable CALL procedures
    if _WRITE_PROCEDURE.search(no_strings):
        raise ValueError(
            "Cypher validation failed: CALL to a write-capable procedure is not allowed"
        )

    # Layer 7 — explicit LIMIT required
    if not _LIMIT_CLAUSE.search(no_strings):
        raise ValueError(
            "Cypher validation failed: query must include a LIMIT clause"
        )


def _node_to_dict(node: Any) -> dict[str, Any]:
    if node is None:
        return {}
    if hasattr(node, "items"):
        return dict(node.items())
    if isinstance(node, dict):
        return node
    return {"value": str(node)}


def records_to_rows(records: list[Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for record in records:
        row: dict[str, Any] = {}
        for key in record.keys():
            value = record[key]
            if hasattr(value, "items"):
                row[key] = _node_to_dict(value)
            elif isinstance(value, list):
                row[key] = [
                    _node_to_dict(item) if hasattr(item, "items") else item for item in value
                ]
            else:
                row[key] = value
        rows.append(row)
    return rows


def extract_uuids(text: str) -> list[str]:
    """Return unique UUIDs from text in order of appearance."""
    return list(dict.fromkeys(match.group(0) for match in _UUID_PATTERN.finditer(text)))


def extract_event_id(row: dict[str, Any]) -> str | None:
    if row.get("event_id"):
        return str(row["event_id"])
    for value in row.values():
        if isinstance(value, dict) and value.get("event_id"):
            return str(value["event_id"])
    return None


def row_summary(row: dict[str, Any]) -> str:
    event_id = extract_event_id(row)
    if event_id:
        for key, value in row.items():
            if isinstance(value, dict) and value.get("event_id") == event_id:
                parts = [
                    value.get("action"),
                    value.get("severity"),
                    value.get("message"),
                    value.get("timestamp"),
                ]
                return " · ".join(str(p) for p in parts if p)
    return " · ".join(f"{k}={v}" for k, v in row.items() if v is not None)[:500]
