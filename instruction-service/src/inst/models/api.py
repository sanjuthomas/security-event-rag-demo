from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from inst.models.enums import InstructionType, WireScope
from inst.models.instruction import (
    AgentWithAccount,
    BranchAndFinancialInstitutionIdentification,
    CashAccount,
    CashSettlementInstruction,
    ChargeBearer,
    FundingAccount,
    InstructionForAgent,
    PartyIdentification,
    UserReference,
)


class Subject(BaseModel):
    """Caller identity passed on each request (demo via HTTP headers)."""

    user_id: str
    given_name: str | None = None
    family_name: str | None = None
    title: str
    lob: str | None = None
    roles: list[str] = Field(min_length=1)
    groups: list[str] = Field(default_factory=list)
    supervisor_id: str | None = None
    # Populated when the request arrives via an On-Behalf-Of delegation
    # (e.g. payment-service calling ILM on behalf of a human user).
    delegated_by: str | None = None
    # Roles held by the delegating service account.  Empty for direct calls.
    # OPA policies can gate actions on specific service roles (e.g. INSTRUCTION_MARKER)
    # so that certain operations can ONLY be invoked via trusted service delegation.
    delegated_by_roles: list[str] = Field(default_factory=list)

    def to_opa_subject(self) -> dict:
        payload = {
            "user_id": self.user_id,
            "title": self.title,
            "roles": self.roles,
            "groups": self.groups,
        }
        if self.lob is not None:
            payload["lob"] = self.lob
        if self.supervisor_id is not None:
            payload["supervisor_id"] = self.supervisor_id
        # Always include delegated_by_roles so OPA can rely on its presence.
        # It is an empty list for non-OBO calls, which causes role checks to fail.
        payload["delegated_by_roles"] = self.delegated_by_roles
        return payload


class CreateInstructionRequest(BaseModel):
    instruction_type: InstructionType
    owning_lob: str
    wire_scope: WireScope
    currency: str = Field(min_length=3, max_length=3)
    funding_account: FundingAccount
    initiating_party: PartyIdentification | None = None
    ultimate_debtor: PartyIdentification | None = None
    debtor: PartyIdentification
    debtor_account: CashAccount
    debtor_agent: BranchAndFinancialInstitutionIdentification
    debtor_agent_account: CashAccount | None = None
    instructing_agent: BranchAndFinancialInstitutionIdentification | None = None
    instructed_agent: BranchAndFinancialInstitutionIdentification | None = None
    previous_instructing_agents: list[AgentWithAccount] = Field(default_factory=list, max_length=3)
    intermediary_agents: list[AgentWithAccount] = Field(default_factory=list, max_length=3)
    creditor_agent: BranchAndFinancialInstitutionIdentification
    creditor_agent_account: CashAccount | None = None
    creditor: PartyIdentification
    creditor_account: CashAccount
    ultimate_creditor: PartyIdentification | None = None
    charge_bearer: ChargeBearer
    instructions_for_creditor_agent: list[InstructionForAgent] = Field(default_factory=list)
    instructions_for_next_agent: list[InstructionForAgent] = Field(default_factory=list)
    effective_date: str
    end_date: str

    @model_validator(mode="after")
    def validate_create_request(self) -> "CreateInstructionRequest":
        CashSettlementInstruction(
            instruction_type=self.instruction_type,
            owning_lob=self.owning_lob,
            wire_scope=self.wire_scope,
            currency=self.currency,
            funding_account=self.funding_account,
            initiating_party=self.initiating_party,
            ultimate_debtor=self.ultimate_debtor,
            debtor=self.debtor,
            debtor_account=self.debtor_account,
            debtor_agent=self.debtor_agent,
            debtor_agent_account=self.debtor_agent_account,
            instructing_agent=self.instructing_agent,
            instructed_agent=self.instructed_agent,
            previous_instructing_agents=self.previous_instructing_agents,
            intermediary_agents=self.intermediary_agents,
            creditor_agent=self.creditor_agent,
            creditor_agent_account=self.creditor_agent_account,
            creditor=self.creditor,
            creditor_account=self.creditor_account,
            ultimate_creditor=self.ultimate_creditor,
            charge_bearer=self.charge_bearer,
            instructions_for_creditor_agent=self.instructions_for_creditor_agent,
            instructions_for_next_agent=self.instructions_for_next_agent,
            effective_date=_parse_datetime(self.effective_date),
            end_date=_parse_datetime(self.end_date),
            created_by=UserReference(
                user_id="validation",
                title="Analyst",
                lob=None,
                roles=["INSTRUCTION_CREATOR", "MIDDLE_OFFICE"],
            ),
        )
        return self


def _parse_datetime(value: str) -> datetime:
    normalized = value.replace("Z", "+00:00")
    return datetime.fromisoformat(normalized).replace(tzinfo=None)


class RejectInstructionRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=1024)


class UseInstructionRequest(BaseModel):
    payment_reference: str = Field(min_length=1, max_length=128)
    end_to_end_identification: str | None = Field(default=None, max_length=35)


UpdateInstructionRequest = CreateInstructionRequest


class DeleteInstructionRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=1024)


class InstructionResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    instruction_id: str
    version_number: int
    record_in: str = Field(serialization_alias="in")
    record_out: str | None = Field(default=None, serialization_alias="out")
    instruction_type: str
    status: str
    owning_lob: str
    wire_scope: str
    currency: str
    funding_account: FundingAccount
    initiating_party: PartyIdentification | None
    ultimate_debtor: PartyIdentification | None
    debtor: PartyIdentification
    debtor_account: CashAccount
    debtor_agent: BranchAndFinancialInstitutionIdentification
    debtor_agent_account: CashAccount | None
    instructing_agent: BranchAndFinancialInstitutionIdentification | None
    instructed_agent: BranchAndFinancialInstitutionIdentification | None
    previous_instructing_agents: list[AgentWithAccount]
    intermediary_agents: list[AgentWithAccount]
    creditor_agent: BranchAndFinancialInstitutionIdentification
    creditor_agent_account: CashAccount | None
    creditor: PartyIdentification
    creditor_account: CashAccount
    ultimate_creditor: PartyIdentification | None
    charge_bearer: str
    instructions_for_creditor_agent: list[InstructionForAgent]
    instructions_for_next_agent: list[InstructionForAgent]
    effective_date: str
    end_date: str
    created_by: UserReference
    created_at: str
    updated_at: str
    submitted_at: str | None = None
    approved_by: UserReference | None = None
    approved_at: str | None = None
    rejected_by: UserReference | None = None
    rejected_at: str | None = None
    rejection_reason: str | None = None
    suspended_by: str | None = None
    suspended_at: str | None = None
    last_used_at: str | None = None
    usage_count: int


class EligibleApproverResponse(BaseModel):
    user_id: str
    display_name: str
    title: str
    allow_basis: list[str] = Field(default_factory=list)


class InstructionEligibleApproversResponse(BaseModel):
    instruction_id: str
    instruction_status: str
    instruction_type: str
    owning_lob: str
    created_by_user_id: str
    created_by_title: str
    evaluated_at: str
    eligible: list[EligibleApproverResponse]
    candidates_evaluated: int
