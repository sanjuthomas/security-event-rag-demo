"""Payment service — business logic with Saga for SINGLE_USE instructions."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from uuid import uuid4

from authz_client import AuthzClient
from platform_auth import is_platform_admin
from sequence_client import SequenceClient
from sequence_client.errors import SequenceClientError

from ps.authorization import (
    build_authorization_block,
    details_with_authorization,
    payment_resource_context,
)
from ps.config import settings
from ps.ilm_client import IlmClient, InstructionNotFoundError, InstructionStateError
from ps.kafka_publisher import kafka_publisher
from ps.models.api import LifecycleEvent, RejectPaymentRequest, Subject, UserReference
from ps.models.enums import PaymentAction, PaymentStatus
from ps.models.payment import Payment
from ps.models.security_event import PaymentSecurityEvent
from ps.repository import PaymentNotFoundError, PaymentRepository
from ps.security_event_repository import SecurityEventRepository
from ps.service_identity import service_identity

logger = logging.getLogger(__name__)

_APPROVED_STATUSES = {"STANDING", "SINGLE_USE"}


def _covers_payment_lob(subject: Subject, owning_lob: str) -> bool:
    return owning_lob in subject.covering_lobs


def _can_view_payment(subject: Subject, payment: Payment) -> bool:
    if is_platform_admin(subject):
        return True
    if subject.user_id == payment.created_by.user_id:
        return True
    lob = payment.owning_lob
    roles = set(subject.roles)
    if "PAYMENT_CREATOR" in roles and (
        _covers_payment_lob(subject, lob) or subject.lob == lob
    ):
        return True
    if "FUNDING_APPROVER" in roles and _covers_payment_lob(subject, lob):
        return True
    return False


def _user_ref(subject: Subject) -> UserReference:
    return UserReference(
        user_id=subject.user_id,
        given_name=subject.given_name,
        family_name=subject.family_name,
        title=subject.title,
        lob=subject.lob,
        roles=subject.roles,
        supervisor_id=subject.supervisor_id,
    )


def _validate_instruction_at_create(instruction: dict) -> None:
    """Basic validation at payment creation time."""
    status = instruction.get("status", "")
    if status not in _APPROVED_STATUSES:
        raise ValueError(
            f"instruction is not in an approved state (status={status}). "
            "Only STANDING or SINGLE_USE instructions can be used for payments."
        )
    end_date_raw = instruction.get("end_date") or ""
    if end_date_raw:
        end_dt = datetime.fromisoformat(end_date_raw.replace("Z", "+00:00")).replace(tzinfo=None)
        if end_dt < datetime.now(timezone.utc).replace(tzinfo=None):
            raise ValueError(f"instruction has expired (end_date={end_date_raw})")


def _check_instruction_validity_for_approval(payment: Payment, instruction: dict) -> str | None:
    """Comprehensive instruction validity check at approval time.

    Returns a human-readable cancellation reason if the instruction is invalid,
    or ``None`` if everything looks good.

    Checks (in order):
      1. Version drift — instruction was modified after the payment was created.
      2. Status — instruction is still in an approvable state.
      3. Expiry — instruction end_date has not passed.
      4. Effective date — instruction is already in effect.
      5. Instruction type consistency — type matches the snapshot stored on creation.
    """
    now = datetime.now(timezone.utc)

    # 1. Version drift
    current_version = int(instruction.get("version_number") or 0)
    if current_version != payment.instruction_version:
        return (
            f"instruction was modified after payment creation — "
            f"payment was created against version {payment.instruction_version} "
            f"but the current version is {current_version}; "
            "please review the instruction changes and create a new payment if still valid"
        )

    # 2. Status
    status = instruction.get("status", "")
    if status not in _APPROVED_STATUSES:
        return (
            f"instruction is no longer in an approvable state "
            f"(current status={status!r}); it must be STANDING or SINGLE_USE to approve a payment"
        )

    # 3. Expiry
    end_date_raw = instruction.get("end_date") or ""
    if end_date_raw:
        try:
            end_dt = datetime.fromisoformat(end_date_raw.replace("Z", "+00:00"))
            if end_dt.tzinfo is None:
                end_dt = end_dt.replace(tzinfo=timezone.utc)
            if end_dt < now:
                return (
                    f"instruction has expired (end_date={end_date_raw}); "
                    "the payment cannot be approved against an expired instruction"
                )
        except ValueError:
            return f"instruction has an unparseable end_date value: {end_date_raw!r}"

    # 4. Effective date
    effective_date_raw = instruction.get("effective_date") or ""
    if effective_date_raw:
        try:
            eff_dt = datetime.fromisoformat(effective_date_raw.replace("Z", "+00:00"))
            if eff_dt.tzinfo is None:
                eff_dt = eff_dt.replace(tzinfo=timezone.utc)
            if eff_dt > now:
                return (
                    f"instruction is not yet effective (effective_date={effective_date_raw}); "
                    "payments cannot be approved before the instruction becomes active"
                )
        except ValueError:
            return f"instruction has an unparseable effective_date value: {effective_date_raw!r}"

    # 5. Instruction type consistency
    current_type = instruction.get("instruction_type") or instruction.get("status") or ""
    if current_type and payment.instruction_type and current_type != payment.instruction_type:
        return (
            f"instruction type changed since payment creation "
            f"(payment snapshot={payment.instruction_type!r}, current={current_type!r})"
        )

    return None


class PaymentService:
    def __init__(self, sequence_client: SequenceClient | None = None) -> None:
        self.repo = PaymentRepository()
        self.event_repo = SecurityEventRepository()
        self.authz = AuthzClient(settings.authorization_service_url)
        self.ilm = IlmClient()
        self.sequence = sequence_client or SequenceClient(settings.sequence_service_url)

    async def _evaluate_policy(
        self,
        action: PaymentAction,
        subject: Subject,
        payment: Payment,
        *,
        instruction_end_date: str = "",
        instruction_status: str = "",
        bearer_token: str | None = None,
        session_id: str | None = None,
    ):
        await service_identity.ensure_logged_in()
        common = {
            "action": action.value,
            "payment": payment.to_opa_payment(
                instruction_end_date=instruction_end_date,
                instruction_status=instruction_status,
            ),
            "instruction_end_date": instruction_end_date,
            "instruction_status": instruction_status,
            "service_token": service_identity.token,
            "service_session_id": service_identity.session_id,
        }
        if bearer_token and service_identity.token:
            return await self.authz.evaluate_payment(
                **common,
                user_token=bearer_token,
                user_session_id=session_id,
            )
        return await self.authz.evaluate_payment(
            **common,
            subject=subject.model_dump(mode="json"),
        )

    async def _authorize(
        self,
        action: PaymentAction,
        subject: Subject,
        payment: Payment,
        *,
        instruction_end_date: str = "",
        instruction_status: str = "",
        bearer_token: str | None = None,
        session_id: str | None = None,
    ) -> dict:
        decision = await self._evaluate_policy(
            action,
            subject,
            payment,
            instruction_end_date=instruction_end_date,
            instruction_status=instruction_status,
            bearer_token=bearer_token,
            session_id=session_id,
        )
        authorization = build_authorization_block(
            decision,
            subject,
            action,
            resource_context=payment_resource_context(
                payment,
                instruction_status=instruction_status,
                instruction_end_date=instruction_end_date,
            ),
        )
        if not decision.allowed:
            await self.event_repo.record_policy_denial(
                action,
                subject,
                payment,
                reason=authorization["summary"],
                details=details_with_authorization(None, authorization),
            )
            raise PermissionError(authorization["summary"])
        return authorization

    async def _record_success(
        self,
        action: PaymentAction,
        subject: Subject,
        payment: Payment,
        *,
        event_id: str,
        details: dict | None = None,
    ) -> None:
        event = PaymentSecurityEvent.authorized_action(
            action, subject, payment, details=details
        )
        event.event_id = event_id
        await self.event_repo.insert(event)
        await self._publish_payment_fact(payment)

    async def _publish_payment_fact(self, payment: Payment) -> None:
        try:
            await kafka_publisher.publish_payment(payment.to_mongo())
        except Exception:
            logger.exception(
                "failed to publish payment fact %s to Kafka", payment.payment_id
            )

    # ── Create ────────────────────────────────────────────────────────────────

    async def create(
        self,
        *,
        instruction_id: str,
        value_date: str,
        amount: float,
        subject: Subject,
        bearer_token: str | None = None,
        session_id: str | None = None,
    ) -> Payment:
        try:
            instruction = await self.ilm.get_instruction(
                instruction_id, bearer_token=bearer_token, session_id=session_id
            )
        except InstructionNotFoundError:
            raise

        _validate_instruction_at_create(instruction)

        instruction_status = instruction["status"]
        instruction_version = int(instruction.get("version_number") or 1)
        end_date = instruction.get("end_date") or ""

        event_id = str(uuid4())
        business_date = datetime.now(timezone.utc).date()
        try:
            payment_id = await self.sequence.next_payment_id(
                business_date=business_date,
                owning_lob=instruction["owning_lob"],
            )
        except SequenceClientError as exc:
            raise RuntimeError(f"sequence allocation failed: {exc}") from exc

        payment = Payment.create(
            payment_id=payment_id,
            instruction_id=instruction_id,
            instruction_version=instruction_version,
            amount=amount,
            currency=instruction["currency"],
            value_date=value_date,
            owning_lob=instruction["owning_lob"],
            instruction_type=instruction_status,
            subject=subject,
            event_id=event_id,
        )

        try:
            authorization = await self._authorize(
                PaymentAction.CREATE_PAYMENT,
                subject,
                payment,
                instruction_end_date=end_date,
                instruction_status=instruction_status,
                bearer_token=bearer_token,
                session_id=session_id,
            )
        except PermissionError:
            raise

        # Saga: for SINGLE_USE mark instruction USED first
        if instruction_status == "SINGLE_USE":
            try:
                await self.ilm.mark_used(
                    instruction_id,
                    payment.payment_id,
                    bearer_token=bearer_token,
                    session_id=session_id,
                )
            except InstructionStateError as exc:
                await self.event_repo.record_policy_denial(
                    PaymentAction.CREATE_PAYMENT,
                    subject,
                    payment,
                    reason=f"Saga step failed — instruction cannot be marked USED: {exc}",
                    details={"saga_step": "mark_used", "saga_error": str(exc)},
                )
                raise ValueError(str(exc)) from exc
            except Exception as exc:
                await self.event_repo.record_policy_denial(
                    PaymentAction.CREATE_PAYMENT,
                    subject,
                    payment,
                    reason=f"Saga step failed — ILM unreachable: {exc}",
                    details={"saga_step": "mark_used", "saga_error": str(exc)},
                )
                raise RuntimeError(
                    f"Could not mark instruction as USED before creating payment: {exc}"
                ) from exc

        await self.repo.insert(payment)

        await self._record_success(
            PaymentAction.CREATE_PAYMENT,
            subject,
            payment,
            event_id=event_id,
            details=details_with_authorization(None, authorization),
        )

        logger.info(
            "payment created (DRAFT) payment_id=%s instruction_id=%s amount=%s currency=%s",
            payment.payment_id, instruction_id, amount, payment.currency,
        )
        return payment

    # ── Submit ────────────────────────────────────────────────────────────────

    async def submit(
        self,
        payment_id: str,
        subject: Subject,
        bearer_token: str | None = None,
        session_id: str | None = None,
    ) -> Payment:
        payment = await self._get_or_404(payment_id)
        if payment.status != PaymentStatus.DRAFT:
            raise ValueError(f"payment cannot be submitted (current status={payment.status}); only DRAFT payments can be submitted")

        # Fetch instruction to give OPA full context
        try:
            instruction = await self.ilm.get_instruction(
                payment.instruction_id, bearer_token=bearer_token, session_id=session_id
            )
        except InstructionNotFoundError:
            raise ValueError(f"backing instruction {payment.instruction_id} not found")

        instruction_end_date = instruction.get("end_date") or ""
        instruction_status = instruction.get("status", "")

        try:
            authorization = await self._authorize(
                PaymentAction.SUBMIT_PAYMENT,
                subject,
                payment,
                instruction_end_date=instruction_end_date,
                instruction_status=instruction_status,
                bearer_token=bearer_token,
                session_id=session_id,
            )
        except PermissionError:
            raise

        now = datetime.now(timezone.utc)
        event_id = str(uuid4())
        payment.status = PaymentStatus.SUBMITTED
        payment.submitted_by = _user_ref(subject)
        payment.updated_at = now
        payment.lifecycle_events.append(
            LifecycleEvent(
                event_id=event_id,
                action="SUBMIT_PAYMENT",
                actor_user_id=subject.user_id,
                timestamp=now.isoformat(),
            )
        )

        await self.repo.update(payment)

        await self._record_success(
            PaymentAction.SUBMIT_PAYMENT,
            subject,
            payment,
            event_id=event_id,
            details=details_with_authorization(None, authorization),
        )

        logger.info("payment submitted payment_id=%s by=%s", payment_id, subject.user_id)
        return payment

    # ── Approve ───────────────────────────────────────────────────────────────

    async def approve(
        self,
        payment_id: str,
        subject: Subject,
        bearer_token: str | None = None,
        session_id: str | None = None,
    ) -> Payment:
        payment = await self._get_or_404(payment_id)
        if payment.status != PaymentStatus.SUBMITTED:
            raise ValueError(
                f"payment cannot be approved (current status={payment.status}); "
                "only SUBMITTED payments can be approved"
            )

        # Fetch the latest instruction version for validity check
        try:
            instruction = await self.ilm.get_instruction(
                payment.instruction_id, bearer_token=bearer_token, session_id=session_id
            )
        except InstructionNotFoundError:
            cancellation_reason = f"backing instruction {payment.instruction_id} could not be found at approval time"
            return await self._cancel(payment, subject, cancellation_reason)

        # Comprehensive validity check — version drift, status, expiry, effective date
        invalid_reason = _check_instruction_validity_for_approval(payment, instruction)
        if invalid_reason:
            return await self._cancel(payment, subject, invalid_reason)

        instruction_end_date = instruction.get("end_date") or ""
        instruction_status = instruction.get("status", "")

        try:
            authorization = await self._authorize(
                PaymentAction.APPROVE_PAYMENT,
                subject,
                payment,
                instruction_end_date=instruction_end_date,
                instruction_status=instruction_status,
                bearer_token=bearer_token,
                session_id=session_id,
            )
        except PermissionError:
            raise

        now = datetime.now(timezone.utc)
        event_id = str(uuid4())
        payment.status = PaymentStatus.APPROVED
        payment.approved_by = _user_ref(subject)
        payment.updated_at = now
        payment.lifecycle_events.append(
            LifecycleEvent(
                event_id=event_id,
                action="APPROVE_PAYMENT",
                actor_user_id=subject.user_id,
                timestamp=now.isoformat(),
            )
        )

        await self.repo.update(payment)

        await self._record_success(
            PaymentAction.APPROVE_PAYMENT,
            subject,
            payment,
            event_id=event_id,
            details=details_with_authorization(None, authorization),
        )

        logger.info("payment approved payment_id=%s by=%s", payment_id, subject.user_id)
        return payment

    # ── Reject ────────────────────────────────────────────────────────────────

    async def reject(
        self,
        payment_id: str,
        subject: Subject,
        request: RejectPaymentRequest,
        bearer_token: str | None = None,
        session_id: str | None = None,
    ) -> Payment:
        payment = await self._get_or_404(payment_id)
        if payment.status != PaymentStatus.SUBMITTED:
            raise ValueError(
                f"payment cannot be rejected (current status={payment.status}); "
                "only SUBMITTED payments can be rejected"
            )

        # Fetch instruction for OPA context (status and expiry)
        try:
            instruction = await self.ilm.get_instruction(
                payment.instruction_id, bearer_token=bearer_token, session_id=session_id
            )
        except InstructionNotFoundError:
            raise ValueError(f"backing instruction {payment.instruction_id} not found")

        instruction_end_date = instruction.get("end_date") or ""
        instruction_status = instruction.get("status", "")

        try:
            authorization = await self._authorize(
                PaymentAction.REJECT_PAYMENT,
                subject,
                payment,
                instruction_end_date=instruction_end_date,
                instruction_status=instruction_status,
                bearer_token=bearer_token,
                session_id=session_id,
            )
        except PermissionError:
            raise

        now = datetime.now(timezone.utc)
        event_id = str(uuid4())
        payment.status = PaymentStatus.REJECTED
        payment.rejected_by = _user_ref(subject)
        payment.rejection_reason = request.reason
        payment.updated_at = now
        payment.lifecycle_events.append(
            LifecycleEvent(
                event_id=event_id,
                action="REJECT_PAYMENT",
                actor_user_id=subject.user_id,
                timestamp=now.isoformat(),
                details={"reason": request.reason},
            )
        )

        await self.repo.update(payment)

        await self._record_success(
            PaymentAction.REJECT_PAYMENT,
            subject,
            payment,
            event_id=event_id,
            details=details_with_authorization({"reason": request.reason}, authorization),
        )

        logger.info("payment rejected payment_id=%s by=%s", payment_id, subject.user_id)
        return payment

    # ── Read ──────────────────────────────────────────────────────────────────

    async def get(self, payment_id: str, subject: Subject) -> Payment:
        payment = await self._get_or_404(payment_id)
        if not _can_view_payment(subject, payment):
            raise PermissionError("not authorized to view payment")
        return payment

    async def list(
        self,
        subject: Subject,
        *,
        instruction_id: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[Payment]:
        payments = await self.repo.list(
            instruction_id=instruction_id,
            status=status,
            limit=limit,
        )
        return [payment for payment in payments if _can_view_payment(subject, payment)]

    async def eligible_approvers(self, payment_id: str) -> dict:
        payment = await self._get_or_404(payment_id)
        instruction = await self.ilm.get_instruction_as_service(payment.instruction_id)
        await service_identity.ensure_logged_in()
        return await self.authz.eligible_payment_approvers(
            payment={
                "payment_id": payment.payment_id,
                "instruction_id": payment.instruction_id,
                "instruction_version": payment.instruction_version,
                "status": payment.status.value,
                "amount": payment.amount,
                "currency": payment.currency,
                "owning_lob": payment.owning_lob,
                "created_by_user_id": payment.created_by.user_id,
                "created_by_supervisor_id": payment.created_by.supervisor_id,
            },
            instruction_status=str(instruction.get("status") or ""),
            instruction_end_date=str(instruction.get("end_date") or ""),
            service_token=service_identity.token,
            service_session_id=service_identity.session_id,
        )

    # ── Internal helpers ──────────────────────────────────────────────────────

    async def _cancel(self, payment: Payment, subject: Subject, reason: str) -> Payment:
        """Move a payment to CANCELLED and record a security event with the approver's identity."""
        now = datetime.now(timezone.utc)
        event_id = str(uuid4())
        payment.status = PaymentStatus.CANCELLED
        payment.cancelled_by = _user_ref(subject)
        payment.cancellation_reason = reason
        payment.updated_at = now
        payment.lifecycle_events.append(
            LifecycleEvent(
                event_id=event_id,
                action="CANCEL_PAYMENT",
                actor_user_id=subject.user_id,
                timestamp=now.isoformat(),
                details={"reason": reason},
            )
        )

        await self.repo.update(payment)

        await self._record_success(
            PaymentAction.CANCEL_PAYMENT,
            subject,
            payment,
            event_id=event_id,
            details={"reason": reason},
        )

        logger.warning(
            "payment cancelled payment_id=%s by=%s reason=%s",
            payment.payment_id, subject.user_id, reason,
        )
        return payment

    async def _get_or_404(self, payment_id: str) -> Payment:
        try:
            return await self.repo.find_by_id(payment_id)
        except PaymentNotFoundError as exc:
            raise LookupError(f"payment {payment_id} not found") from exc
