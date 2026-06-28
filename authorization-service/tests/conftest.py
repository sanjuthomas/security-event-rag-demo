from __future__ import annotations

import os
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from authz.admin import get_admin_subject
from authz.models import Subject


@pytest.fixture(scope="session", autouse=True)
def disable_open_telemetry_for_tests() -> None:
    os.environ["OTEL_SDK_DISABLED"] = "true"


@pytest.fixture
def users_file(tmp_path):
    path = tmp_path / "users.yaml"
    path.write_text("users: []\n", encoding="utf-8")
    return path


@pytest.fixture
def test_client(users_file, monkeypatch):
    monkeypatch.setattr("authz.config.settings.users_file", users_file)
    monkeypatch.setattr("authz.config.settings.oidc_issuer_url", "http://localhost:8080")

    from authz import main as main_module

    admin_subject = Subject(
        user_id="admin-001",
        title="Platform Admin",
        roles=["PLATFORM_ADMIN"],
    )
    main_module.app.dependency_overrides[get_admin_subject] = lambda: admin_subject

    with TestClient(main_module.app) as client:
        yield client

    main_module.app.dependency_overrides.clear()
