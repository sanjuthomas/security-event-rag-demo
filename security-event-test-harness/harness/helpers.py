from __future__ import annotations

from enum import StrEnum

import httpx

from harness.config import Settings
from harness.fixtures import SeedFile, build_instruction_payload, load_users
from harness.ilm_client import InstructionLifecycleClient
from harness.zitadel_auth import SessionCredentials, ZitadelAuthClient


class Operation(StrEnum):
    CREATE = "create"
    GET = "get"
    LIST = "list"
    SUBMIT = "submit"
    APPROVE = "approve"
    LIST_VERSIONS = "list_versions"


def build_scenario() -> list[tuple[Operation, str, bool, str]]:
    """Return (operation, user_id, expect_success, description)."""
    return [
        (Operation.CREATE, "mo-100", True, "middle office creates FICC instruction"),
        (Operation.GET, "mo-100", True, "creator reads instruction"),
        (Operation.CREATE, "ficc-201", False, "approver cannot create (ALERT)"),
        (Operation.SUBMIT, "mo-100", True, "middle office submits instruction"),
        (Operation.LIST, "mo-100", True, "middle office lists instructions"),
        (Operation.APPROVE, "mo-100", False, "creator cannot approve (ALERT)"),
        (Operation.APPROVE, "ficc-300", True, "FICC VP approves instruction"),
        (Operation.GET, "ficc-300", True, "approver reads instruction"),
        (Operation.LIST_VERSIONS, "fx-201", False, "FX user cannot read FICC versions (ALERT)"),
    ]


def _login_name(user_id: str, email_domain: str) -> str:
    return f"{user_id}@{email_domain}"


def _session_for_user(
    auth: ZitadelAuthClient,
    seed: SeedFile,
    settings: Settings,
    user_id: str,
) -> SessionCredentials:
    password = seed.defaults.get("password", settings.default_password)
    domain = seed.defaults.get("email_domain", settings.email_domain)
    login_name = _login_name(user_id, domain)
    return auth.login(login_name, password)


def _count_security_events(settings: Settings) -> int:
    try:
        from pymongo import MongoClient
    except ImportError:
        return -1

    client = MongoClient(settings.mongodb_uri)
    try:
        collection = client[settings.security_events_database][settings.security_events_collection]
        return collection.count_documents({})
    finally:
        client.close()


def build_seed_plan(count: int) -> list[tuple[str, str, str, str]]:
    """Return (user_id, owning_lob, instruction_type, currency) for each instruction."""
    templates = [
        ("mo-100", "FICC", "SINGLE_USE", "USD"),
        ("mo-101", "FICC", "STANDING", "USD"),
        ("mo-100", "FX", "SINGLE_USE", "EUR"),
        ("mo-101", "FX", "STANDING", "EUR"),
        ("mo-050", "DESK_RATES", "SINGLE_USE", "USD"),
        ("mo-050", "FICC", "SINGLE_USE", "USD"),
        ("mo-010", "FICC", "STANDING", "USD"),
        ("mo-050", "FX", "SINGLE_USE", "GBP"),
    ]
    plan: list[tuple[str, str, str, str]] = []
    for index in range(count):
        plan.append(templates[index % len(templates)])
    return plan


def _approver_for_instruction(owning_lob: str, creator_title: str) -> str | None:
    """Pick a seeded approver that satisfies the OPA approval_matrix for the creator."""
    by_lob: dict[str, dict[str, str]] = {
        "FICC": {
            "Analyst": "ficc-300",
            "Associate": "ficc-300",
            "Vice President": "ficc-400",
            "Managing Director": "ficc-500",
        },
        "FX": {
            "Analyst": "fx-300",
            "Associate": "fx-300",
        },
        "DESK_RATES": {
            "Analyst": "rates-201",
            "Associate": "rates-201",
        },
    }
    lob_key = "DESK_RATES" if owning_lob.startswith("DESK_") else owning_lob
    return by_lob.get(lob_key, {}).get(creator_title)


def _fetch_ui_instructions(settings: Settings, *, status: str | None = None) -> list[dict]:
    params = {"limit": 500}
    if status:
        params["status"] = status
    with httpx.Client(timeout=30.0) as client:
        response = client.get(f"{settings.ilm_url.rstrip('/')}/api/ui/instructions", params=params)
        response.raise_for_status()
        return response.json().get("instructions", [])


def auth_client(settings: Settings) -> ZitadelAuthClient:
    return ZitadelAuthClient(
        settings.zitadel_url,
        settings.zitadel_service_pat,
        host_header=settings.zitadel_host_header,
    )


def ilm_client(settings: Settings) -> InstructionLifecycleClient:
    return InstructionLifecycleClient(settings)


__all__ = [
    "Operation",
    "build_instruction_payload",
    "build_scenario",
    "build_seed_plan",
    "_approver_for_instruction",
    "_count_security_events",
    "_fetch_ui_instructions",
    "_session_for_user",
    "auth_client",
    "ilm_client",
    "load_users",
]
