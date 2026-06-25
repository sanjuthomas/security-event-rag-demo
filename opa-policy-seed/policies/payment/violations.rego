package payment.lifecycle

# ---------------------------------------------------------------------------
# violations — named denial reasons returned alongside allow=false.
#
# The payment service queries /v1/data/payment/lifecycle/violations and maps
# each key to a SecurityEvent message and severity level.
#
# Naming convention:
#   ALERT_*   →  SecurityEvent severity=ALERT  (must be escalated immediately)
#   <others>  →  SecurityEvent severity=WARNING (block and log, no escalation)
#
# The payment service can also query the convenience boolean `is_alert` to
# check whether at least one ALERT-severity violation is present without
# iterating the full set:
#   POST /v1/data/payment/lifecycle/is_alert
# ---------------------------------------------------------------------------

# ── Absolute payment ceiling (100 Billion) ──────────────────────────────────
# Rule:     No single payment may exceed 100 Billion USD under any circumstances.
#           The initiator must split the payment into smaller tranches.
# Severity: ALERT — represents a controls bypass or rogue large-value transfer.

violations["ALERT_AMOUNT_EXCEEDS_100B_LIMIT"] if {
    input.action in {"CREATE_PAYMENT", "APPROVE_PAYMENT"}
    exceeds_absolute_limit
}

# ── Subject club ceiling exceeded ────────────────────────────────────────────
# Rule:     The payment amount is within the absolute ceiling but exceeds the
#           ceiling delegated to this subject via their club group membership.
#           Example: a member of UP_TO_1_BILLION_CLUB attempting a 5B payment.
# Severity: ALERT — subject is acting beyond their delegated authority.

violations["ALERT_AMOUNT_EXCEEDS_SUBJECT_LIMIT"] if {
    input.action in {"CREATE_PAYMENT", "APPROVE_PAYMENT"}
    exceeds_subject_limit
}

# ── Subject has no club group at all ─────────────────────────────────────────
# Rule:     Subject holds the FUNDING_APPROVER role but has not been placed in
#           any payment-limit club.  This is an identity misconfiguration.
# Severity: WARNING — block the action; no amount limit can be validated.

violations["NO_LIMIT_GROUP_ASSIGNED"] if {
    input.action in {"CREATE_PAYMENT", "APPROVE_PAYMENT"}
    not has_any_limit_group
}

# ── Unapproved instruction ────────────────────────────────────────────────────
# Rule:     The instruction referenced by the payment has not completed the SSI
#           approval lifecycle (status must be STANDING or SINGLE_USE).
#           Using a DRAFT or PENDING instruction to route a real payment
#           bypasses the four-eyes control on instruction setup.
# Severity: ALERT — potential fraud or controls bypass.

violations["ALERT_UNAPPROVED_INSTRUCTION"] if {
    input.action in {"CREATE_PAYMENT", "APPROVE_PAYMENT"}
    not instruction_is_approved
}

# ── Expired instruction ───────────────────────────────────────────────────────
# Rule:     The instruction's end_date has passed.  Routing payments through an
#           expired instruction may violate regulatory standing instruction rules.
# Severity: ALERT — compliance breach.

violations["ALERT_EXPIRED_INSTRUCTION"] if {
    input.action in {"CREATE_PAYMENT", "APPROVE_PAYMENT"}
    input.payment.instruction_end_date != ""
    time.now_ns() >= time.parse_rfc3339_ns(input.payment.instruction_end_date)
}

# ── Approver not in MIDDLE_OFFICE group ───────────────────────────────────────
# Rule:     Subject holds FUNDING_APPROVER role but is not a member of the
#           MIDDLE_OFFICE group.  Holding the role alone is insufficient —
#           the approver must be an active middle-office analyst.
# Severity: ALERT — role assigned without the required group; potential
#           misconfiguration or privilege escalation attempt.

violations["ALERT_NOT_MIDDLE_OFFICE_GROUP"] if {
    input.action == "APPROVE_PAYMENT"
    has_role("FUNDING_APPROVER")
    not in_group("MIDDLE_OFFICE")
}

# ── Desk-coverage (LOB) violation ─────────────────────────────────────────────
# Rule:     The approver is in MIDDLE_OFFICE but their covering_lobs attribute
#           does not include the instruction's owning LOB.
#           Example: Mike covers ["FX"] but tries to approve a FICC payment —
#                    this must be blocked and logged.
# Severity: ALERT — potential cross-desk interference or collusion attempt.

violations["ALERT_LOB_COVERAGE_VIOLATION"] if {
    input.action == "APPROVE_PAYMENT"
    has_role("FUNDING_APPROVER")
    in_group("MIDDLE_OFFICE")
    not covers_lob(input.payment.instruction_owning_lob)
}

# ── Self-approval (segregation of duties) ────────────────────────────────────
# Rule:     The person who created the payment cannot also approve it.
#           This applies even when the subject holds BOTH PAYMENT_CREATOR and
#           FUNDING_APPROVER roles simultaneously.
# Severity: WARNING — four-eyes principle violation.

violations["SELF_APPROVAL"] if {
    input.action == "APPROVE_PAYMENT"
    not payment_creator_is_not_approver
}

# ---------------------------------------------------------------------------
# is_alert — convenience rule
#
# True when at least one ALERT-level violation is present.
# The payment service can query this directly to decide SecurityEvent severity
# without iterating the full violations set.
#
#   POST /v1/data/payment/lifecycle/is_alert   { "input": { ... } }
# ---------------------------------------------------------------------------

is_alert if {
    some v
    violations[v]
    startswith(v, "ALERT_")
}
