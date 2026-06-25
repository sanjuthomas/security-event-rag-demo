from datetime import datetime
from typing import Any

from bson import ObjectId


def serialize_security_event(document: dict[str, Any]) -> dict[str, Any]:
    return _normalize(document)


def _normalize(value: Any) -> Any:
    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _normalize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_normalize(item) for item in value]
    return value
