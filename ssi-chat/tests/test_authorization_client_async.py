from unittest.mock import AsyncMock, patch

import httpx
import pytest

from chat_application.authorization_client import EligibilityClient, EligibilityClientError


@pytest.mark.asyncio
async def test_eligible_approvers_for_payment_success() -> None:
    response = httpx.Response(
        200,
        json={"payment_id": "p1", "eligible": []},
        request=httpx.Request("POST", "http://payment.test/api/v1/payments/p1/eligible-approvers"),
    )

    with patch("chat_application.authorization_client.httpx.AsyncClient") as mock_client_cls:
        mock_client = mock_client_cls.return_value.__aenter__.return_value
        mock_client.post = AsyncMock(return_value=response)
        client = EligibilityClient(
            payment_service_url="http://payment.test",
            instruction_service_url="http://instruction.test",
        )
        body = await client.eligible_approvers_for_payment(
            "p1",
            bearer_token="token",
        )

    assert body["payment_id"] == "p1"


@pytest.mark.asyncio
async def test_eligible_approvers_for_payment_forbidden() -> None:
    response = httpx.Response(
        403,
        json={"detail": "forbidden"},
        request=httpx.Request("POST", "http://payment.test/api/v1/payments/p1/eligible-approvers"),
    )

    with patch("chat_application.authorization_client.httpx.AsyncClient") as mock_client_cls:
        mock_client = mock_client_cls.return_value.__aenter__.return_value
        mock_client.post = AsyncMock(return_value=response)
        client = EligibilityClient(payment_service_url="http://payment.test")

        with pytest.raises(EligibilityClientError, match="COMPLIANCE_ANALYST"):
            await client.eligible_approvers_for_payment("p1", bearer_token="token")
