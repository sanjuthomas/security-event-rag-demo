from __future__ import annotations

import random
from enum import StrEnum

from harness.config import Settings
from harness.fixtures import (
    SeedFile,
    SeedUser,
    build_instruction_payload,
    load_users,
)
from harness.ilm_client import InstructionLifecycleClient
from harness.payment_client import PaymentServiceClient
from harness.zitadel_auth import SessionCredentials, ZitadelAuthClient


class Operation(StrEnum):
    CREATE = "create"
    GET = "get"
    LIST = "list"
    SUBMIT = "submit"
    APPROVE = "approve"
    LIST_VERSIONS = "list_versions"


class PaymentOperation(StrEnum):
    CREATE_PAYMENT = "create_payment"
    SUBMIT_PAYMENT = "submit_payment"
    APPROVE_PAYMENT = "approve_payment"
    REJECT_PAYMENT = "reject_payment"


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


_INSTRUCTION_LOBS = ("FICC", "FX", "DESK_RATES")
_INSTRUCTION_TYPES = ("SINGLE_USE", "STANDING")
_LOB_CURRENCIES = {"FICC": "USD", "FX": "EUR", "DESK_RATES": "USD"}

_APPROVAL_MATRIX: dict[str, frozenset[str]] = {
    "Analyst": frozenset({"Associate", "Vice President", "Managing Director", "Partner"}),
    "Associate": frozenset({"Vice President", "Managing Director", "Partner"}),
    "Vice President": frozenset({"Managing Director", "Partner"}),
    "Managing Director": frozenset({"Partner"}),
}


def _middle_office_creators(seed: SeedFile) -> list[SeedUser]:
    return [user for user in seed.users if "INSTRUCTION_CREATOR" in user.roles]


def _instruction_approvers_for_lob(seed: SeedFile, owning_lob: str) -> list[SeedUser]:
    return [
        user
        for user in seed.users
        if "INSTRUCTION_APPROVER" in user.roles and user.lob == owning_lob
    ]


def _eligible_instruction_approvers(
    seed: SeedFile,
    *,
    owning_lob: str,
    creator_user_id: str,
    creator_title: str,
    creator_supervisor_id: str | None,
) -> list[str]:
    """Return approver user_ids that satisfy approval matrix and reporting-line rules."""
    allowed_titles = _APPROVAL_MATRIX.get(creator_title, frozenset())
    eligible: list[str] = []

    for approver in _instruction_approvers_for_lob(seed, owning_lob):
        if approver.title not in allowed_titles:
            continue
        if approver.user_id == creator_user_id:
            continue
        if creator_supervisor_id and approver.user_id == creator_supervisor_id:
            continue
        if approver.supervisor_id and approver.supervisor_id == creator_user_id:
            continue
        eligible.append(approver.user_id)

    return eligible


def _valid_instruction_seed_pairs(seed: SeedFile) -> list[tuple[str, str]]:
    """Return (creator_user_id, owning_lob) pairs with at least one eligible approver."""
    pairs: list[tuple[str, str]] = []
    for creator in _middle_office_creators(seed):
        for owning_lob in _INSTRUCTION_LOBS:
            if _eligible_instruction_approvers(
                seed,
                owning_lob=owning_lob,
                creator_user_id=creator.user_id,
                creator_title=creator.title,
                creator_supervisor_id=creator.supervisor_id,
            ):
                pairs.append((creator.user_id, owning_lob))
    return pairs


def build_seed_plan(
    count: int,
    *,
    seed: SeedFile | None = None,
    rng: random.Random | None = None,
) -> list[tuple[str, str, str, str]]:
    """Return randomized (user_id, owning_lob, instruction_type, currency) rows."""
    rng = rng or random.Random()
    pairs = _valid_instruction_seed_pairs(seed) if seed else [
        ("mo-100", "FICC"),
        ("mo-101", "FICC"),
        ("mo-100", "FX"),
        ("mo-101", "FX"),
    ]

    plan: list[tuple[str, str, str, str]] = []
    for _ in range(count):
        creator_id, owning_lob = rng.choice(pairs)
        instruction_type = rng.choice(_INSTRUCTION_TYPES)
        currency = _LOB_CURRENCIES[owning_lob]
        plan.append((creator_id, owning_lob, instruction_type, currency))
    return plan


def _approver_for_instruction(
    seed: SeedFile,
    owning_lob: str,
    creator_user_id: str,
    creator_title: str,
    creator_supervisor_id: str | None = None,
    *,
    rng: random.Random | None = None,
) -> str | None:
    """Pick a seeded approver that satisfies OPA approval matrix and hierarchy rules."""
    eligible = _eligible_instruction_approvers(
        seed,
        owning_lob=owning_lob,
        creator_user_id=creator_user_id,
        creator_title=creator_title,
        creator_supervisor_id=creator_supervisor_id,
    )
    if not eligible:
        return None
    rng = rng or random.Random()
    return rng.choice(eligible)


def _instruction_submitter(seed: SeedFile, *, rng: random.Random | None = None) -> str:
    """Middle-office creator used to submit draft instructions."""
    creators = _middle_office_creators(seed)
    if not creators:
        return "mo-100"
    rng = rng or random.Random()
    return rng.choice(creators).user_id


def _fetch_api_instructions(
    settings: Settings,
    session: SessionCredentials,
    *,
    status: str | None = None,
) -> list[dict]:
    response = ilm_client(settings).list_instructions(session, status=status, limit=500)
    response.raise_for_status()
    payload = response.json()
    if isinstance(payload, list):
        return payload
    return payload.get("instructions", [])


def auth_client(settings: Settings) -> ZitadelAuthClient:
    return ZitadelAuthClient(
        settings.zitadel_url,
        settings.zitadel_service_pat,
        host_header=settings.zitadel_host_header,
    )


def ilm_client(settings: Settings) -> InstructionLifecycleClient:
    return InstructionLifecycleClient(settings)


def payment_client(settings: Settings) -> PaymentServiceClient:
    return PaymentServiceClient(settings)


# ---------------------------------------------------------------------------
# Payment helpers
# ---------------------------------------------------------------------------

_AMOUNT_CLUB_LIMITS: dict[str, float] = {
    "UP_TO_100_MILLION_CLUB": 100_000_000.0,
    "UP_TO_1_BILLION_CLUB": 1_000_000_000.0,
    "UP_TO_100_BILLION_CLUB": 100_000_000_000.0,
}

_PAYMENT_AMOUNT_TIERS = (
    500_000.0,
    1_000_000.0,
    2_000_000.0,
    5_000_000.0,
    10_000_000.0,
    50_000_000.0,
    100_000_000.0,
)


def _user_amount_limit(user: SeedUser) -> float | None:
    limits = [_AMOUNT_CLUB_LIMITS[group] for group in user.groups if group in _AMOUNT_CLUB_LIMITS]
    return max(limits) if limits else None


def _payment_creators(seed: SeedFile) -> list[SeedUser]:
    return [
        user
        for user in seed.users
        if "PAYMENT_CREATOR" in user.roles and "MIDDLE_OFFICE" in user.groups
    ]


def _funding_approvers(seed: SeedFile) -> list[SeedUser]:
    return [
        user
        for user in seed.users
        if "FUNDING_APPROVER" in user.roles and "MIDDLE_OFFICE" in user.groups
    ]


def _eligible_payment_approvers(
    seed: SeedFile,
    *,
    owning_lob: str,
    amount: float,
    creator_user_id: str,
    creator_supervisor_id: str | None,
) -> list[str]:
    """Return approver user_ids that satisfy payment OPA rules from the seed file."""
    eligible: list[str] = []

    for approver in _funding_approvers(seed):
        if owning_lob not in approver.covering_lobs:
            continue
        limit = _user_amount_limit(approver)
        if limit is None or amount > limit:
            continue
        if approver.user_id == creator_user_id:
            continue
        if approver.supervisor_id and approver.supervisor_id == creator_user_id:
            continue
        eligible.append(approver.user_id)

    return eligible


def _eligible_payment_rejectors(seed: SeedFile, owning_lob: str) -> list[str]:
    return [
        user.user_id
        for user in _funding_approvers(seed)
        if owning_lob in user.covering_lobs
    ]


def _approver_for_payment(
    seed: SeedFile,
    payment: dict,
    *,
    rng: random.Random | None = None,
) -> str | None:
    created_by = payment.get("created_by") or {}
    eligible = _eligible_payment_approvers(
        seed,
        owning_lob=str(payment.get("owning_lob") or ""),
        amount=float(payment.get("amount") or 0),
        creator_user_id=str(created_by.get("user_id") or ""),
        creator_supervisor_id=created_by.get("supervisor_id"),
    )
    if not eligible:
        return None
    rng = rng or random.Random()
    return rng.choice(eligible)


def _rejector_for_payment(
    seed: SeedFile,
    payment: dict,
    *,
    rng: random.Random | None = None,
) -> str | None:
    eligible = _eligible_payment_rejectors(seed, str(payment.get("owning_lob") or ""))
    if not eligible:
        return None
    rng = rng or random.Random()
    return rng.choice(eligible)


def build_payment_seed_plan(
    count: int,
    *,
    seed: SeedFile | None = None,
    rng: random.Random | None = None,
) -> list[tuple[str, float]]:
    """Return randomized (creator_user_id, amount) rows within club limits."""
    rng = rng or random.Random()
    if seed is None:
        templates = [
            ("pay-101", 1_000_000.0),
            ("pay-102", 5_000_000.0),
            ("pay-103", 50_000_000.0),
        ]
        return [templates[index % len(templates)] for index in range(count)]

    creators = _payment_creators(seed)
    if not creators:
        return []

    plan: list[tuple[str, float]] = []
    for _ in range(count):
        creator = rng.choice(creators)
        limit = _user_amount_limit(creator) or 1_000_000.0
        valid_amounts = [amount for amount in _PAYMENT_AMOUNT_TIERS if amount <= limit]
        amount = rng.choice(valid_amounts or [min(limit, 1_000_000.0)])
        plan.append((creator.user_id, amount))
    return plan


def payment_submitter_for_lob(seed: SeedFile, lob: str, *, rng: random.Random | None = None) -> str:
    """Front-office user who may SUBMIT_PAYMENT for the given instruction LOB."""
    candidates = [
        user.user_id
        for user in seed.users
        if "PAYMENT_CREATOR" in user.roles and user.lob == lob
    ]
    if not candidates:
        raise ValueError(f"no front-office payment submitter configured for LOB {lob!r}")
    rng = rng or random.Random()
    return rng.choice(candidates)


def _fetch_api_payments(
    settings: Settings,
    session: SessionCredentials,
    *,
    status: str | None = None,
) -> list[dict]:
    response = payment_client(settings).list_payments(session, status=status, limit=500)
    response.raise_for_status()
    payload = response.json()
    if isinstance(payload, list):
        return payload
    return payload.get("payments", [])


def _count_payment_security_events(
    settings: Settings,
    *,
    severity: str | None = None,
    outcome: str | None = None,
) -> int:
    try:
        from pymongo import MongoClient
    except ImportError:
        return -1

    query: dict = {}
    if severity is not None:
        query["severity"] = severity
    if outcome is not None:
        query["event.outcome"] = outcome

    client = MongoClient(settings.mongodb_uri)
    try:
        collection = client[settings.security_events_database][
            settings.payment_security_events_collection
        ]
        return collection.count_documents(query)
    finally:
        client.close()


def _fetch_approved_instructions(
    settings: Settings,
    session: SessionCredentials,
) -> list[dict]:
    """Return STANDING and SINGLE_USE instructions from the ILM REST API."""
    all_instructions = _fetch_api_instructions(settings, session)
    return [
        i for i in all_instructions
        if i.get("status") in {"STANDING", "SINGLE_USE"}
    ]


def build_payment_scenario() -> list[tuple[PaymentOperation, str, bool, str]]:
    """Return (operation, user_id, expect_success, description) for the payment policy demo.

    Full DRAFT → SUBMIT → APPROVE lifecycle with OPA denial cases that emit ALERT events:
      1. pay-101 creates a FICC payment (→ DRAFT, INFO)
      2. pay-201 (approver only) tries to create           → DENY (ALERT)
      3. pay-101 (middle office) tries to submit            → DENY (ALERT)
      4. fo-ficc-101 submits the payment (→ SUBMITTED, INFO)
      5. pay-101 tries to approve own payment               → DENY (ALERT)
      6. pay-203 (FX-only) tries to approve                 → DENY (ALERT)
      7. pay-201 (FICC/FX VP) approves                      → OK (INFO)
    """
    return [
        (PaymentOperation.CREATE_PAYMENT,  "pay-101", True,  "middle office creates FICC payment (→ DRAFT)"),
        (PaymentOperation.CREATE_PAYMENT,  "pay-201", False, "funding approver cannot create payment (ALERT)"),
        (PaymentOperation.SUBMIT_PAYMENT,  "pay-101", False, "middle office cannot submit — not front-office LOB (ALERT)"),
        (PaymentOperation.SUBMIT_PAYMENT,  "fo-ficc-101", True,  "front office submits payment for approval (→ SUBMITTED)"),
        (PaymentOperation.APPROVE_PAYMENT, "pay-101", False, "creator cannot approve own payment (ALERT)"),
        (PaymentOperation.APPROVE_PAYMENT, "pay-203", False, "FX-only approver cannot approve FICC payment (ALERT)"),
        (PaymentOperation.APPROVE_PAYMENT, "pay-201", True,  "FICC/FX VP approver approves payment (→ APPROVED)"),
    ]


__all__ = [
    "Operation",
    "build_instruction_payload",
    "build_scenario",
    "build_seed_plan",
    "_approver_for_instruction",
    "_eligible_instruction_approvers",
    "_instruction_submitter",
    "_approver_for_payment",
    "_eligible_payment_approvers",
    "_rejector_for_payment",
    "build_payment_seed_plan",
    "payment_submitter_for_lob",
    "_count_security_events",
    "_count_payment_security_events",
    "_fetch_api_instructions",
    "_fetch_api_payments",
    "_session_for_user",
    "auth_client",
    "ilm_client",
    "load_users",
]
