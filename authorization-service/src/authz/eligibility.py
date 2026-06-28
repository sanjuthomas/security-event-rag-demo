from __future__ import annotations

from datetime import UTC, datetime

from authz.instruction_opa import build_instruction_opa_context
from authz.models import (
    EligibleApprover,
    InstructionEligibleApproversEvaluateRequest,
    InstructionEligibleApproversResponse,
    PaymentEligibilityContext,
    PaymentEligibleApproversEvaluateRequest,
    PaymentEligibleApproversResponse,
    PaymentRecord,
    UserReference,
)
from authz.opa import OpaClient
from authz.user_directory import UserDirectory


class EligibilityService:
    def __init__(
        self,
        *,
        users: UserDirectory,
        opa: OpaClient,
    ) -> None:
        self._users = users
        self._opa = opa

    @staticmethod
    def _payment_record(context: PaymentEligibilityContext) -> PaymentRecord:
        return PaymentRecord(
            payment_id=context.payment_id,
            instruction_id=context.instruction_id,
            instruction_version=context.instruction_version,
            status=context.status,
            amount=context.amount,
            currency=context.currency,
            owning_lob=context.owning_lob,
            created_by=UserReference(
                user_id=context.created_by_user_id,
                supervisor_id=context.created_by_supervisor_id,
            ),
        )

    async def eligible_approvers_for_payment(
        self,
        request: PaymentEligibleApproversEvaluateRequest,
    ) -> PaymentEligibleApproversResponse:
        payment = self._payment_record(request.payment)
        instruction_status = request.instruction_status
        instruction_end_date = request.instruction_end_date

        candidates = self._users.funding_approver_candidates(payment.owning_lob)
        eligible: list[EligibleApprover] = []

        for candidate in candidates:
            allowed, basis = await self._opa.can_approve_payment(
                candidate,
                payment,
                instruction_end_date=instruction_end_date,
                instruction_status=instruction_status,
            )
            if allowed:
                eligible.append(
                    EligibleApprover(
                        user_id=candidate.user_id,
                        display_name=candidate.display_name,
                        title=candidate.title,
                        allow_basis=basis,
                    )
                )

        eligible.sort(key=lambda row: row.display_name)

        return PaymentEligibleApproversResponse(
            payment_id=payment.payment_id,
            instruction_id=payment.instruction_id,
            payment_status=payment.status,
            amount=payment.amount,
            currency=payment.currency,
            owning_lob=payment.owning_lob,
            instruction_status=instruction_status,
            evaluated_at=datetime.now(UTC).isoformat(),
            eligible=eligible,
            candidates_evaluated=len(candidates),
        )

    async def eligible_approvers_for_instruction(
        self,
        request: InstructionEligibleApproversEvaluateRequest,
    ) -> InstructionEligibleApproversResponse:
        instruction = request.instruction
        instruction_status = str(instruction.get("status") or "")
        instruction_type = str(instruction.get("instruction_type") or "")
        owning_lob = str(instruction.get("owning_lob") or "")
        created_by = instruction.get("created_by") or {}
        instruction_id = str(instruction.get("instruction_id") or "")
        opa_instruction, opa_account = build_instruction_opa_context(instruction)

        candidates = self._users.instruction_approver_candidates(owning_lob)
        eligible: list[EligibleApprover] = []

        for candidate in candidates:
            allowed, basis = await self._opa.can_approve_instruction(
                candidate,
                opa_instruction=opa_instruction,
                opa_account=opa_account,
            )
            if allowed:
                eligible.append(
                    EligibleApprover(
                        user_id=candidate.user_id,
                        display_name=candidate.display_name,
                        title=candidate.title,
                        allow_basis=basis,
                    )
                )

        eligible.sort(key=lambda row: row.display_name)

        return InstructionEligibleApproversResponse(
            instruction_id=instruction_id,
            instruction_status=instruction_status,
            instruction_type=instruction_type,
            owning_lob=owning_lob,
            created_by_user_id=str(created_by.get("user_id") or ""),
            created_by_title=str(created_by.get("title") or ""),
            evaluated_at=datetime.now(UTC).isoformat(),
            eligible=eligible,
            candidates_evaluated=len(candidates),
        )
