from __future__ import annotations

from typing import Any


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
