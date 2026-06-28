from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock

import pytest

from seq.models import EntityType
from seq.repository import SequenceRepositoryError


def test_health(test_client) -> None:
    client, _repo = test_client
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "UP"}


def test_next_sequence_returns_formatted_id(test_client) -> None:
    client, repo = test_client
    repo.allocate_next.return_value = 1

    response = client.post(
        "/api/v1/sequences/next",
        json={
            "business_date": "2026-06-27",
            "owning_lob": "FICC",
            "entity_type": "INSTRUCTION",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["sequence_id"] == "20260627-FICC-I-1"
    assert body["sequence_number"] == 1
    assert body["owning_lob"] == "FICC"
    assert body["entity_type"] == "INSTRUCTION"
    assert body["counter_key"] == "20260627-FICC-I"


def test_next_sequence_increments_via_repository(test_client) -> None:
    client, repo = test_client
    repo.allocate_next.side_effect = [1, 2]

    first = client.post(
        "/api/v1/sequences/next",
        json={
            "business_date": "2026-06-27",
            "owning_lob": "FX",
            "entity_type": "PAYMENT",
        },
    )
    second = client.post(
        "/api/v1/sequences/next",
        json={
            "business_date": "2026-06-27",
            "owning_lob": "FX",
            "entity_type": "PAYMENT",
        },
    )

    assert first.json()["sequence_id"] == "20260627-FX-P-1"
    assert second.json()["sequence_id"] == "20260627-FX-P-2"


def test_next_sequence_rejects_invalid_payload(test_client) -> None:
    client, _repo = test_client
    response = client.post(
        "/api/v1/sequences/next",
        json={
            "business_date": "not-a-date",
            "owning_lob": "FICC",
            "entity_type": "INSTRUCTION",
        },
    )
    assert response.status_code == 422


def test_next_sequence_maps_repository_failure_to_503(test_client) -> None:
    client, repo = test_client
    repo.allocate_next.side_effect = SequenceRepositoryError("sequence allocation failed")

    response = client.post(
        "/api/v1/sequences/next",
        json={
            "business_date": "2026-06-27",
            "owning_lob": "FICC",
            "entity_type": "INSTRUCTION",
        },
    )

    assert response.status_code == 503


def test_next_security_event_sequence_returns_formatted_id(test_client) -> None:
    client, repo = test_client
    repo.allocate_next.return_value = 1

    response = client.post(
        "/api/v1/sequences/security-events/next",
        json={"resource_id": "20260628-FICC-I-32"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["sequence_id"] == "20260628-FICC-I-32-SE-1"
    assert body["resource_id"] == "20260628-FICC-I-32"
    assert body["sequence_number"] == 1
    assert body["counter_key"] == "20260628-FICC-I-32-SE"
    repo.allocate_next.assert_awaited_once_with("20260628-FICC-I-32-SE")


def test_next_security_event_sequence_increments(test_client) -> None:
    client, repo = test_client
    repo.allocate_next.side_effect = [1, 2]

    first = client.post(
        "/api/v1/sequences/security-events/next",
        json={"resource_id": "20260628-FICC-P-2"},
    )
    second = client.post(
        "/api/v1/sequences/security-events/next",
        json={"resource_id": "20260628-FICC-P-2"},
    )

    assert first.json()["sequence_id"] == "20260628-FICC-P-2-SE-1"
    assert second.json()["sequence_id"] == "20260628-FICC-P-2-SE-2"
