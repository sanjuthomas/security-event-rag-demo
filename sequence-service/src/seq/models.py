from __future__ import annotations

from datetime import date
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator


class EntityType(str, Enum):
    INSTRUCTION = "INSTRUCTION"
    PAYMENT = "PAYMENT"


class NextSequenceRequest(BaseModel):
    business_date: date = Field(description="Business date in yyyy-mm-dd format")
    owning_lob: str = Field(min_length=1, description="Line of business code, e.g. FICC or FX")
    entity_type: EntityType

    @field_validator("owning_lob")
    @classmethod
    def validate_owning_lob(cls, value: str) -> str:
        normalized = value.strip().upper()
        if not normalized:
            raise ValueError("owning_lob must not be blank")
        if not all(ch.isalnum() or ch == "_" for ch in normalized):
            raise ValueError("owning_lob may only contain letters, digits, and underscores")
        return normalized


class NextSequenceResponse(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    sequence_id: str
    business_date: date
    owning_lob: str
    entity_type: EntityType
    sequence_number: int
    counter_key: str


class NextSecurityEventSequenceRequest(BaseModel):
    resource_id: str = Field(
        min_length=1,
        description="Parent resource id, e.g. instruction or payment sequence id",
    )

    @field_validator("resource_id")
    @classmethod
    def validate_resource_id(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("resource_id must not be blank")
        return normalized


class NextSecurityEventSequenceResponse(BaseModel):
    sequence_id: str
    resource_id: str
    sequence_number: int
    counter_key: str
