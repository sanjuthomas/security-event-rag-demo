from datetime import datetime
from uuid import uuid4

from ilm.config import settings
from ilm.database import mongo_transaction
from ilm.models.api import (
    CreateInstructionRequest,
    DeleteInstructionRequest,
    InstructionResponse,
    RejectInstructionRequest,
    Subject,
    UpdateInstructionRequest,
    UseInstructionRequest,
)
from ilm.models.enums import (
    InstructionStatus,
    InstructionType,
    LifecycleAction,
)
from ilm.models.instruction import (
    CashSettlementInstruction,
    LifecycleEvent,
    UserReference,
)
from ilm.models.security_event import SecurityEvent
from ilm.opa import OpaClient, PolicyDeniedError
from ilm.repository import InstructionRepository
from ilm.security_event_repository import SecurityEventRepository
from ilm.storage import VersionedInstruction


class InvalidStateTransitionError(Exception):
    pass


def _parse_datetime(value: str) -> datetime:
    normalized = value.replace("Z", "+00:00")
    return datetime.fromisoformat(normalized).replace(tzinfo=None)


def _fmt_datetime(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat() + "Z"


def _to_response(record: VersionedInstruction) -> InstructionResponse:
    instruction = record.instruction

    return InstructionResponse(
        instruction_id=instruction.instruction_id,
        version_number=record.version_number,
        record_in=_fmt_datetime(record.valid_in) or "",
        record_out=_fmt_datetime(record.valid_out),
        instruction_type=instruction.instruction_type.value,
        status=instruction.status.value,
        owning_lob=instruction.owning_lob,
        wire_scope=instruction.wire_scope.value,
        currency=instruction.currency,
        funding_account=instruction.funding_account,
        initiating_party=instruction.initiating_party,
        ultimate_debtor=instruction.ultimate_debtor,
        debtor=instruction.debtor,
        debtor_account=instruction.debtor_account,
        debtor_agent=instruction.debtor_agent,
        debtor_agent_account=instruction.debtor_agent_account,
        instructing_agent=instruction.instructing_agent,
        instructed_agent=instruction.instructed_agent,
        previous_instructing_agents=instruction.previous_instructing_agents,
        intermediary_agents=instruction.intermediary_agents,
        creditor_agent=instruction.creditor_agent,
        creditor_agent_account=instruction.creditor_agent_account,
        creditor=instruction.creditor,
        creditor_account=instruction.creditor_account,
        ultimate_creditor=instruction.ultimate_creditor,
        charge_bearer=instruction.charge_bearer.value,
        instructions_for_creditor_agent=instruction.instructions_for_creditor_agent,
        instructions_for_next_agent=instruction.instructions_for_next_agent,
        effective_date=_fmt_datetime(instruction.effective_date) or "",
        end_date=_fmt_datetime(instruction.end_date) or "",
        created_by=instruction.created_by,
        created_at=_fmt_datetime(instruction.created_at) or "",
        updated_at=_fmt_datetime(instruction.updated_at) or "",
        submitted_at=_fmt_datetime(instruction.submitted_at),
        approved_by=instruction.approved_by,
        approved_at=_fmt_datetime(instruction.approved_at),
        rejected_by=instruction.rejected_by,
        rejected_at=_fmt_datetime(instruction.rejected_at),
        rejection_reason=instruction.rejection_reason,
        suspended_by=instruction.suspended_by,
        suspended_at=_fmt_datetime(instruction.suspended_at),
        last_used_at=_fmt_datetime(instruction.last_used_at),
        usage_count=instruction.usage_count,
    )


def _instruction_from_request(
    request: CreateInstructionRequest,
    subject: Subject,
    *,
    instruction_id: str | None = None,
    status: InstructionStatus = InstructionStatus.DRAFT,
    created_by: UserReference | None = None,
) -> CashSettlementInstruction:
    return CashSettlementInstruction(
        instruction_id=instruction_id or str(uuid4()),
        instruction_type=request.instruction_type,
        status=status,
        owning_lob=request.owning_lob,
        wire_scope=request.wire_scope,
        currency=request.currency,
        funding_account=request.funding_account,
        initiating_party=request.initiating_party,
        ultimate_debtor=request.ultimate_debtor,
        debtor=request.debtor,
        debtor_account=request.debtor_account,
        debtor_agent=request.debtor_agent,
        debtor_agent_account=request.debtor_agent_account,
        instructing_agent=request.instructing_agent,
        instructed_agent=request.instructed_agent,
        previous_instructing_agents=request.previous_instructing_agents,
        intermediary_agents=request.intermediary_agents,
        creditor_agent=request.creditor_agent,
        creditor_agent_account=request.creditor_agent_account,
        creditor=request.creditor,
        creditor_account=request.creditor_account,
        ultimate_creditor=request.ultimate_creditor,
        charge_bearer=request.charge_bearer,
        instructions_for_creditor_agent=request.instructions_for_creditor_agent,
        instructions_for_next_agent=request.instructions_for_next_agent,
        effective_date=_parse_datetime(request.effective_date),
        end_date=_parse_datetime(request.end_date),
        created_by=created_by
        or UserReference(
            user_id=subject.user_id,
            given_name=subject.given_name,
            family_name=subject.family_name,
            title=subject.title,
            lob=subject.lob,
            roles=subject.roles,
            supervisor_id=subject.supervisor_id,
        ),
    )


class InstructionService:
    def __init__(
        self,
        repository: InstructionRepository | None = None,
        opa_client: OpaClient | None = None,
        security_events: SecurityEventRepository | None = None,
    ) -> None:
        self.repository = repository or InstructionRepository()
        self.opa = opa_client or OpaClient()
        self.security_events = security_events or SecurityEventRepository()

    @staticmethod
    def _should_record_security_event(subject: Subject) -> bool:
        return subject.user_id not in settings.security_event_excluded_user_id_set

    async def _authorize(
        self,
        action: LifecycleAction,
        subject: Subject,
        instruction: CashSettlementInstruction,
        *,
        record_security_event: bool = False,
        security_event_details: dict | None = None,
    ) -> None:
        try:
            await self.opa.authorize(action, subject, instruction)
        except PolicyDeniedError as exc:
            if record_security_event and self._should_record_security_event(subject):
                await self.security_events.record_policy_denial(
                    action,
                    subject,
                    instruction,
                    reason=str(exc),
                    details=security_event_details,
                )
            raise PermissionError(str(exc)) from exc

    async def _record_authorized_action(
        self,
        action: LifecycleAction,
        subject: Subject,
        instruction: CashSettlementInstruction,
        *,
        version_number: int | None = None,
        details: dict | None = None,
    ) -> None:
        if not self._should_record_security_event(subject):
            return
        await self.security_events.record_authorized_action(
            action,
            subject,
            instruction,
            version_number=version_number,
            details=details,
        )

    def _record_event(
        self,
        instruction: CashSettlementInstruction,
        action: LifecycleAction,
        subject: Subject,
        details: dict | None = None,
    ) -> None:
        instruction.lifecycle_events.append(
            LifecycleEvent(
                action=action.value,
                actor_user_id=subject.user_id,
                details=details or {},
            )
        )

    async def _save_instruction_with_security_event(
        self,
        instruction: CashSettlementInstruction,
        action: LifecycleAction,
        subject: Subject,
        *,
        details: dict | None = None,
        initial: bool = False,
    ) -> VersionedInstruction:
        """Persist instruction version and matching security event atomically."""
        security_event_doc: dict | None = None

        async with mongo_transaction() as session:
            if initial:
                saved = await self.repository.insert_initial(instruction, session=session)
            else:
                saved = await self.repository.append_version(instruction, session=session)

            if self._should_record_security_event(subject):
                event = SecurityEvent.authorized_action(
                    action,
                    subject,
                    saved.instruction,
                    version_number=saved.version_number,
                    details=details,
                )
                security_event_doc = event.model_dump(mode="json")
                await self.security_events.insert_document(
                    security_event_doc,
                    session=session,
                )

        if security_event_doc is not None:
            await self.security_events.publish(security_event_doc)

        return saved

    async def _persist_new_version(
        self,
        instruction: CashSettlementInstruction,
        action: LifecycleAction,
        subject: Subject,
        details: dict | None = None,
        *,
        skip_authorize: bool = False,
    ) -> VersionedInstruction:
        if not skip_authorize:
            await self._authorize(
                action,
                subject,
                instruction,
                record_security_event=True,
                security_event_details=details,
            )
        self._record_event(instruction, action, subject, details)
        return await self._save_instruction_with_security_event(
            instruction,
            action,
            subject,
            details=details,
        )

    async def create(
        self, request: CreateInstructionRequest, subject: Subject
    ) -> InstructionResponse:
        instruction = _instruction_from_request(request, subject)
        await self._authorize(
            LifecycleAction.CREATE,
            subject,
            instruction,
            record_security_event=True,
        )
        self._record_event(instruction, LifecycleAction.CREATE, subject)
        saved = await self._save_instruction_with_security_event(
            instruction,
            LifecycleAction.CREATE,
            subject,
            initial=True,
        )
        return _to_response(saved)

    async def update(
        self,
        instruction_id: str,
        request: UpdateInstructionRequest,
        subject: Subject,
    ) -> InstructionResponse:
        current = await self.repository.get_current(instruction_id)
        instruction = current.instruction

        if instruction.status != InstructionStatus.DRAFT:
            raise InvalidStateTransitionError("only DRAFT instructions can be updated")

        updated = _instruction_from_request(
            request,
            subject,
            instruction_id=instruction.instruction_id,
            status=instruction.status,
            created_by=instruction.created_by,
        )
        updated.lifecycle_events = list(instruction.lifecycle_events)
        updated.created_at = instruction.created_at
        updated.submitted_at = instruction.submitted_at
        updated.approved_by = instruction.approved_by
        updated.approved_at = instruction.approved_at
        updated.rejected_by = instruction.rejected_by
        updated.rejected_at = instruction.rejected_at
        updated.rejection_reason = instruction.rejection_reason
        updated.suspended_by = instruction.suspended_by
        updated.suspended_at = instruction.suspended_at
        updated.last_used_at = instruction.last_used_at
        updated.usage_count = instruction.usage_count

        saved = await self._persist_new_version(
            updated,
            LifecycleAction.UPDATE,
            subject,
        )
        return _to_response(saved)

    async def delete(
        self,
        instruction_id: str,
        subject: Subject,
        request: DeleteInstructionRequest | None = None,
    ) -> InstructionResponse:
        current = await self.repository.get_current(instruction_id)
        instruction = current.instruction.model_copy(deep=True)

        if instruction.status == InstructionStatus.DELETED:
            raise InvalidStateTransitionError("instruction is already deleted")

        if instruction.status not in {
            InstructionStatus.DRAFT,
            InstructionStatus.PENDING,
        }:
            raise InvalidStateTransitionError(
                "only DRAFT or PENDING instructions can be soft deleted"
            )

        instruction.status = InstructionStatus.DELETED
        details = {"reason": request.reason} if request and request.reason else {}
        saved = await self._persist_new_version(
            instruction,
            LifecycleAction.DELETE,
            subject,
            details,
        )
        return _to_response(saved)

    async def get(self, instruction_id: str, subject: Subject) -> InstructionResponse:
        record = await self.repository.get_current(instruction_id)
        await self._authorize(
            LifecycleAction.VIEW,
            subject,
            record.instruction,
            record_security_event=True,
        )
        await self._record_authorized_action(
            LifecycleAction.VIEW,
            subject,
            record.instruction,
            version_number=record.version_number,
        )
        return _to_response(record)

    async def list_versions(
        self, instruction_id: str, subject: Subject
    ) -> list[InstructionResponse]:
        await self.get(instruction_id, subject)
        records = await self.repository.list_versions(instruction_id)
        return [_to_response(record) for record in records]

    async def list(
        self,
        subject: Subject,
        *,
        owning_lob: str | None = None,
        status: str | None = None,
        limit: int = 100,
        include_deleted: bool = False,
    ) -> list[InstructionResponse]:
        records = await self.repository.list_current(
            owning_lob=owning_lob, status=status, limit=limit
        )
        visible = []
        for record in records:
            if (
                not include_deleted
                and record.instruction.status == InstructionStatus.DELETED
            ):
                continue
            try:
                await self._authorize(
                    LifecycleAction.VIEW,
                    subject,
                    record.instruction,
                    record_security_event=True,
                )
            except PermissionError:
                continue
            await self._record_authorized_action(
                LifecycleAction.VIEW,
                subject,
                record.instruction,
                version_number=record.version_number,
            )
            visible.append(_to_response(record))
        return visible

    async def submit(self, instruction_id: str, subject: Subject) -> InstructionResponse:
        current = await self.repository.get_current(instruction_id)
        instruction = current.instruction.model_copy(deep=True)
        if instruction.status != InstructionStatus.DRAFT:
            raise InvalidStateTransitionError("only DRAFT instructions can be submitted")

        await self._authorize(
            LifecycleAction.SUBMIT,
            subject,
            instruction,
            record_security_event=True,
        )
        instruction.status = InstructionStatus.PENDING
        instruction.submitted_at = datetime.utcnow()
        saved = await self._persist_new_version(
            instruction,
            LifecycleAction.SUBMIT,
            subject,
            skip_authorize=True,
        )
        return _to_response(saved)

    async def approve(self, instruction_id: str, subject: Subject) -> InstructionResponse:
        current = await self.repository.get_current(instruction_id)
        instruction = current.instruction.model_copy(deep=True)
        if instruction.status != InstructionStatus.PENDING:
            raise InvalidStateTransitionError("only PENDING instructions can be approved")

        await self._authorize(
            LifecycleAction.APPROVE,
            subject,
            instruction,
            record_security_event=True,
        )
        instruction.status = (
            InstructionStatus.STANDING
            if instruction.instruction_type == InstructionType.STANDING
            else InstructionStatus.SINGLE_USE
        )
        instruction.approved_by = UserReference(
            user_id=subject.user_id,
            given_name=subject.given_name,
            family_name=subject.family_name,
            title=subject.title,
            lob=subject.lob,
            roles=subject.roles,
            supervisor_id=subject.supervisor_id,
        )
        instruction.approved_at = datetime.utcnow()
        saved = await self._persist_new_version(
            instruction,
            LifecycleAction.APPROVE,
            subject,
            skip_authorize=True,
        )
        return _to_response(saved)

    async def reject(
        self,
        instruction_id: str,
        subject: Subject,
        request: RejectInstructionRequest,
    ) -> InstructionResponse:
        current = await self.repository.get_current(instruction_id)
        instruction = current.instruction.model_copy(deep=True)
        if instruction.status != InstructionStatus.PENDING:
            raise InvalidStateTransitionError("only PENDING instructions can be rejected")

        await self._authorize(
            LifecycleAction.REJECT,
            subject,
            instruction,
            record_security_event=True,
            security_event_details={"reason": request.reason},
        )
        instruction.status = InstructionStatus.REJECTED
        instruction.rejected_by = UserReference(
            user_id=subject.user_id,
            given_name=subject.given_name,
            family_name=subject.family_name,
            title=subject.title,
            lob=subject.lob,
            roles=subject.roles,
            supervisor_id=subject.supervisor_id,
        )
        instruction.rejected_at = datetime.utcnow()
        instruction.rejection_reason = request.reason
        saved = await self._persist_new_version(
            instruction,
            LifecycleAction.REJECT,
            subject,
            {"reason": request.reason},
            skip_authorize=True,
        )
        return _to_response(saved)

    async def suspend(self, instruction_id: str, subject: Subject) -> InstructionResponse:
        current = await self.repository.get_current(instruction_id)
        instruction = current.instruction.model_copy(deep=True)
        if instruction.status not in {
            InstructionStatus.STANDING,
            InstructionStatus.SINGLE_USE,
        }:
            raise InvalidStateTransitionError(
                "only active STANDING or SINGLE_USE instructions can be suspended"
            )

        await self._authorize(
            LifecycleAction.SUSPEND,
            subject,
            instruction,
            record_security_event=True,
        )
        instruction.status = InstructionStatus.SUSPENDED
        instruction.suspended_by = subject.user_id
        instruction.suspended_at = datetime.utcnow()
        saved = await self._persist_new_version(
            instruction,
            LifecycleAction.SUSPEND,
            subject,
            skip_authorize=True,
        )
        return _to_response(saved)

    async def reactivate(self, instruction_id: str, subject: Subject) -> InstructionResponse:
        current = await self.repository.get_current(instruction_id)
        instruction = current.instruction.model_copy(deep=True)
        if instruction.status != InstructionStatus.SUSPENDED:
            raise InvalidStateTransitionError("only SUSPENDED instructions can be reactivated")

        await self._authorize(
            LifecycleAction.REACTIVATE,
            subject,
            instruction,
            record_security_event=True,
        )
        instruction.status = (
            InstructionStatus.STANDING
            if instruction.instruction_type == InstructionType.STANDING
            else InstructionStatus.SINGLE_USE
        )
        instruction.suspended_by = None
        instruction.suspended_at = None
        saved = await self._persist_new_version(
            instruction,
            LifecycleAction.REACTIVATE,
            subject,
            skip_authorize=True,
        )
        return _to_response(saved)

    async def use(
        self,
        instruction_id: str,
        subject: Subject,
        request: UseInstructionRequest,
    ) -> InstructionResponse:
        current = await self.repository.get_current(instruction_id)
        instruction = current.instruction.model_copy(deep=True)
        if instruction.status not in {
            InstructionStatus.STANDING,
            InstructionStatus.SINGLE_USE,
        }:
            raise InvalidStateTransitionError(
                "only active STANDING or SINGLE_USE instructions can be used"
            )

        instruction.usage_count += 1
        instruction.last_used_at = datetime.utcnow()

        use_details = {
            "payment_reference": request.payment_reference,
            "end_to_end_identification": request.end_to_end_identification,
            "currency": instruction.currency,
        }
        await self._authorize(
            LifecycleAction.USE,
            subject,
            instruction,
            record_security_event=True,
            security_event_details=use_details,
        )

        if instruction.instruction_type == InstructionType.SINGLE_USE:
            instruction.status = InstructionStatus.USED

        saved = await self._persist_new_version(
            instruction,
            LifecycleAction.USE,
            subject,
            use_details,
            skip_authorize=True,
        )
        return _to_response(saved)
