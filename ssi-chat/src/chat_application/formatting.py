from __future__ import annotations

import re
from typing import Any

_AMOUNT_IN_BASIS = re.compile(
    r"amount\s+([\d.eE+-]+)\s+(within subject and absolute limits)",
    re.IGNORECASE,
)


def escape_markdown_cell(value: Any) -> str:
    if value is None or value == "":
        text = "—"
    else:
        text = str(value)
    return text.replace("|", "\\|").replace("\n", " ")


def format_markdown_table(headers: list[str], rows: list[list[Any]]) -> str:
    """Render a GitHub-flavored markdown table."""
    if not headers:
        return ""
    if not rows:
        return "_No rows._"

    str_headers = [escape_markdown_cell(header) for header in headers]
    str_rows = [[escape_markdown_cell(cell) for cell in row] for row in rows]

    widths = [len(header) for header in str_headers]
    for row in str_rows:
        for index, cell in enumerate(row):
            if index < len(widths):
                widths[index] = max(widths[index], len(cell))

    def format_line(cells: list[str]) -> str:
        parts: list[str] = []
        for index, cell in enumerate(cells):
            width = widths[index] if index < len(widths) else len(cell)
            parts.append(cell.ljust(width))
        return "| " + " | ".join(parts) + " |"

    separator = "| " + " | ".join("-" * max(3, width) for width in widths) + " |"
    body = "\n".join(format_line(row) for row in str_rows)
    return f"{format_line(str_headers)}\n{separator}\n{body}"


def coerce_numeric_amount(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def format_usd_compact(amount: float) -> str:
    """Format a USD amount in compact compliance-facing prose (e.g. $1 million)."""
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


def format_money_amount(
    amount: Any,
    currency: str | None = None,
    *,
    currency_first: bool = True,
) -> str:
    """Format a monetary amount for display (handles scientific notation strings)."""
    value = coerce_numeric_amount(amount)
    if value is None:
        return "N/A"
    formatted = f"{value:,.2f}"
    if not currency:
        return formatted
    if currency_first:
        return f"{currency} {formatted}"
    return f"{formatted} {currency}"


def humanize_policy_basis_point(point: str) -> str:
    def _replace(match: re.Match[str]) -> str:
        try:
            amount = float(match.group(1))
        except ValueError:
            return match.group(0)
        return f"amount {format_usd_compact(amount)} {match.group(2)}"

    return _AMOUNT_IN_BASIS.sub(_replace, point)


def humanize_policy_basis(basis: list[str]) -> list[str]:
    return [humanize_policy_basis_point(point) for point in basis]


def humanize_authorization_text(text: str) -> str:
    if not text:
        return text
    return _AMOUNT_IN_BASIS.sub(
        lambda match: humanize_policy_basis_point(match.group(0)),
        text,
    )


def format_policy_basis_cell(basis: list[str] | None) -> str:
    if not basis:
        return "—"
    return "; ".join(humanize_policy_basis_point(point) for point in basis)


def format_eligible_approvers_section(
    *,
    header: str,
    section_title: str,
    eligible: list[dict[str, Any]],
    empty_message: str,
    candidate_role_label: str,
    candidates_evaluated: int | None = None,
) -> str:
    if not eligible:
        return f"{header}\n\n{empty_message}"

    table_rows = [
        [
            index,
            row.get("display_name") or row.get("user_id") or "—",
            row.get("title") or "—",
            format_policy_basis_cell(row.get("allow_basis")),
        ]
        for index, row in enumerate(eligible, start=1)
    ]
    lines = [
        header,
        "",
        section_title,
        "",
        format_markdown_table(["#", "Approver", "Title", "Policy basis"], table_rows),
    ]
    if candidates_evaluated is not None:
        lines.extend(
            [
                "",
                f"Evaluated {candidates_evaluated} {candidate_role_label} candidate(s) from the user directory.",
            ]
        )
    return "\n".join(lines)
