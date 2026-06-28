from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from authz.main import app
from authz.models import PaymentEligibilityContext, Subject


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_evaluate_instruction_requires_service_auth(client: TestClient) -> None:
    response = client.post(
        "/api/v1/authorization/instructions/evaluate",
        json={
            "action": "CREATE",
            "instruction": {"status": "DRAFT", "type": "STANDING", "owning_lob": "FICC"},
            "account": {"lob": "FICC"},
        },
    )
    assert response.status_code == 401


def test_payment_eligible_approvers_requires_service_auth(client: TestClient) -> None:
    response = client.post(
        "/api/v1/authorization/payments/eligible-approvers",
        json={
            "payment": {
                "payment_id": "p1",
                "instruction_id": "i1",
                "instruction_version": 1,
                "status": "SUBMITTED",
                "amount": 100.0,
                "currency": "USD",
                "owning_lob": "FICC",
                "created_by_user_id": "pay-101",
            },
            "instruction_status": "STANDING",
        },
    )
    assert response.status_code == 401


def test_payment_eligible_approvers_success(client: TestClient) -> None:
    service_subject = Subject(user_id="svc-payment", title="Service Account", roles=["R"])
    response_payload = {
        "payment_id": "p1",
        "instruction_id": "i1",
        "payment_status": "SUBMITTED",
        "amount": 100.0,
        "currency": "USD",
        "owning_lob": "FICC",
        "instruction_status": "STANDING",
        "evaluated_at": "2026-01-01T00:00:00Z",
        "eligible": [],
        "candidates_evaluated": 0,
    }

    with (
        patch("authz.authorization_routes.get_service_caller", return_value=service_subject),
        patch("authz.authorization_routes._eligibility_service") as mock_service_factory,
    ):
        mock_service = AsyncMock()
        mock_service.eligible_approvers_for_payment.return_value = response_payload
        mock_service_factory.return_value = mock_service
        response = client.post(
            "/api/v1/authorization/payments/eligible-approvers",
            headers={"Authorization": "Bearer svc-token"},
            json={
                "payment": PaymentEligibilityContext(
                    payment_id="p1",
                    instruction_id="i1",
                    instruction_version=1,
                    status="SUBMITTED",
                    amount=100.0,
                    currency="USD",
                    owning_lob="FICC",
                    created_by_user_id="pay-101",
                ).model_dump(mode="json"),
                "instruction_status": "STANDING",
            },
        )

    assert response.status_code == 200
    assert response.json()["payment_id"] == "p1"
