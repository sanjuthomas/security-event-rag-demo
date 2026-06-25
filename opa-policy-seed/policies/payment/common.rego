package payment.lifecycle

# ---------------------------------------------------------------------------
# Subject helpers
# ---------------------------------------------------------------------------

# True when the acting subject holds the named role.
has_role(role) if {
    role in input.subject.roles
}

# True when the acting subject is a member of the named ZITADEL group.
in_group(group) if {
    group in input.subject.groups
}

# A subject covers a LOB when that LOB appears in their covering_lobs
# metadata attribute (set in ZITADEL).  An approver may cover more than one LOB.
# MIDDLE_OFFICE group membership is verified separately in lifecycle.rego so
# this predicate stays a pure data check.
# Example: John covers ["FICC","FX"] → he can approve payments for both desks.
covers_lob(lob) if {
    lob in input.subject.covering_lobs
}

# ---------------------------------------------------------------------------
# Instruction helpers
# ---------------------------------------------------------------------------

# A payment may only be initiated or approved against a fully-approved
# instruction.  In the SSI lifecycle that means status STANDING or SINGLE_USE.
instruction_is_approved if {
    input.payment.instruction_status in {"STANDING", "SINGLE_USE"}
}

# Instruction must not be expired.
instruction_not_expired if {
    input.payment.instruction_end_date != ""
    time.now_ns() < time.parse_rfc3339_ns(input.payment.instruction_end_date)
}

# No end_date means no expiry constraint.
instruction_not_expired if {
    input.payment.instruction_end_date == ""
}

# ---------------------------------------------------------------------------
# Payment helpers
# ---------------------------------------------------------------------------

# Segregation of duties: the person who created the payment cannot also be
# its approver.
payment_creator_is_not_approver if {
    input.subject.user_id != input.payment.created_by.user_id
}
