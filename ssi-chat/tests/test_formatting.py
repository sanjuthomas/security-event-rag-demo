from __future__ import annotations

from chat_application.formatting import (
    format_markdown_table,
    format_money_amount,
    format_usd_compact,
    humanize_policy_basis_point,
)


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


class TestMoneyFormatting:
    def test_format_money_amount_currency_first(self) -> None:
        assert format_money_amount(1_000_000, "USD") == "USD 1,000,000.00"

    def test_format_money_amount_suffix(self) -> None:
        assert format_money_amount(1_000_000, "USD", currency_first=False) == "1,000,000.00 USD"

    def test_format_money_amount_from_scientific_string(self) -> None:
        assert format_money_amount("1e+06", "USD") == "USD 1,000,000.00"

    def test_humanize_basis_scientific_amount(self) -> None:
        point = humanize_policy_basis_point(
            "amount 1e+06 within subject and absolute limits"
        )
        assert point == "amount $1 million within subject and absolute limits"
        assert "1e+06" not in point

    def test_format_usd_compact(self) -> None:
        assert format_usd_compact(1_000_000) == "$1 million"
