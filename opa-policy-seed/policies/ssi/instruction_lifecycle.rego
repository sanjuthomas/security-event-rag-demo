package ssi.instruction_lifecycle

default allow := false

#
# CREATE — middle office creates on behalf of a profit center
#

allow if {
    input.action == "CREATE"

    has_role("INSTRUCTION_CREATOR")

    is_middle_office

    creator_eligible

    account_owning_lob_matches_instruction

    is_valid_profit_center

    input.instruction.status == "DRAFT"

    input.instruction.type in {
        "STANDING",
        "SINGLE_USE"
    }

    within_three_year_limit
}

#
# UPDATE — middle office edits draft instructions
#

allow if {
    input.action == "UPDATE"

    has_role("INSTRUCTION_CREATOR")

    is_middle_office

    creator_eligible

    account_owning_lob_matches_instruction

    is_valid_profit_center

    input.instruction.status == "DRAFT"

    within_three_year_limit
}

#
# DELETE — soft delete draft or pending instructions
#

allow if {
    input.action == "DELETE"

    has_role("INSTRUCTION_CREATOR")

    is_middle_office

    creator_eligible

    account_owning_lob_matches_instruction

    is_valid_profit_center

    input.instruction.status in {
        "DRAFT",
        "PENDING"
    }
}

#
# SUBMIT — middle office submits
#

allow if {
    input.action == "SUBMIT"

    has_role("INSTRUCTION_CREATOR")

    is_middle_office

    valid_transition
}

#
# APPROVE — profit center approver
#

allow if {
    input.action == "APPROVE"

    has_role("INSTRUCTION_APPROVER")

    same_lob_as_instruction

    is_valid_profit_center

    valid_transition

    creator_is_not_approver

    not_supervisor_of_creator

    approver_is_allowed

    within_three_year_limit
}

#
# REJECT
#

allow if {
    input.action == "REJECT"

    has_role("INSTRUCTION_APPROVER")

    same_lob_as_instruction

    is_valid_profit_center

    valid_transition
}

#
# SUSPEND
#

allow if {
    input.action == "SUSPEND"

    has_role("INSTRUCTION_APPROVER")

    input.subject.title == "Managing Director"

    same_lob_as_instruction

    is_valid_profit_center

    valid_transition
}

#
# REACTIVATE
#

allow if {
    input.action == "REACTIVATE"

    has_role("INSTRUCTION_APPROVER")

    same_lob_as_instruction

    is_valid_profit_center

    valid_transition

    input.subject.user_id != input.instruction.suspended_by
}

#
# USE — profit center executes payment
#

allow if {
    input.action == "USE"

    same_lob_as_instruction

    is_valid_profit_center

    not_expired

    input.instruction.status in {
        "STANDING",
        "SINGLE_USE"
    }
}

#
# VIEW — middle office or owning profit center
#

allow if {
    input.action == "VIEW"

    is_middle_office

    is_valid_profit_center
}

allow if {
    input.action == "VIEW"

    same_lob_as_instruction

    is_valid_profit_center
}
