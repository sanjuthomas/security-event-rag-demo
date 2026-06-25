import re

from enum import StrEnum

# FICC, FX, or desk codes such as DESK_RATES, DESK_CREDIT
OWNING_LOB_PATTERN = re.compile(r"^(FICC|FX|DESK_[A-Z][A-Z0-9_]*)$")


class OwningProfitCenter(StrEnum):
    """P&L profit centers that own cash settlement instructions."""

    FICC = "FICC"
    FX = "FX"


def is_valid_owning_lob(value: str) -> bool:
    return bool(OWNING_LOB_PATTERN.match(value))


class InstructionType(StrEnum):
    """How long the instruction remains available for payment."""

    STANDING = "STANDING"
    SINGLE_USE = "SINGLE_USE"


class InstructionStatus(StrEnum):
    DRAFT = "DRAFT"
    PENDING = "PENDING"
    STANDING = "STANDING"
    SINGLE_USE = "SINGLE_USE"
    SUSPENDED = "SUSPENDED"
    REJECTED = "REJECTED"
    USED = "USED"
    EXPIRED = "EXPIRED"
    DELETED = "DELETED"


class WireScope(StrEnum):
    """Domestic or cross-border cash wire."""

    DOMESTIC = "DOMESTIC"
    INTERNATIONAL = "INTERNATIONAL"


class ChargeBearer(StrEnum):
    """ISO 20022 ChargeBearerType1Code."""

    DEBT = "DEBT"  # debtor pays all charges (OUR)
    CRED = "CRED"  # creditor pays (BEN)
    SHAR = "SHAR"  # shared (SHA)
    SLEV = "SLEV"  # service level charges


class AccountIdentificationScheme(StrEnum):
    """ISO 20022 AccountIdentification4Choice schemes."""

    IBAN = "IBAN"
    BBAN = "BBAN"
    PROPRIETARY = "PROPRIETARY"


class FinancialInstitutionIdScheme(StrEnum):
    """ISO 20022 FinancialInstitutionIdentification18 identification."""

    BICFI = "BICFI"
    CLEARING_SYSTEM = "CLEARING_SYSTEM"
    PROPRIETARY = "PROPRIETARY"


class LifecycleAction(StrEnum):
    CREATE = "CREATE"
    UPDATE = "UPDATE"
    DELETE = "DELETE"
    SUBMIT = "SUBMIT"
    APPROVE = "APPROVE"
    REJECT = "REJECT"
    SUSPEND = "SUSPEND"
    REACTIVATE = "REACTIVATE"
    USE = "USE"
    VIEW = "VIEW"


MUTATING_ACTIONS = frozenset(
    {
        LifecycleAction.CREATE,
        LifecycleAction.UPDATE,
        LifecycleAction.DELETE,
        LifecycleAction.SUBMIT,
        LifecycleAction.APPROVE,
        LifecycleAction.REJECT,
        LifecycleAction.SUSPEND,
        LifecycleAction.REACTIVATE,
        LifecycleAction.USE,
    }
)


class SecurityEventSeverity(StrEnum):
    """Normalized severity for SIEM correlation and alerting."""

    INFO = "INFO"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    ALERT = "ALERT"
    CRITICAL = "CRITICAL"


class SecurityEventOutcome(StrEnum):
    SUCCESS = "success"
    FAILURE = "failure"
