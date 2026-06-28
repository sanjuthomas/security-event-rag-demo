package instruction.lifecycle

# ---------------------------------------------------------------------------
# violations — named denial reasons returned alongside allow=false.
#
# ILM queries /v1/data/instruction/lifecycle/violations and maps
# each key to a SecurityEvent message and severity level.
#
# Naming convention:
#   ALERT_*   →  escalation-worthy violation (is_alert=true in authorization details)
#   <others>  →  policy denial recorded as SecurityEvent severity=ALERT
#
# ILM can also query the convenience boolean `is_alert` to check whether at
# least one ALERT-severity violation is present without iterating the full set:
#   POST /v1/data/instruction/lifecycle/is_alert
# ---------------------------------------------------------------------------

# ── Missing functional role ───────────────────────────────────────────────────

violations["MISSING_ROLE_INSTRUCTION_CREATOR"] if {
    input.action in {"CREATE", "UPDATE", "DELETE", "SUBMIT"}
    not has_role("INSTRUCTION_CREATOR")
}

violations["MISSING_ROLE_INSTRUCTION_APPROVER"] if {
    input.action in {"APPROVE", "REJECT", "SUSPEND", "REACTIVATE"}
    not has_role("INSTRUCTION_APPROVER")
}

# ── Middle-office group required for creator actions ──────────────────────────
# Rule:     CREATE / UPDATE / DELETE / SUBMIT require MIDDLE_OFFICE group
#           membership in addition to INSTRUCTION_CREATOR.
# Denial → ALERT security event — role without required group; likely misconfiguration.

violations["NOT_MIDDLE_OFFICE_GROUP"] if {
    input.action in {"CREATE", "UPDATE", "DELETE", "SUBMIT"}
    has_role("INSTRUCTION_CREATOR")
    not is_middle_office
}

# ── Creator title eligibility ─────────────────────────────────────────────────
# Rule:     Only Analyst through Managing Director may create or mutate drafts.
# Denial → ALERT security event — title outside the permitted creator band.

violations["CREATOR_TITLE_INELIGIBLE"] if {
    input.action in {"CREATE", "UPDATE", "DELETE", "SUBMIT"}
    has_role("INSTRUCTION_CREATOR")
    is_middle_office
    not creator_eligible
}

# ── Account LOB must match instruction LOB ────────────────────────────────────
# Rule:     The funding account's owning LOB must match the instruction LOB.
# Denial → ALERT security event — cross-LOB account routing attempt.

violations["ACCOUNT_LOB_MISMATCH"] if {
    input.action in {"CREATE", "UPDATE", "DELETE"}
    has_role("INSTRUCTION_CREATOR")
    is_middle_office
    not account_owning_lob_matches_instruction
}

# ── Invalid profit centre ─────────────────────────────────────────────────────
# Rule:     owning_lob must be FICC, FX, or DESK_<name>.
# Denial → ALERT security event — instruction scoped to an unknown LOB.

violations["INVALID_PROFIT_CENTER"] if {
    input.action in {
        "CREATE", "UPDATE", "DELETE", "SUBMIT",
        "APPROVE", "REJECT", "SUSPEND", "REACTIVATE",
        "USE", "VIEW",
    }
    not is_valid_profit_center
}

# ── Instruction type (CREATE only) ────────────────────────────────────────────

violations["INVALID_INSTRUCTION_TYPE"] if {
    input.action == "CREATE"
    not input.instruction.type in {"STANDING", "SINGLE_USE"}
}

# ── Instruction status for creator mutations ──────────────────────────────────

violations["INVALID_INSTRUCTION_STATUS"] if {
    input.action == "CREATE"
    input.instruction.status != "DRAFT"
}

violations["INVALID_INSTRUCTION_STATUS"] if {
    input.action in {"UPDATE", "SUBMIT"}
    input.instruction.status != "DRAFT"
}

violations["INVALID_INSTRUCTION_STATUS"] if {
    input.action == "DELETE"
    not input.instruction.status in {"DRAFT", "PENDING"}
}

# ── Three-year duration ceiling ───────────────────────────────────────────────
# Rule:     effective_date to end_date must be positive and ≤ 3 years.
# Denial → ALERT security event — standing instruction exceeds permitted horizon.

violations["INSTRUCTION_DURATION_EXCEEDS_3Y"] if {
    input.action in {"CREATE", "UPDATE", "APPROVE"}
    not within_three_year_limit
}

# ── Invalid lifecycle transition ──────────────────────────────────────────────

violations["INVALID_STATE_TRANSITION"] if {
    input.action in {"UPDATE", "DELETE", "SUBMIT", "APPROVE", "REJECT", "SUSPEND", "REACTIVATE"}
    not valid_transition
}

# ── Approver LOB mismatch ─────────────────────────────────────────────────────
# Rule:     INSTRUCTION_APPROVER subject.lob must equal instruction.owning_lob.
# Severity: ALERT — cross-desk approval attempt.

violations["ALERT_LOB_MISMATCH"] if {
    input.action in {"APPROVE", "REJECT", "SUSPEND", "REACTIVATE"}
    has_role("INSTRUCTION_APPROVER")
    not same_lob_as_instruction
}

# ── Self-approval (segregation of duties) ────────────────────────────────────
# Rule:     The instruction creator cannot approve their own instruction.
# Denial → ALERT security event — four-eyes principle violation.

violations["SELF_APPROVAL"] if {
    input.action == "APPROVE"
    not creator_is_not_approver
}

# ── Approver is creator's direct supervisor ───────────────────────────────────
# Rule:     A manager must not approve an instruction created by their direct
#           report — undue influence over the approval chain.
# Severity: ALERT — reporting-line conflict.

violations["ALERT_SUPERVISOR_APPROVING_SUBORDINATE"] if {
    input.action == "APPROVE"
    has_role("INSTRUCTION_APPROVER")
    not not_supervisor_of_creator
}

# ── Subordinate approving creator's instruction ───────────────────────────────
# Rule:     If the approver reports directly to the creator, the approval must
#           be blocked (inversion-of-control / chain-of-command conflict).
# Severity: ALERT — potential coercion or collusion.

violations["ALERT_SUBORDINATE_APPROVING_CREATOR"] if {
    input.action == "APPROVE"
    has_role("INSTRUCTION_APPROVER")
    not approver_not_subordinate_of_creator
}

# ── Approval matrix (title seniority) ─────────────────────────────────────────
# Rule:     Approver title must be senior to creator title per approval_matrix.
# Severity: ALERT — junior title attempting to approve senior creator's work.

violations["ALERT_APPROVAL_MATRIX_VIOLATION"] if {
    input.action == "APPROVE"
    has_role("INSTRUCTION_APPROVER")
    same_lob_as_instruction
    not approver_is_allowed
}

# ── SUSPEND requires Managing Director title ────────────────────────────────────

violations["SUSPEND_REQUIRES_MANAGING_DIRECTOR"] if {
    input.action == "SUSPEND"
    has_role("INSTRUCTION_APPROVER")
    input.subject.title != "Managing Director"
}

# ── Self-reactivation after suspend ───────────────────────────────────────────
# Rule:     The user who suspended an instruction may not reactivate it.
# Denial → ALERT security event — segregation on suspend/reactivate pair.

violations["SELF_REACTIVATION"] if {
    input.action == "REACTIVATE"
    has_role("INSTRUCTION_APPROVER")
    input.subject.user_id == input.instruction.suspended_by
}

# ── USE — service delegation gate ─────────────────────────────────────────────
# Rule:     Only a service account with INSTRUCTION_MARKER (via OBO delegation)
#           may mark an instruction as used during payment creation.
# Severity: ALERT — unauthorized service or direct human call.

violations["ALERT_UNAUTHORIZED_SERVICE"] if {
    input.action == "USE"
    not "INSTRUCTION_MARKER" in input.subject.delegated_by_roles
}

# ── USE — instruction must be approved and not expired ────────────────────────

violations["ALERT_UNAPPROVED_INSTRUCTION"] if {
    input.action == "USE"
    not input.instruction.status in {"STANDING", "SINGLE_USE"}
}

violations["ALERT_EXPIRED_INSTRUCTION"] if {
    input.action == "USE"
    not not_expired
}

# ── VIEW / USE — read access ──────────────────────────────────────────────────

violations["VIEWER_ACCESS_DENIED"] if {
    input.action in {"VIEW", "USE"}
    not has_viewer_access
}

# ---------------------------------------------------------------------------
# is_alert — convenience rule
#
# True when at least one ALERT-level violation is present.
# ILM can query this directly to decide SecurityEvent severity without
# iterating the full violations set.
#
#   POST /v1/data/instruction/lifecycle/is_alert   { "input": { ... } }
# ---------------------------------------------------------------------------

is_alert if {
    some v
    violations[v]
    startswith(v, "ALERT_")
}
