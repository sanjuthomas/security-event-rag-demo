package payment.lifecycle

default allow := false

# ---------------------------------------------------------------------------
# CREATE_PAYMENT
#
# Only a PAYMENT_CREATOR may initiate a payment.
#   1. Subject must hold the PAYMENT_CREATOR role.
#   2. The backing instruction must already be approved (STANDING or SINGLE_USE).
#   3. The instruction must not be expired.
#   4. The payment amount must be positive and within the subject's club ceiling.
#
# The payment enters a PENDING state and awaits a separate APPROVE step.
# Note: a subject may hold both PAYMENT_CREATOR and FUNDING_APPROVER, but they
#       are still prohibited from approving a payment they personally created.
# ---------------------------------------------------------------------------

allow if {
    input.action == "CREATE_PAYMENT"

    has_role("PAYMENT_CREATOR")

    instruction_is_approved

    instruction_not_expired

    input.payment.amount > 0

    within_amount_limit
}

# ---------------------------------------------------------------------------
# APPROVE_PAYMENT
#
# Only a FUNDING_APPROVER may authorise a pending payment.
#   1. Subject must hold the FUNDING_APPROVER role.
#   2. Subject must be a member of the COVERING_LOBS group AND their
#      covering_lobs attribute must include the instruction's owning LOB.
#      (Desk-coverage rule: only the analyst covering FICC may approve
#       payments routed through FICC instructions — even if another analyst
#       also holds FUNDING_APPROVER.)
#   3. The backing instruction must still be approved and not expired.
#   4. Payment amount must be within the approver's club ceiling.
#   5. The approver must not be the same person who created the payment
#      (four-eyes / segregation of duties — applies even when the subject
#       holds both PAYMENT_CREATOR and FUNDING_APPROVER).
# ---------------------------------------------------------------------------

allow if {
    input.action == "APPROVE_PAYMENT"

    has_role("FUNDING_APPROVER")

    in_group("COVERING_LOBS")
    covers_lob(input.payment.instruction_owning_lob)

    instruction_is_approved

    instruction_not_expired

    within_amount_limit

    payment_creator_is_not_approver
}
