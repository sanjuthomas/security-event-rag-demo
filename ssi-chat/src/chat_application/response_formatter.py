"""Post-process chat answers into structured presentation formats.

Formatters are tried in order; the first match wins. Unmatched text is returned unchanged
so new rules can be added incrementally without breaking existing answers.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from chat_application.formatting import format_markdown_table

_KV_FIELD_RE = re.compile(r"([a-z][a-z0-9_]*)=")
_NUMBERED_LINE_RE = re.compile(r"^\s*\d+\.\s+(.+)$")
_MARKDOWN_TABLE_SEPARATOR_RE = re.compile(r"^\|?\s*[-:| ]+\|")

INSTRUCTION_FIELD_ORDER = [
    "instruction_id",
    "owning_lob",
    "status",
    "instruction_type",
    "currency",
    "wire_scope",
    "creditor",
    "creditor_name",
    "creditor_account",
    "creator",
    "creator_display",
    "approver",
    "approver_display",
    "approved_at",
    "effective",
    "effective_date",
    "end",
    "end_date",
    "rejected_by",
    "rejected_at",
    "rejection_reason",
    "event_id",
    "timestamp",
    "action",
    "actor",
    "actor_display",
    "severity",
    "outcome",
    "message",
]

PAYMENT_FIELD_ORDER = [
    "payment_id",
    "instruction_id",
    "status",
    "amount",
    "currency",
    "value_date",
    "owning_lob",
    "creator",
    "creator_display",
    "approver",
    "approver_display",
    "approved_at",
    "event_id",
    "timestamp",
    "action",
    "actor",
    "actor_display",
    "severity",
    "outcome",
]

FIELD_LABELS: dict[str, str] = {
    "instruction_id": "Instruction ID",
    "payment_id": "Payment ID",
    "event_id": "Event ID",
    "owning_lob": "LOB",
    "wire_scope": "Wire Scope",
    "instruction_type": "Type",
    "value_date": "Value Date",
    "approved_at": "Approved At",
    "effective_date": "Effective",
    "end_date": "End",
    "rejected_at": "Rejected At",
    "rejection_reason": "Rejection Reason",
    "actor_display": "Actor",
    "creator_display": "Creator",
    "approver_display": "Approver",
    "creditor_name": "Creditor",
    "creditor_account": "Creditor Account",
}

DOMAIN_FIELD_ORDERS = [INSTRUCTION_FIELD_ORDER, PAYMENT_FIELD_ORDER]


def format_chat_response(text: str) -> str:
    """Apply the first matching formatter, or return the original text."""
    if not text or not text.strip():
        return text

    for formatter in _FORMATTERS:
        formatted = formatter(text)
        if formatted is not None:
            return formatted
    return text


def parse_key_value_record(text: str) -> dict[str, str]:
    """Parse comma-separated key=value segments from a single record line."""
    matches = list(_KV_FIELD_RE.finditer(text))
    if not matches:
        return {}

    record: dict[str, str] = {}
    for index, match in enumerate(matches):
        key = match.group(1)
        value_start = match.end()
        value_end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        value = text[value_start:value_end].rstrip().removesuffix(",").strip()
        record[key] = value
    return record


def humanize_field_name(key: str) -> str:
    return FIELD_LABELS.get(key, key.replace("_", " ").title())


def column_order(keys: set[str]) -> list[str]:
    for preferred in DOMAIN_FIELD_ORDERS:
        ordered = [key for key in preferred if key in keys]
        if len(ordered) >= 2:
            remaining = sorted(key for key in keys if key not in ordered)
            return ordered + remaining
    return sorted(keys)


def records_to_markdown_table(records: list[dict[str, str]]) -> str:
    keys = {key for record in records for key in record}
    headers = [humanize_field_name(key) for key in column_order(keys)]
    key_order = column_order(keys)
    rows = [[record.get(key, "—") for key in key_order] for record in records]
    return format_markdown_table(headers, rows)


def has_markdown_table(text: str) -> bool:
    lines = text.splitlines()
    for index in range(len(lines) - 1):
        if "|" in lines[index] and _MARKDOWN_TABLE_SEPARATOR_RE.match(lines[index + 1]):
            return True
    return False


def _join_sections(*parts: str) -> str:
    return "\n".join(part for part in parts if part).rstrip()


def _format_record_block(
    intro: str,
    records: list[dict[str, str]],
    footer: str,
) -> str | None:
    if not records:
        return None

    min_keys = min(len(record) for record in records)
    if min_keys < 2:
        return None
    if len(records) == 1 and min_keys < 3:
        return None

    table = records_to_markdown_table(records)
    return _join_sections(intro, table, footer)


def _split_numbered_key_value_records(text: str) -> tuple[str, list[dict[str, str]], str]:
    intro_lines: list[str] = []
    footer_lines: list[str] = []
    records: list[dict[str, str]] = []
    seen_record = False

    for line in text.splitlines():
        match = _NUMBERED_LINE_RE.match(line)
        if match:
            seen_record = True
            records.append(parse_key_value_record(match.group(1)))
            continue
        if not seen_record:
            intro_lines.append(line)
        else:
            footer_lines.append(line)

    return "\n".join(intro_lines).strip(), records, "\n".join(footer_lines).strip()


def _split_plain_key_value_lines(text: str) -> tuple[str, list[dict[str, str]], str]:
    intro_lines: list[str] = []
    footer_lines: list[str] = []
    records: list[dict[str, str]] = []
    seen_record = False

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            if not seen_record:
                intro_lines.append(line)
            else:
                footer_lines.append(line)
            continue

        parsed = parse_key_value_record(stripped)
        if len(parsed) >= 2 and "=" in stripped:
            seen_record = True
            records.append(parsed)
            continue

        if not seen_record:
            intro_lines.append(line)
        else:
            footer_lines.append(line)

    return "\n".join(intro_lines).strip(), records, "\n".join(footer_lines).strip()


def _try_numbered_key_value_list(text: str) -> str | None:
    if has_markdown_table(text):
        return None

    intro, records, footer = _split_numbered_key_value_records(text)
    if len(records) < 1:
        return None
    return _format_record_block(intro, records, footer)


def _try_plain_key_value_lines(text: str) -> str | None:
    if has_markdown_table(text):
        return None

    intro, records, footer = _split_plain_key_value_lines(text)
    if len(records) < 2:
        return None
    return _format_record_block(intro, records, footer)


_FORMATTERS: list[Callable[[str], str | None]] = [
    _try_numbered_key_value_list,
    _try_plain_key_value_lines,
]
