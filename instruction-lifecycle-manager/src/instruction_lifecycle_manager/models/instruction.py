from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from instruction_lifecycle_manager.models.enums import (
    AccountIdentificationScheme,
    ChargeBearer,
    FinancialInstitutionIdScheme,
    InstructionStatus,
    InstructionType,
    WireScope,
    is_valid_owning_lob,
)

OwningLob = Annotated[str, Field(min_length=1, max_length=64)]


def _validate_owning_lob(value: str) -> str:
    if not is_valid_owning_lob(value):
        raise ValueError(
            "owning_lob must be a P&L profit center: FICC, FX, or DESK_<name>"
        )
    return value


class ActiveCurrencyAndAmount(BaseModel):
    """ISO 20022 ActiveCurrencyAndAmount — payment amount with currency."""

    currency: str = Field(min_length=3, max_length=3, description="ISO 4217")
    amount: Decimal | None = Field(
        default=None,
        gt=0,
        description="Omitted for standing templates without a fixed amount",
    )


class PaymentIdentification(BaseModel):
    """ISO 20022 PaymentIdentification7."""

    instruction_identification: str | None = Field(default=None, max_length=35)
    end_to_end_identification: str | None = Field(default=None, max_length=35)
    transaction_identification: str | None = Field(default=None, max_length=35)
    uetr: str | None = Field(default=None, max_length=36)
    clearing_system_reference: str | None = Field(default=None, max_length=35)


class PaymentTypeInformation(BaseModel):
    """ISO 20022 PaymentTypeInformation28 (subset)."""

    instruction_priority: str | None = Field(
        default=None,
        max_length=4,
        description="e.g. HIGH, NORM",
    )
    clearing_channel: str | None = Field(
        default=None,
        max_length=4,
        description="e.g. RTGS, MPNS, BOOK",
    )
    service_levels: list[str] = Field(
        default_factory=list,
        max_length=3,
        description="e.g. G001 for SWIFT gpi",
    )
    local_instrument: str | None = Field(default=None, max_length=35)
    category_purpose: str | None = Field(
        default=None,
        max_length=4,
        description="ISO 20022 ExternalCategoryPurpose1Code",
    )


class PostalAddress(BaseModel):
    """ISO 20022 PostalAddress24 (subset)."""

    street_name: str | None = Field(default=None, max_length=70)
    building_number: str | None = Field(default=None, max_length=16)
    post_code: str | None = Field(default=None, max_length=16)
    town_name: str | None = Field(default=None, max_length=35)
    country_sub_division: str | None = Field(default=None, max_length=35)
    country: str = Field(min_length=2, max_length=2, description="ISO 3166-1 alpha-2")
    address_lines: list[str] = Field(default_factory=list, max_length=3)


class PartyIdentification(BaseModel):
    """ISO 20022 PartyIdentification135 (subset)."""

    name: str = Field(min_length=1, max_length=140)
    postal_address: PostalAddress | None = None
    organisation_identification: str | None = Field(
        default=None,
        max_length=35,
        description="LEI or internal organisation id",
    )
    country_of_residence: str | None = Field(
        default=None,
        min_length=2,
        max_length=2,
        description="ISO 3166-1 alpha-2",
    )


class CashAccount(BaseModel):
    """ISO 20022 CashAccount38 (subset)."""

    identification_scheme: AccountIdentificationScheme
    identification: str = Field(min_length=1, max_length=34)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    name: str | None = Field(default=None, max_length=70)


class FinancialInstitutionIdentification(BaseModel):
    """ISO 20022 FinancialInstitutionIdentification18 (subset)."""

    scheme: FinancialInstitutionIdScheme
    identification: str = Field(min_length=1, max_length=35)
    name: str | None = Field(default=None, max_length=140)
    clearing_system_id: str | None = Field(
        default=None,
        max_length=35,
        description="e.g. USABA when using national clearing codes",
    )


class BranchAndFinancialInstitutionIdentification(BaseModel):
    """ISO 20022 BranchAndFinancialInstitutionIdentification6 (subset)."""

    financial_institution: FinancialInstitutionIdentification
    name: str | None = Field(default=None, max_length=140)
    country: str | None = Field(default=None, min_length=2, max_length=2)


class AgentWithAccount(BaseModel):
    """ISO 20022 agent paired with optional nostro/vostro account."""

    agent: BranchAndFinancialInstitutionIdentification
    account: CashAccount | None = None


class ChargesInformation(BaseModel):
    """ISO 20022 Charges7."""

    amount: ActiveCurrencyAndAmount
    agent: BranchAndFinancialInstitutionIdentification


class Purpose(BaseModel):
    """ISO 20022 Purpose2Choice."""

    code: str | None = Field(default=None, max_length=4)
    proprietary: str | None = Field(default=None, max_length=35)

    @model_validator(mode="after")
    def validate_choice(self) -> "Purpose":
        if self.code is None and self.proprietary is None:
            raise ValueError("purpose requires code or proprietary")
        return self


class StructuredRemittanceInformation(BaseModel):
    """ISO 20022 StructuredRemittanceInformation16 (subset)."""

    creditor_reference: str | None = Field(default=None, max_length=35)
    creditor_reference_type: str | None = Field(
        default=None,
        max_length=4,
        description="e.g. SCOR",
    )
    additional_information: list[str] = Field(default_factory=list, max_length=3)


class RemittanceInformation(BaseModel):
    """ISO 20022 RemittanceInformation16 (subset)."""

    unstructured: list[str] = Field(default_factory=list, max_length=3)
    structured: list[StructuredRemittanceInformation] = Field(default_factory=list)

    @field_validator("unstructured")
    @classmethod
    def validate_unstructured_lines(cls, value: list[str]) -> list[str]:
        for line in value:
            if len(line) > 140:
                raise ValueError(
                    "remittance unstructured lines must be at most 140 characters"
                )
        return value


class RelatedRemittanceInformation(BaseModel):
    """ISO 20022 RemittanceLocation7 (subset)."""

    remittance_identification: str | None = Field(default=None, max_length=35)


class RegulatoryReportingDetail(BaseModel):
    """ISO 20022 StructuredRegulatoryReporting3 (subset)."""

    type: str | None = Field(default=None, max_length=35)
    date: str | None = Field(default=None, max_length=10)
    country: str | None = Field(default=None, min_length=2, max_length=2)
    code: str | None = Field(default=None, max_length=10)
    amount: ActiveCurrencyAndAmount | None = None
    information: list[str] = Field(default_factory=list, max_length=3)


class RegulatoryReporting(BaseModel):
    """ISO 20022 RegulatoryReporting3 (subset)."""

    debit_credit_reporting_indicator: str | None = Field(
        default=None,
        max_length=4,
        description="CRED, DEBT, or BOTH",
    )
    authority_name: str | None = Field(default=None, max_length=140)
    authority_country: str | None = Field(default=None, min_length=2, max_length=2)
    details: list[RegulatoryReportingDetail] = Field(default_factory=list)


class TaxInformation(BaseModel):
    """ISO 20022 TaxInformation7 (subset)."""

    creditor_tax_id: str | None = Field(default=None, max_length=35)
    debtor_tax_id: str | None = Field(default=None, max_length=35)
    administrative_zone: str | None = Field(default=None, max_length=35)
    reference_number: str | None = Field(default=None, max_length=140)
    method: str | None = Field(default=None, max_length=35)
    total_taxable_base_amount: ActiveCurrencyAndAmount | None = None
    total_tax_amount: ActiveCurrencyAndAmount | None = None


class InstructionForAgent(BaseModel):
    """ISO 20022 InstructionForCreditorAgent1 / InstructionForNextAgent1."""

    code: str | None = Field(default=None, max_length=4)
    instruction_information: str | None = Field(default=None, max_length=140)


class FundingAccount(BaseModel):
    """Client funding account tagged to the owning profit center."""

    account_id: str = Field(min_length=1, max_length=64)
    account_name: str | None = Field(default=None, max_length=256)
    owning_lob: OwningLob

    @field_validator("owning_lob")
    @classmethod
    def validate_owning_lob(cls, value: str) -> str:
        return _validate_owning_lob(value)


class UserReference(BaseModel):
    user_id: str
    title: str
    lob: OwningLob | None = None
    roles: list[str]
    supervisor_id: str | None = None

    @field_validator("lob")
    @classmethod
    def validate_lob(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _validate_owning_lob(value)


class LifecycleEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: str(uuid4()))
    action: str
    actor_user_id: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    details: dict[str, Any] = Field(default_factory=dict)


def _agent_requires_bicfi(agent: BranchAndFinancialInstitutionIdentification) -> bool:
    return agent.financial_institution.scheme == FinancialInstitutionIdScheme.BICFI


class CashSettlementInstruction(BaseModel):
    """
    SSI settlement route template — accounts, agent chain, currency, and validity.

    Not a payment message: amounts, value dates, and payment IDs belong on a future
    payment service. Owned by a P&L profit center (FICC, FX, DESK_*).
    """

    model_config = ConfigDict(validate_assignment=True)

    instruction_id: str = Field(default_factory=lambda: str(uuid4()))
    instruction_type: InstructionType
    status: InstructionStatus = InstructionStatus.DRAFT
    owning_lob: OwningLob
    wire_scope: WireScope
    currency: str = Field(min_length=3, max_length=3, description="ISO 4217 route currency")

    funding_account: FundingAccount

    initiating_party: PartyIdentification | None = None
    ultimate_debtor: PartyIdentification | None = None
    debtor: PartyIdentification
    debtor_account: CashAccount
    debtor_agent: BranchAndFinancialInstitutionIdentification
    debtor_agent_account: CashAccount | None = None

    instructing_agent: BranchAndFinancialInstitutionIdentification | None = None
    instructed_agent: BranchAndFinancialInstitutionIdentification | None = None
    previous_instructing_agents: list[AgentWithAccount] = Field(
        default_factory=list,
        max_length=3,
        description="ISO 20022 PrvsInstgAgt1..3",
    )
    intermediary_agents: list[AgentWithAccount] = Field(
        default_factory=list,
        max_length=3,
        description=(
            "ISO 20022 IntrmyAgt1..3 — ordered correspondent route before creditor_agent"
        ),
    )

    creditor_agent: BranchAndFinancialInstitutionIdentification
    creditor_agent_account: CashAccount | None = None
    creditor: PartyIdentification
    creditor_account: CashAccount
    ultimate_creditor: PartyIdentification | None = None

    charge_bearer: ChargeBearer
    instructions_for_creditor_agent: list[InstructionForAgent] = Field(
        default_factory=list
    )
    instructions_for_next_agent: list[InstructionForAgent] = Field(default_factory=list)

    effective_date: datetime
    end_date: datetime

    created_by: UserReference
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    submitted_at: datetime | None = None
    approved_by: UserReference | None = None
    approved_at: datetime | None = None
    rejected_by: UserReference | None = None
    rejected_at: datetime | None = None
    rejection_reason: str | None = None
    suspended_by: str | None = None
    suspended_at: datetime | None = None
    last_used_at: datetime | None = None
    usage_count: int = 0
    lifecycle_events: list[LifecycleEvent] = Field(default_factory=list)

    @field_validator("owning_lob")
    @classmethod
    def validate_owning_lob(cls, value: str) -> str:
        return _validate_owning_lob(value)

    @model_validator(mode="after")
    def validate_route_rules(self) -> "CashSettlementInstruction":
        if self.funding_account.owning_lob != self.owning_lob:
            raise ValueError("funding_account.owning_lob must match instruction owning_lob")

        for index, hop in enumerate(self.intermediary_agents):
            if hop.account is not None and hop.agent is None:
                raise ValueError(
                    f"intermediary_agents[{index}] account requires agent"
                )

        for index, hop in enumerate(self.previous_instructing_agents):
            if hop.account is not None and hop.agent is None:
                raise ValueError(
                    f"previous_instructing_agents[{index}] account requires agent"
                )

        if self.wire_scope == WireScope.INTERNATIONAL:
            international_agents = [
                self.debtor_agent,
                self.creditor_agent,
                *(
                    hop.agent
                    for hop in self.intermediary_agents
                ),
                *(
                    hop.agent
                    for hop in self.previous_instructing_agents
                ),
            ]
            if self.instructing_agent is not None:
                international_agents.append(self.instructing_agent)
            if self.instructed_agent is not None:
                international_agents.append(self.instructed_agent)

            for agent in international_agents:
                if not _agent_requires_bicfi(agent):
                    raise ValueError(
                        "international wires require BICFI on all agents in the payment chain"
                    )

            if self.creditor_account.identification_scheme not in {
                AccountIdentificationScheme.IBAN,
                AccountIdentificationScheme.BBAN,
            }:
                raise ValueError(
                    "international wires require creditor_account IBAN or BBAN"
                )

        if self.wire_scope == WireScope.DOMESTIC:
            if (
                self.creditor_agent.financial_institution.scheme
                == FinancialInstitutionIdScheme.CLEARING_SYSTEM
                and not self.creditor_agent.financial_institution.clearing_system_id
            ):
                raise ValueError(
                    "domestic wires with CLEARING_SYSTEM require clearing_system_id"
                )


        return self

    def to_opa_instruction(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "type": self.instruction_type.value,
            "owning_lob": self.owning_lob,
            "effective_date": self.effective_date.isoformat() + "Z",
            "end_date": self.end_date.isoformat() + "Z",
            "created_by": {
                "user_id": self.created_by.user_id,
                "title": self.created_by.title,
                "supervisor_id": self.created_by.supervisor_id,
            },
            "suspended_by": self.suspended_by,
        }

    def to_opa_account(self) -> dict[str, Any]:
        return {"owning_lob": self.funding_account.owning_lob}
