package payment.lifecycle

# ---------------------------------------------------------------------------
# Absolute ceiling
#
# No individual payment — regardless of who submits or approves it — may
# exceed 100 billion USD.  Payments above this threshold MUST be split.
# A violation triggers an ALERT-level security event.
# ---------------------------------------------------------------------------

absolute_limit := 100000000000   # 100,000,000,000 USD

# ---------------------------------------------------------------------------
# Club ceiling map
#
# ZITADEL group name  →  maximum authorized payment amount (USD)
# Users belong to exactly one club; the group assignment is managed in ZITADEL.
# ---------------------------------------------------------------------------

club_limit := {
    "UP_TO_100_MILLION_CLUB": 100000000,       #       100,000,000 USD
    "UP_TO_1_BILLION_CLUB":   1000000000,      #     1,000,000,000 USD
    "UP_TO_100_BILLION_CLUB": 100000000000,    # 100,000,000,000 USD
}

# ---------------------------------------------------------------------------
# Effective subject ceiling
#
# Derived from the highest club limit the subject qualifies for.
# A well-configured user belongs to exactly one club; if somehow they hold
# multiple club memberships we take the maximum (benefit-of-the-doubt).
# ---------------------------------------------------------------------------

subject_limit := limit if {
    limits := [v |
        some g
        club_limit[g]
        in_group(g)
        v := club_limit[g]
    ]
    count(limits) > 0
    limit := max(limits)
}

# True when the subject belongs to at least one club.
has_any_limit_group if {
    some g
    club_limit[g]
    in_group(g)
}

# ---------------------------------------------------------------------------
# Amount guard predicates
# ---------------------------------------------------------------------------

# Payment is within BOTH the subject's club ceiling AND the absolute ceiling.
within_amount_limit if {
    has_any_limit_group
    input.payment.amount <= subject_limit
    input.payment.amount <= absolute_limit
}

# Amount strictly exceeds the absolute 100B ceiling.
exceeds_absolute_limit if {
    input.payment.amount > absolute_limit
}

# Amount is under the absolute ceiling but above the subject's club ceiling.
exceeds_subject_limit if {
    not exceeds_absolute_limit
    has_any_limit_group
    input.payment.amount > subject_limit
}
