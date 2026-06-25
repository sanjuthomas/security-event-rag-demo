package ssi.instruction_lifecycle

valid_transition if {
    input.action == "UPDATE"
    input.instruction.status == "DRAFT"
}

valid_transition if {
    input.action == "DELETE"
    input.instruction.status in {
        "DRAFT",
        "PENDING"
    }
}

valid_transition if {
    input.action == "SUBMIT"
    input.instruction.status == "DRAFT"
}

valid_transition if {
    input.action == "APPROVE"
    input.instruction.status == "PENDING"
}

valid_transition if {
    input.action == "REJECT"
    input.instruction.status == "PENDING"
}

valid_transition if {
    input.action == "SUSPEND"
    input.instruction.status in {
        "STANDING",
        "SINGLE_USE"
    }
}

valid_transition if {
    input.action == "REACTIVATE"
    input.instruction.status == "SUSPENDED"
}
