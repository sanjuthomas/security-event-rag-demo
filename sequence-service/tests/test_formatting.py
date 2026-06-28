from __future__ import annotations

from datetime import date

import pytest

from seq.formatting import (
    build_counter_key,
    build_security_event_counter_key,
    build_security_event_sequence_id,
    build_sequence_id,
    compact_business_date,
    entity_type_code,
)
from seq.models import EntityType


def test_compact_business_date() -> None:
    assert compact_business_date(date(2026, 6, 27)) == "20260627"


def test_entity_type_code() -> None:
    assert entity_type_code(EntityType.INSTRUCTION) == "I"
    assert entity_type_code(EntityType.PAYMENT) == "P"


def test_build_counter_key() -> None:
    key = build_counter_key(date(2026, 6, 27), "FICC", EntityType.INSTRUCTION)
    assert key == "20260627-FICC-I"


def test_build_sequence_id() -> None:
    assert build_sequence_id("20260627-FICC-I", 3) == "20260627-FICC-I-3"


def test_build_security_event_counter_key() -> None:
    assert build_security_event_counter_key("20260628-FICC-I-32") == "20260628-FICC-I-32-SE"


def test_build_security_event_sequence_id() -> None:
    assert (
        build_security_event_sequence_id("20260628-FICC-P-2", 3)
        == "20260628-FICC-P-2-SE-3"
    )


@pytest.mark.parametrize(
    ("lob", "expected"),
    [
        ("fx", "FX"),
        (" desk_a ", "DESK_A"),
    ],
)
def test_request_normalizes_lob(lob: str, expected: str) -> None:
    from seq.models import NextSequenceRequest

    request = NextSequenceRequest(
        business_date=date(2026, 6, 26),
        owning_lob=lob,
        entity_type=EntityType.PAYMENT,
    )
    assert request.owning_lob == expected


def test_request_rejects_invalid_lob() -> None:
    from pydantic import ValidationError

    from seq.models import NextSequenceRequest

    with pytest.raises(ValidationError):
        NextSequenceRequest(
            business_date=date(2026, 6, 26),
            owning_lob="FX-Desk",
            entity_type=EntityType.INSTRUCTION,
        )
