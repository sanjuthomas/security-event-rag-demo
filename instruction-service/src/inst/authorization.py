from __future__ import annotations

from typing import Any

from authz_client import PolicyDecision

from inst.models.api import Subject
from inst.models.enums import LifecycleAction
from inst.models.instruction import CashSettlementInstruction

VIOLATION_LABELS: dict[str, str] = {
    "MISSING_ROLE_INSTRUCTION_CREATOR": "missing INSTRUCTION_CREATOR role",
    "MISSING_ROLE_INSTRUCTION_APPROVER": "missing INSTRUCTION_APPROVER role",
    "NOT_MIDDLE_OFFICE_GROUP": "not a member of MIDDLE_OFFICE group",
    "CREATOR_TITLE_INELIGIBLE": "creator title not eligible for instruction mutations",
    "ACCOUNT_LOB_MISMATCH": "funding account LOB does not match instruction LOB",
    "INVALID_PROFIT_CENTER": "instruction LOB is not a valid profit center",
    "INVALID_INSTRUCTION_TYPE": "instruction type must be STANDING or SINGLE_USE",
    "INVALID_INSTRUCTION_STATUS": "instruction status not valid for this action",
    "INSTRUCTION_DURATION_EXCEEDS_3Y": "instruction duration exceeds three-year limit",
    "INVALID_STATE_TRANSITION": "invalid lifecycle state transition",
    "ALERT_LOB_MISMATCH": "approver LOB does not match instruction LOB",
    "SELF_APPROVAL": "creator cannot approve own instruction",
    "ALERT_SUPERVISOR_APPROVING_SUBORDINATE": "approver is supervisor of the creator",
    "ALERT_SUBORDINATE_APPROVING_CREATOR": "approver reports directly to the creator",
    "ALERT_APPROVAL_MATRIX_VIOLATION": "approver title does not satisfy approval matrix",
    "SUSPEND_REQUIRES_MANAGING_DIRECTOR": "suspend requires Managing Director title",
    "SELF_REACTIVATION": "suspender cannot reactivate the same instruction",
    "ALERT_UNAUTHORIZED_SERVICE": "USE requires INSTRUCTION_MARKER service delegation",
    "ALERT_UNAPPROVED_INSTRUCTION": "instruction is not in an approved status",
    "ALERT_EXPIRED_INSTRUCTION": "instruction has expired",
    "VIEWER_ACCESS_DENIED": "subject lacks instruction viewer access",
}

_ALERT_PREFIX = "ALERT_"


def _display_name(subject: Subject) -> str:
    if subject.given_name and subject.family_name:
        return f"{subject.family_name}, {subject.given_name} ({subject.user_id})"
    return subject.user_id


def subject_at_decision(subject: Subject) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "user_id": subject.user_id,
        "given_name": subject.given_name,
        "family_name": subject.family_name,
        "title": subject.title,
        "roles": list(subject.roles),
        "groups": list(subject.groups),
        "lob": subject.lob,
        "supervisor_id": subject.supervisor_id,
        "delegated_by": subject.delegated_by,
        "delegated_by_roles": list(subject.delegated_by_roles),
    }
    return payload


def instruction_resource_context(instruction: CashSettlementInstruction) -> dict[str, Any]:
    return {
        "instruction_id": instruction.instruction_id,
        "owning_lob": instruction.owning_lob,
        "status": instruction.status.value,
        "instruction_type": instruction.instruction_type.value,
        "created_by_user_id": instruction.created_by.user_id,
        "created_by_title": instruction.created_by.title,
    }


def _primary_violation(violations: list[str]) -> str:
    for code in violations:
        if code.startswith(_ALERT_PREFIX):
            return code
    return violations[0] if violations else "POLICY_DENIED"


def build_authorization_block(
    decision: PolicyDecision,
    subject: Subject,
    action: LifecycleAction,
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
            "package": "instruction.lifecycle",
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
        "package": "instruction.lifecycle",
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
