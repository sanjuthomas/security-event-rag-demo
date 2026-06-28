from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from inst.dependencies import get_subject
from inst.models.api import InstructionResponse, Subject
from inst.routes import get_service, router
from inst.service import InstructionService


@pytest.fixture
def sample_subject() -> Subject:
    return Subject(
        user_id="alice.ficc",
        title="VP",
        lob="FICC",
        roles=["INSTRUCTION_CREATOR"],
    )


@pytest.fixture
def mock_service() -> MagicMock:
    service = MagicMock(spec=InstructionService)
    service.create = AsyncMock()
    service.list = AsyncMock(return_value=[])
    service.get = AsyncMock()
    service.update = AsyncMock()
    service.delete = AsyncMock()
    service.submit = AsyncMock()
    service.approve = AsyncMock()
    service.reject = AsyncMock()
    service.suspend = AsyncMock()
    service.reactivate = AsyncMock()
    service.use = AsyncMock()
    service.list_versions = AsyncMock(return_value=[])
    return service


@pytest.fixture
def api_client(sample_subject: Subject, mock_service: MagicMock) -> TestClient:
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_subject] = lambda: sample_subject
    app.dependency_overrides[get_service] = lambda: mock_service
    return TestClient(app)


def _sample_response() -> InstructionResponse:
    from datetime import datetime

    from inst.models.api import CreateInstructionRequest
    from inst.service import _instruction_from_request, _to_response
    from inst.storage import VersionedInstruction
    from tests.helpers import domestic_payload

    req = CreateInstructionRequest.model_validate(domestic_payload())
    subject = Subject(user_id="u", title="VP", roles=["R"])
    instruction = _instruction_from_request(req, subject, instruction_id="i1")
    return _to_response(
        VersionedInstruction(
            instruction=instruction,
            version_number=1,
            valid_in=datetime.utcnow(),
            valid_out=None,
        )
    )


def test_create_instruction(api_client: TestClient, mock_service: MagicMock) -> None:
    response_model = _sample_response()
    mock_service.create.return_value = response_model
    from tests.helpers import domestic_payload

    response = api_client.post("/api/v1/instructions", json=domestic_payload())
    assert response.status_code == 201
    assert response.json()["instruction_id"] == "i1"


def test_create_permission_denied(api_client: TestClient, mock_service: MagicMock) -> None:
    mock_service.create.side_effect = PermissionError("denied")
    from tests.helpers import domestic_payload

    response = api_client.post("/api/v1/instructions", json=domestic_payload())
    assert response.status_code == 403


def test_list_instructions(api_client: TestClient, mock_service: MagicMock) -> None:
    mock_service.list.return_value = [_sample_response()]
    response = api_client.get("/api/v1/instructions")
    assert response.status_code == 200
    assert len(response.json()) == 1


def test_get_instruction_not_found(api_client: TestClient, mock_service: MagicMock) -> None:
    from inst.repository import InstructionNotFoundError

    mock_service.get.side_effect = InstructionNotFoundError("i1")
    response = api_client.get("/api/v1/instructions/i1")
    assert response.status_code == 404


def test_submit_invalid_state(api_client: TestClient, mock_service: MagicMock) -> None:
    from inst.service import InvalidStateTransitionError

    mock_service.submit.side_effect = InvalidStateTransitionError("bad state")
    response = api_client.post("/api/v1/instructions/i1/submit")
    assert response.status_code == 409
