from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from etl.config import settings
from etl.search_text.transforms import apply_transform


def profiles_dir() -> Path:
    if settings.search_profiles_dir:
        return Path(settings.search_profiles_dir)
    # etl/config.py → parents[2] == ssi-indexer/
    return Path(__file__).resolve().parents[3] / "search-profiles"


@lru_cache
def load_entity_profile(entity: str) -> dict[str, Any]:
    path = profiles_dir() / f"{entity}.yaml"
    if not path.is_file():
        raise FileNotFoundError(f"search profile not found: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"invalid search profile: {path}")
    return data


def _load_shared_profile(name: str) -> dict[str, Any]:
    path = profiles_dir() / "_shared" / f"{name}.yaml"
    if not path.is_file():
        raise FileNotFoundError(f"shared search profile not found: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"invalid shared search profile: {path}")
    return data


def expand_includes(profile: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten entity includes + shared profile references into field specs."""
    includes = profile.get("includes") or []
    expanded: list[dict[str, Any]] = []
    for item in includes:
        if not isinstance(item, dict):
            continue
        if "profile" in item:
            shared = _load_shared_profile(str(item["profile"]))
            for field in shared.get("fields") or []:
                if isinstance(field, dict):
                    expanded.append(field)
            continue
        expanded.append(item)
    return expanded


def list_profile_fields(entity: str) -> dict[str, Any]:
    """Return metadata for admin/docs: wired flag, includes, excludes."""
    profile = load_entity_profile(entity)
    fields = expand_includes(profile)
    field_specs: list[dict[str, Any]] = []
    for item in fields:
        spec: dict[str, Any] = {}
        if "literal" in item:
            spec["literal"] = item["literal"]
        elif item.get("path"):
            spec["path"] = item["path"]
            if item.get("transform"):
                spec["transform"] = item["transform"]
        field_specs.append(spec)
    return {
        "entity": profile.get("entity", entity),
        "wired": bool(profile.get("wired")),
        "description": profile.get("description", "").strip(),
        "context_root": profile.get("context_root"),
        "payload_source": profile.get("payload_source"),
        "includes": field_specs,
        "excludes": list(profile.get("excludes") or []),
    }


def list_search_profiles() -> list[dict[str, Any]]:
    """Return metadata for every entity profile YAML in search-profiles/."""
    profiles: list[dict[str, Any]] = []
    for path in sorted(profiles_dir().glob("*.yaml")):
        profiles.append(list_profile_fields(path.stem))
    return profiles


def build_search_text_from_profile(entity: str, document: dict[str, Any]) -> str:
    profile = load_entity_profile(entity)
    join_cfg = profile.get("join") or {}
    separator = str(join_cfg.get("separator", " "))
    omit_empty = bool(join_cfg.get("omit_empty", True))

    parts: list[str] = []
    for field in expand_includes(profile):
        if "literal" in field:
            text = str(field["literal"])
        else:
            path = field.get("path")
            if not path:
                continue
            raw = document.get(path) if "." not in path else _get_nested(document, path)
            transform = str(field.get("transform", "default"))
            text = apply_transform(raw, transform)
        if text is None:
            continue
        if omit_empty and not str(text).strip():
            continue
        parts.append(str(text))

    return separator.join(parts).strip()


def _get_nested(document: dict[str, Any], path: str) -> Any:
    current: Any = document
    for segment in path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(segment)
    return current
