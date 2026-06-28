from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from sequence_client.client import SequenceClient
from sequence_client.errors import SequenceClientError, SequenceServiceUnavailable


@pytest.mark.asyncio
async def test_next_security_event_id_returns_sequence_id() -> None:
    client = SequenceClient("http://sequence:8095")
    response = httpx.Response(
        200,
        json={
            "sequence_id": "20260628-FICC-I-32-SE-1",
            "resource_id": "20260628-FICC-I-32",
            "sequence_number": 1,
            "counter_key": "20260628-FICC-I-32-SE",
        },
        request=httpx.Request(
            "POST",
            "http://sequence:8095/api/v1/sequences/security-events/next",
        ),
    )

    with patch("sequence_client.client.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None
        mock_client.post = AsyncMock(return_value=response)
        mock_client_cls.return_value = mock_client

        sequence_id = await client.next_security_event_id(
            resource_id="20260628-FICC-I-32",
        )

    assert sequence_id == "20260628-FICC-I-32-SE-1"
    payload = mock_client.post.await_args.kwargs["json"]
    assert payload["resource_id"] == "20260628-FICC-I-32"


@pytest.mark.asyncio
async def test_next_instruction_id_returns_sequence_id() -> None:
    client = SequenceClient("http://sequence:8095")

    response = httpx.Response(
        200,
        json={
            "sequence_id": "20260627-FICC-I-1",
            "business_date": "2026-06-27",
            "owning_lob": "FICC",
            "entity_type": "INSTRUCTION",
            "sequence_number": 1,
            "counter_key": "20260627-FICC-I",
        },
        request=httpx.Request("POST", "http://sequence:8095/api/v1/sequences/next"),
    )

    with patch("sequence_client.client.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None
        mock_client.post = AsyncMock(return_value=response)
        mock_client_cls.return_value = mock_client

        sequence_id = await client.next_instruction_id(
            business_date=date(2026, 6, 27),
            owning_lob="FICC",
        )

    assert sequence_id == "20260627-FICC-I-1"
    mock_client.post.assert_awaited_once()
    payload = mock_client.post.await_args.kwargs["json"]
    assert payload["entity_type"] == "INSTRUCTION"


@pytest.mark.asyncio
async def test_next_payment_id_maps_entity_type() -> None:
    client = SequenceClient("http://sequence:8095")
    response = httpx.Response(
        200,
        json={"sequence_id": "20260627-FX-P-2"},
        request=httpx.Request("POST", "http://sequence:8095/api/v1/sequences/next"),
    )

    with patch("sequence_client.client.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None
        mock_client.post = AsyncMock(return_value=response)
        mock_client_cls.return_value = mock_client

        sequence_id = await client.next_payment_id(
            business_date=date(2026, 6, 27),
            owning_lob="FX",
        )

    assert sequence_id == "20260627-FX-P-2"
    payload = mock_client.post.await_args.kwargs["json"]
    assert payload["entity_type"] == "PAYMENT"


@pytest.mark.asyncio
async def test_transport_error_raises_unavailable() -> None:
    client = SequenceClient("http://sequence:8095")

    with patch("sequence_client.client.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None
        mock_client.post = AsyncMock(side_effect=httpx.ConnectError("refused"))
        mock_client_cls.return_value = mock_client

        with pytest.raises(SequenceServiceUnavailable):
            await client.next_instruction_id(
                business_date=date(2026, 6, 27),
                owning_lob="FICC",
            )


@pytest.mark.asyncio
async def test_422_raises_client_error() -> None:
    client = SequenceClient("http://sequence:8095")
    response = httpx.Response(
        422,
        json={"detail": "invalid"},
        request=httpx.Request("POST", "http://sequence:8095/api/v1/sequences/next"),
    )

    with patch("sequence_client.client.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None
        mock_client.post = AsyncMock(return_value=response)
        mock_client_cls.return_value = mock_client

        with pytest.raises(SequenceClientError):
            await client.next_instruction_id(
                business_date=date(2026, 6, 27),
                owning_lob="FICC",
            )
