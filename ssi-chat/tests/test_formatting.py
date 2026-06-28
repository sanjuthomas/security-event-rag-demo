from __future__ import annotations

from chat_application.formatting import format_markdown_table


class TestFormatMarkdownTable:
    def test_renders_headers_and_rows(self) -> None:
        table = format_markdown_table(
            ["Payment ID", "Status", "Amount"],
            [
                ["pay-1", "APPROVED", "1,000.00 USD"],
                ["pay-2", "SUBMITTED", "500.00 USD"],
            ],
        )
        assert "| Payment ID | Status    | Amount       |" in table
        assert "| pay-1      | APPROVED  | 1,000.00 USD |" in table
        assert "| pay-2      | SUBMITTED | 500.00 USD   |" in table

    def test_empty_rows(self) -> None:
        assert format_markdown_table(["A"], []) == "_No rows._"

    def test_escapes_pipe_characters(self) -> None:
        table = format_markdown_table(["Name"], [["a|b"]])
        assert "a\\|b" in table

    def test_short_header_uses_gfm_separator(self) -> None:
        table = format_markdown_table(["#", "Policy check"], [[1, "role FUNDING_APPROVER"]])
        lines = table.split("\n")
        assert len(lines) == 3
        assert "---" in lines[1]
        assert lines[1].count("-") >= 6
