from __future__ import annotations

from datetime import date

from seq.models import EntityType


def compact_business_date(business_date: date) -> str:
    return business_date.strftime("%Y%m%d")


def entity_type_code(entity_type: EntityType) -> str:
    return "I" if entity_type == EntityType.INSTRUCTION else "P"


def build_counter_key(business_date: date, owning_lob: str, entity_type: EntityType) -> str:
    return f"{compact_business_date(business_date)}-{owning_lob}-{entity_type_code(entity_type)}"


def build_sequence_id(counter_key: str, sequence_number: int) -> str:
    return f"{counter_key}-{sequence_number}"


def build_security_event_counter_key(resource_id: str) -> str:
    return f"{resource_id}-SE"


def build_security_event_sequence_id(resource_id: str, sequence_number: int) -> str:
    return f"{build_security_event_counter_key(resource_id)}-{sequence_number}"
