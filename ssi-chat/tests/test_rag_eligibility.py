from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from chat_application.models import ChatMessage
from chat_application.rag import RagService


@pytest.fixture
def rag_service() -> RagService:
    return RagService(
        ollama=MagicMock(),
        qdrant=MagicMock(),
        neo4j=MagicMock(),
    )


@pytest.mark.asyncio
async def test_answer_payment_eligible_approvers_without_token(
    rag_service: RagService,
) -> None:
    answer = await rag_service._answer_payment_eligible_approvers(
        "Who can approve payment abc?",
        bearer_token=None,
        session_id=None,
    )
    assert answer is not None
    assert "Log in as a compliance analyst" in answer


@pytest.mark.asyncio
async def test_answer_payment_eligible_approvers_without_payment_id(
    rag_service: RagService,
) -> None:
    answer = await rag_service._answer_payment_eligible_approvers(
        "Who can approve this payment?",
        bearer_token="token",
        session_id=None,
    )
    assert answer is not None
    assert "payment ID" in answer


@pytest.mark.asyncio
async def test_answer_payment_eligible_approvers_calls_authorization_client(
    rag_service: RagService,
) -> None:
    payment_id = "11111111-1111-1111-1111-111111111111"
    rag_service._eligibility = AsyncMock()
    rag_service._eligibility.eligible_approvers_for_payment.return_value = {
        "payment_id": payment_id,
        "payment_status": "SUBMITTED",
        "amount": 1_000_000,
        "currency": "USD",
        "owning_lob": "FICC",
        "instruction_status": "STANDING",
        "eligible": [],
        "candidates_evaluated": 1,
    }

    answer = await rag_service._answer_payment_eligible_approvers(
        f"Who can approve payment {payment_id}?",
        bearer_token="token",
        session_id="sess-1",
    )

    assert answer is not None
    assert payment_id in answer
    rag_service._eligibility.eligible_approvers_for_payment.assert_awaited_once()


@pytest.mark.asyncio
async def test_ask_short_circuits_eligibility_question(rag_service: RagService) -> None:
    payment_id = "22222222-2222-2222-2222-222222222222"
    rag_service._eligibility = AsyncMock()
    rag_service._eligibility.eligible_approvers_for_payment.return_value = {
        "payment_id": payment_id,
        "payment_status": "SUBMITTED",
        "amount": 500_000,
        "currency": "USD",
        "owning_lob": "FX",
        "instruction_status": "STANDING",
        "eligible": [
            {
                "user_id": "pay-201",
                "display_name": "Laurent, Sophie (pay-201)",
                "title": "VP",
                "allow_basis": [],
            }
        ],
        "candidates_evaluated": 1,
    }

    response = await rag_service.ask(
        f"Who can approve payment {payment_id}?",
        [],
        mode="payments",
        bearer_token="token",
        session_id="sess",
    )

    assert "Laurent, Sophie" in response.answer
    assert response.sources == []


@pytest.mark.asyncio
async def test_answer_instruction_eligible_approvers_calls_authorization_client(
    rag_service: RagService,
) -> None:
    instruction_id = "11111111-1111-1111-1111-111111111111"
    rag_service._eligibility = AsyncMock()
    rag_service._eligibility.eligible_approvers_for_instruction.return_value = {
        "instruction_id": instruction_id,
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
                "allow_basis": [],
            }
        ],
        "candidates_evaluated": 4,
    }

    answer = await rag_service._answer_instruction_eligible_approvers(
        f"Who can approve instruction {instruction_id}?",
        bearer_token="token",
        session_id="sess-1",
    )

    assert answer is not None
    assert "Vasquez, Elena" in answer
    rag_service._eligibility.eligible_approvers_for_instruction.assert_awaited_once()


@pytest.mark.asyncio
async def test_ask_short_circuits_instruction_eligibility_question(
    rag_service: RagService,
) -> None:
    instruction_id = "22222222-2222-2222-2222-222222222222"
    rag_service._eligibility = AsyncMock()
    rag_service._eligibility.eligible_approvers_for_instruction.return_value = {
        "instruction_id": instruction_id,
        "instruction_status": "PENDING",
        "instruction_type": "STANDING",
        "owning_lob": "FICC",
        "created_by_user_id": "ficc-101",
        "created_by_title": "Analyst",
        "eligible": [],
        "candidates_evaluated": 4,
    }

    response = await rag_service.ask(
        f"Who can approve this instruction {instruction_id}?",
        [],
        mode="instructions",
        bearer_token="token",
        session_id="sess",
    )

    assert instruction_id in response.answer
    assert response.sources == []
