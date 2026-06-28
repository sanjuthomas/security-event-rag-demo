from __future__ import annotations

from chat_application.formatting import format_markdown_table
from chat_application.response_formatter import (
    format_chat_response,
    has_markdown_table,
    parse_key_value_record,
    records_to_markdown_table,
)


STANDING_INSTRUCTIONS_ANSWER = """1. instruction_id=20260628-FICC-I-1, owning_lob=FICC, status=STANDING, currency=USD, wire_scope=DOMESTIC, creditor=Counterparty LLC, creator=Walsh, Patricia (mo-010), effective=2026-06-28T00:00:00, end=2027-06-28T00:00:00, approver=Nguyen, Caroline (ficc-500), approved_at=2026-06-28T13:53:16.560562
2. instruction_id=20260628-FICC-I-12, owning_lob=FICC, status=STANDING, currency=USD, wire_scope=DOMESTIC, creditor=Counterparty LLC, creator=Chen, Sarah (mo-100), effective=2026-06-28T00:00:00, end=2027-06-28T00:00:00, approver=Vasquez, Elena (ficc-300), approved_at=2026-06-28T13:53:26.070029"""


class TestParseKeyValueRecord:
    def test_parses_commas_inside_display_names(self) -> None:
        record = parse_key_value_record(
            "creator=Walsh, Patricia (mo-010), status=STANDING, approver=Nguyen, Caroline (ficc-500)"
        )
        assert record["creator"] == "Walsh, Patricia (mo-010)"
        assert record["status"] == "STANDING"
        assert record["approver"] == "Nguyen, Caroline (ficc-500)"


class TestFormatChatResponse:
    def test_formats_numbered_instruction_list_as_table(self) -> None:
        formatted = format_chat_response(STANDING_INSTRUCTIONS_ANSWER)

        assert "| Instruction ID" in formatted
        assert "| LOB" in formatted
        assert "| Status" in formatted
        assert "20260628-FICC-I-1" in formatted
        assert "20260628-FICC-I-12" in formatted
        assert "Walsh, Patricia (mo-010)" in formatted
        assert "Vasquez, Elena (ficc-300)" in formatted
        assert "instruction_id=" not in formatted

    def test_preserves_intro_and_footer(self) -> None:
        text = (
            "Found 2 STANDING instructions for FICC:\n\n"
            f"{STANDING_INSTRUCTIONS_ANSWER}\n\n"
            "All are domestic USD wires."
        )
        formatted = format_chat_response(text)

        assert formatted.startswith("Found 2 STANDING instructions for FICC:")
        assert "| Instruction ID" in formatted
        assert formatted.endswith("All are domestic USD wires.")

    def test_leaves_who_when_why_answers_unchanged(self) -> None:
        text = (
            "WHO: Vasquez, Elena (ficc-300)\n"
            "WHEN: 2026-06-28T13:53:26.070029\n"
            "WHY: Approved as FICC supervisor."
        )
        assert format_chat_response(text) == text

    def test_leaves_existing_markdown_tables_unchanged(self) -> None:
        table = format_markdown_table(
            ["Payment ID", "Status"],
            [["pay-1", "APPROVED"]],
        )
        text = f"Payments:\n\n{table}"
        assert format_chat_response(text) == text

    def test_leaves_plain_prose_unchanged(self) -> None:
        text = "No such cases were found in the graph."
        assert format_chat_response(text) == text

    def test_formats_plain_key_value_lines(self) -> None:
        text = (
            "instruction_id=20260628-FICC-I-1, status=STANDING, owning_lob=FICC\n"
            "instruction_id=20260628-FICC-I-12, status=STANDING, owning_lob=FICC"
        )
        formatted = format_chat_response(text)
        assert "| Instruction ID" in formatted
        assert "20260628-FICC-I-1" in formatted
        assert "20260628-FICC-I-12" in formatted


class TestHasMarkdownTable:
    def test_detects_gfm_table(self) -> None:
        table = format_markdown_table(["A"], [["1"]])
        assert has_markdown_table(table) is True

    def test_false_for_key_value_list(self) -> None:
        assert has_markdown_table(STANDING_INSTRUCTIONS_ANSWER) is False


class TestRecordsToMarkdownTable:
    def test_orders_instruction_columns(self) -> None:
        table = records_to_markdown_table(
            [
                {
                    "status": "STANDING",
                    "instruction_id": "20260628-FICC-I-1",
                    "owning_lob": "FICC",
                }
            ]
        )
        header_line = table.splitlines()[0]
        assert header_line.index("Instruction ID") < header_line.index("LOB")
        assert header_line.index("LOB") < header_line.index("Status")
