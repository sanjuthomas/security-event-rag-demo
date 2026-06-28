from __future__ import annotations

from typing import Any

from authz_client import PolicyDecision

from ps.models.api import Subject
from ps.models.enums import PaymentAction
from ps.models.payment import Payment

VIOLATION_LABELS: dict[str, str] = {
    "ALERT_AMOUNT_EXCEEDS_100B_LIMIT": "payment amount exceeds absolute 100B USD ceiling",
    "ALERT_AMOUNT_EXCEEDS_SUBJECT_LIMIT": "payment amount exceeds subject club limit",
    "NO_LIMIT_GROUP_ASSIGNED": "subject has no payment limit club group",
    "ALERT_UNAPPROVED_INSTRUCTION": "backing instruction is not approved",
    "ALERT_EXPIRED_INSTRUCTION": "backing instruction has expired",
    "ALERT_NOT_MIDDLE_OFFICE_GROUP": "FUNDING_APPROVER is not in MIDDLE_OFFICE group",
    "ALERT_LOB_COVERAGE_VIOLATION": "subject does not cover the instruction LOB",
    "SELF_APPROVAL": "payment creator cannot approve own payment",
    "ALERT_SUBORDINATE_APPROVING_CREATOR": "approver reports directly to payment creator",
}

_ALERT_PREFIX = "ALERT_"


def _display_name(subject: Subject) -> str:
    if subject.given_name and subject.family_name:
        return f"{subject.family_name}, {subject.given_name} ({subject.user_id})"
    return subject.user_id


def subject_at_decision(subject: Subject) -> dict[str, Any]:
    return {
        "user_id": subject.user_id,
        "given_name": subject.given_name,
        "family_name": subject.family_name,
        "title": subject.title,
        "roles": list(subject.roles),
        "groups": list(subject.groups),
        "covering_lobs": list(subject.covering_lobs),
        "lob": subject.lob,
        "supervisor_id": subject.supervisor_id,
    }


def payment_resource_context(
    payment: Payment,
    *,
    instruction_status: str = "",
    instruction_end_date: str = "",
) -> dict[str, Any]:
    return {
        "payment_id": payment.payment_id,
        "instruction_id": payment.instruction_id,
        "instruction_owning_lob": payment.owning_lob,
        "instruction_status": instruction_status or payment.instruction_type,
        "instruction_end_date": instruction_end_date,
        "payment_amount": payment.amount,
        "payment_currency": payment.currency,
        "payment_status": payment.status.value,
        "created_by_user_id": payment.created_by.user_id,
    }


def _primary_violation(violations: list[str]) -> str:
    for code in violations:
        if code.startswith(_ALERT_PREFIX):
            return code
    return violations[0] if violations else "POLICY_DENIED"


def build_authorization_block(
    decision: PolicyDecision,
    subject: Subject,
    action: PaymentAction,
    *,
    resource_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    actor = _display_name(subject)
    action_value = action.value

    if decision.allowed:
        basis = list(decision.allow_basis)
        summary = (
            f"{actor} was allowed to {action_value} because "
            + "; ".join(basis)
            if basis
            else f"{actor} was allowed to {action_value}"
        )
        return {
            "engine": "opa",
            "package": "payment.lifecycle",
            "action": action_value,
            "decision": "allow",
            "subject_at_decision": subject_at_decision(subject),
            "resource_context": resource_context or {},
            "allow_basis": basis,
            "violations": [],
            "is_alert": False,
            "summary": summary,
        }

    violations = list(decision.violations)
    primary = _primary_violation(violations)
    primary_label = VIOLATION_LABELS.get(primary, primary.replace("_", " ").lower())
    summary = f"{actor} was denied {action_value}: {primary_label}"
    if len(violations) > 1:
        extras = [
            VIOLATION_LABELS.get(code, code) for code in violations if code != primary
        ]
        if extras:
            summary += f" (also: {'; '.join(extras)})"

    return {
        "engine": "opa",
        "package": "payment.lifecycle",
        "action": action_value,
        "decision": "deny",
        "subject_at_decision": subject_at_decision(subject),
        "resource_context": resource_context or {},
        "allow_basis": [],
        "violations": violations,
        "is_alert": decision.is_alert,
        "summary": summary,
    }


def details_with_authorization(
    details: dict[str, Any] | None,
    authorization: dict[str, Any],
) -> dict[str, Any]:
    merged = dict(details or {})
    merged["authorization"] = authorization
    return merged
