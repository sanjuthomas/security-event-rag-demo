from __future__ import annotations

import logging
import re
from typing import Any

from security_event_chat.config import settings

logger = logging.getLogger(__name__)

_WRITE_PATTERN = re.compile(
    r"\b(CREATE|MERGE|SET|DELETE|REMOVE|DROP|DETACH|FOREACH|LOAD\s+CSV)\b",
    re.IGNORECASE,
)

_UUID_PATTERN = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    re.IGNORECASE,
)

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
    stripped = cypher.strip()
    if not stripped:
        raise ValueError("empty Cypher query")
    if _WRITE_PATTERN.search(stripped):
        raise ValueError("Cypher query contains disallowed write operations")
    if ";" in stripped.rstrip(";"):
        raise ValueError("multiple Cypher statements are not allowed")


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
