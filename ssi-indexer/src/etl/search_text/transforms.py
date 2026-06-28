from __future__ import annotations

from typing import Any


def get_path(document: dict[str, Any], path: str) -> Any:
    """Resolve a dot-separated path on nested dicts."""
    current: Any = document
    for segment in path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(segment)
    return current


def display_name(user: dict[str, Any] | None) -> str | None:
    if not user:
        return None
    family = user.get("family_name")
    given = user.get("given_name")
    user_id = user.get("user_id") or ""
    if family and given and user_id:
        return f"{family}, {given} ({user_id})"
    if user_id:
        return user_id
    return None


def apply_transform(value: Any, transform: str) -> str | None:
    if transform == "join_list":
        if not value:
            return None
        if isinstance(value, list):
            joined = " ".join(str(item) for item in value if item)
            return joined or None
        return str(value)
    if transform == "str_value":
        if value is None or value == "":
            return None
        return str(value)
    if transform == "display_name":
        if isinstance(value, dict):
            return display_name(value)
        return None
    # default — mirror legacy truthy-only inclusion
    if value is None or value == "":
        return None
    if isinstance(value, list):
        joined = " ".join(str(item) for item in value if item)
        return joined or None
    return str(value)
