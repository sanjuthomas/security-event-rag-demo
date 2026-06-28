from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ps.admin import get_admin_subject
from ps.models.api import Subject
from ps.ui_routes import router


@pytest.fixture
def ui_client(payment) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    admin = Subject(user_id="admin-001", title="Admin", roles=["PLATFORM_ADMIN"])
    app.dependency_overrides[get_admin_subject] = lambda: admin
    client = TestClient(app)
    client.payment = payment  # type: ignore[attr-defined]
    return client


@patch("ps.ui_routes.PaymentRepository")
def test_ui_list_payments_instruction_filter(
    mock_repo_cls: AsyncMock, ui_client: TestClient
) -> None:
    mock_repo = AsyncMock()
    mock_repo.list.return_value = [ui_client.payment]
    mock_repo_cls.return_value = mock_repo

    instruction_id = "3bcb9b9a-9415-44ce-b707-4cc4c8281bb9"
    response = ui_client.get(
        "/api/ui/payments",
        params={"instruction_id": instruction_id},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 1
    assert payload["payments"][0]["instruction_id"] == ui_client.payment.instruction_id
    mock_repo.list.assert_awaited_once_with(
        status=None,
        instruction_id=instruction_id,
        limit=200,
    )
