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
