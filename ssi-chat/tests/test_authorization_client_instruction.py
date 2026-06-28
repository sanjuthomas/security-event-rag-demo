from chat_application.authorization_client import format_instruction_eligible_approvers_answer


def test_format_instruction_eligible_approvers_answer_lists_users() -> None:
    text = format_instruction_eligible_approvers_answer(
        {
            "instruction_id": "inst-1",
            "instruction_status": "PENDING",
            "instruction_type": "STANDING",
            "owning_lob": "FICC",
            "created_by_user_id": "ficc-101",
            "created_by_title": "Analyst",
            "eligible": [
                {
                    "user_id": "ficc-300",
                    "display_name": "Vasquez, Elena (ficc-300)",
                    "title": "Vice President",
                    "allow_basis": ["approval matrix"],
                }
            ],
            "candidates_evaluated": 4,
        }
    )

    assert "instruction inst-1" in text
    assert "Vasquez, Elena" in text
    assert "INSTRUCTION_APPROVER" in text
    assert "| Approver" in text
