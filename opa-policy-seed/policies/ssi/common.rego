package ssi.instruction_lifecycle

has_role(role) if {
    role in input.subject.roles
}

is_middle_office if {
    "MIDDLE_OFFICE" in input.subject.roles
}

creator_eligible if {
    input.subject.title in {
        "Analyst",
        "Associate",
        "Vice President",
        "Managing Director"
    }
}

account_owning_lob_matches_instruction if {
    input.account.owning_lob == input.instruction.owning_lob
}

same_lob_as_instruction if {
    input.subject.lob == input.instruction.owning_lob
}

creator_is_not_approver if {
    input.subject.user_id != input.instruction.created_by.user_id
}

not_supervisor_of_creator if {
    input.subject.user_id != input.instruction.created_by.supervisor_id
}

within_three_year_limit if {
    start := time.parse_rfc3339_ns(input.instruction.effective_date)
    finish := time.parse_rfc3339_ns(input.instruction.end_date)

    finish > start

    finish - start <= time.parse_duration_ns("26280h")
}

not_expired if {
    time.now_ns() < time.parse_rfc3339_ns(input.instruction.end_date)
}

is_valid_profit_center if {
    input.instruction.owning_lob == "FICC"
}

is_valid_profit_center if {
    input.instruction.owning_lob == "FX"
}

is_valid_profit_center if {
    startswith(input.instruction.owning_lob, "DESK_")
}
