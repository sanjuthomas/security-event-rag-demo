package ssi.instruction_lifecycle

approval_matrix := {
    "Analyst": [
        "Associate",
        "Vice President",
        "Managing Director",
        "Partner"
    ],

    "Associate": [
        "Vice President",
        "Managing Director",
        "Partner"
    ],

    "Vice President": [
        "Managing Director",
        "Partner"
    ],

    "Managing Director": [
        "Partner"
    ]
}

approver_is_allowed if {
    allowed := approval_matrix[input.instruction.created_by.title]
    input.subject.title in allowed
}