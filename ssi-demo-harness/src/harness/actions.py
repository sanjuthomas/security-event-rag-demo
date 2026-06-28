from __future__ import annotations

from datetime import date, timedelta

from harness.config import Settings
from harness.fixtures import SeedFile, build_instruction_payload, load_users, user_by_id
from harness.helpers import (
    Operation,
    PaymentOperation,
    _approver_for_instruction,
    _approver_for_payment,
    _count_payment_security_events,
    _fetch_api_instructions,
    _fetch_api_payments,
    _fetch_approved_instructions,
    _instruction_submitter,
    _rejector_for_payment,
    _session_for_user,
    auth_client,
    build_payment_scenario,
    build_payment_seed_plan,
    build_scenario,
    build_seed_plan,
    ilm_client,
    payment_client,
    payment_submitter_for_lob,
)
from harness.ilm_client import InstructionLifecycleClient
from harness.payment_client import PaymentServiceClient
from harness.results import HarnessActionResult
from harness.zitadel_auth import SessionCredentials, ZitadelAuthClient


def _require_pat(settings: Settings) -> str | None:
    if settings.zitadel_service_pat:
        return None
    return "ZITADEL_SERVICE_PAT is required for session login"


def _clients(settings: Settings) -> tuple[SeedFile, ZitadelAuthClient, InstructionLifecycleClient]:
    seed = load_users(settings.users_file)
    auth = auth_client(settings)
    ilm = ilm_client(settings)
    return seed, auth, ilm


def create_instructions(
    settings: Settings,
    count: int,
    _admin_session: SessionCredentials,
) -> HarnessActionResult:
    result = HarnessActionResult(action="create_instructions", requested=count)
    if error := _require_pat(settings):
        result.logs.append(f"error: {error}")
        result.ok = False
        return result

    seed, auth, ilm = _clients(settings)
    result.logs.append(f"Creating {count} instruction(s)")

    for index, (user_id, owning_lob, instruction_type, currency) in enumerate(
        build_seed_plan(count, seed=seed), start=1
    ):
        session = _session_for_user(auth, seed, settings, user_id)
        payload = build_instruction_payload(
            owning_lob=owning_lob,
            instruction_type=instruction_type,
            currency=currency,
        )
        result.logs.append(
            f"[{index}] create {instruction_type} {owning_lob} "
            f"currency={currency} user={user_id}"
        )
        response = ilm.create_instruction(session, payload)
        if response.status_code == 201:
            result.succeeded += 1
            result.logs.append(f"  -> HTTP 201 created {response.json()['instruction_id']}")
        else:
            result.failed += 1
            result.logs.append(f"  -> HTTP {response.status_code} FAIL")
            detail = response.text.strip()
            if detail:
                result.logs.append(f"     {detail[:300]}")

    result.ok = result.failed == 0
    result.logs.append(
        f"Created {result.succeeded} instruction(s) with {result.failed} failure(s)."
    )
    return result


def submit_instructions(
    settings: Settings,
    count: int,
    admin_session: SessionCredentials,
) -> HarnessActionResult:
    result = HarnessActionResult(action="submit_instructions", requested=count)
    if error := _require_pat(settings):
        result.logs.append(f"error: {error}")
        result.ok = False
        return result

    seed, auth, ilm = _clients(settings)
    drafts = _fetch_api_instructions(settings, admin_session, status="DRAFT")
    to_process = drafts[:count]

    if not to_process:
        result.logs.append("No DRAFT instructions available to submit.")
        return result

    submitter_id = _instruction_submitter(seed)
    result.logs.append(f"Submitting up to {len(to_process)} instruction(s) as {submitter_id}")
    submit_session = _session_for_user(auth, seed, settings, submitter_id)

    for index, instruction in enumerate(to_process, start=1):
        instruction_id = instruction["instruction_id"]
        result.logs.append(
            f"[{index}] {instruction_id} lob={instruction['owning_lob']} status=DRAFT"
        )
        response = ilm.submit_instruction(submit_session, instruction_id)
        if response.status_code in range(200, 300):
            result.succeeded += 1
            result.logs.append(f"  -> submit HTTP {response.status_code} OK")
        else:
            result.failed += 1
            result.logs.append(f"  -> submit HTTP {response.status_code} FAIL")
            detail = response.text.strip()
            if detail:
                result.logs.append(f"     {detail[:300]}")

    result.ok = result.failed == 0
    result.logs.append(
        f"Submitted {result.succeeded} instruction(s) with {result.failed} failure(s)."
    )
    return result


def approve_instructions(
    settings: Settings,
    count: int,
    admin_session: SessionCredentials,
) -> HarnessActionResult:
    result = HarnessActionResult(action="approve_instructions", requested=count)
    if error := _require_pat(settings):
        result.logs.append(f"error: {error}")
        result.ok = False
        return result

    seed, auth, ilm = _clients(settings)
    submitter_id = _instruction_submitter(seed)

    drafts = _fetch_api_instructions(settings, admin_session, status="DRAFT")
    pending = _fetch_api_instructions(settings, admin_session, status="PENDING")
    candidates = drafts + pending
    if not candidates:
        result.logs.append("No DRAFT or PENDING instructions available to approve.")
        return result

    def _created_by(instruction: dict) -> dict:
        return instruction.get("created_by") or {}

    def _sort_key(instruction: dict) -> tuple[int, str]:
        created_by = _created_by(instruction)
        approvable = _approver_for_instruction(
            seed,
            instruction["owning_lob"],
            str(created_by.get("user_id") or ""),
            str(created_by.get("title") or ""),
            created_by.get("supervisor_id"),
        )
        return (0 if approvable else 1, instruction["instruction_id"])

    candidates.sort(key=_sort_key)
    to_process = candidates[:count]
    result.logs.append(f"Approving up to {len(to_process)} instruction(s)")

    for index, instruction in enumerate(to_process, start=1):
        instruction_id = instruction["instruction_id"]
        owning_lob = instruction["owning_lob"]
        status = instruction["status"]
        created_by = _created_by(instruction)
        creator_title = created_by.get("title", "")
        creator_id = created_by.get("user_id", "")
        approver_id = _approver_for_instruction(
            seed,
            owning_lob,
            str(creator_id or ""),
            str(creator_title or ""),
            created_by.get("supervisor_id"),
        )
        if not approver_id:
            result.skipped += 1
            result.failed += 1
            result.logs.append(
                f"[{index}] {instruction_id} skip: no eligible approver for "
                f"lob={owning_lob} creator={creator_id} title={creator_title!r}"
            )
            continue

        result.logs.append(
            f"[{index}] {instruction_id} lob={owning_lob} status={status} "
            f"creator={creator_id} title={creator_title} "
            f"submit={submitter_id} approve={approver_id}"
        )

        if status == "DRAFT":
            submit_session = _session_for_user(auth, seed, settings, submitter_id)
            submit_response = ilm.submit_instruction(submit_session, instruction_id)
            if submit_response.status_code not in range(200, 300):
                result.failed += 1
                result.logs.append(f"  -> submit HTTP {submit_response.status_code} FAIL")
                detail = submit_response.text.strip()
                if detail:
                    result.logs.append(f"     {detail[:300]}")
                continue
            result.logs.append(f"  -> submit HTTP {submit_response.status_code} OK")

        approve_session = _session_for_user(auth, seed, settings, approver_id)
        approve_response = ilm.approve_instruction(approve_session, instruction_id)
        if approve_response.status_code in range(200, 300):
            result.succeeded += 1
            final_status = approve_response.json().get("status", "APPROVED")
            result.logs.append(f"  -> approve HTTP {approve_response.status_code} OK ({final_status})")
        else:
            result.failed += 1
            result.logs.append(f"  -> approve HTTP {approve_response.status_code} FAIL")
            detail = approve_response.text.strip()
            if detail:
                result.logs.append(f"     {detail[:300]}")

    result.ok = result.failed == 0
    result.logs.append(
        f"Approved {result.succeeded} instruction(s) with {result.failed} failure(s)."
    )
    return result


def reject_instructions(
    settings: Settings,
    count: int,
    admin_session: SessionCredentials,
) -> HarnessActionResult:
    result = HarnessActionResult(action="reject_instructions", requested=count)
    if error := _require_pat(settings):
        result.logs.append(f"error: {error}")
        result.ok = False
        return result

    seed, auth, ilm = _clients(settings)
    pending = _fetch_api_instructions(settings, admin_session, status="PENDING")
    to_process = pending[:count]

    if not to_process:
        result.logs.append("No PENDING instructions available to reject.")
        return result

    result.logs.append(f"Rejecting up to {len(to_process)} instruction(s)")

    for index, instruction in enumerate(to_process, start=1):
        instruction_id = instruction["instruction_id"]
        owning_lob = instruction["owning_lob"]
        created_by = instruction.get("created_by") or {}
        creator_title = created_by.get("title", "")
        creator_id = created_by.get("user_id", "")
        approver_id = _approver_for_instruction(
            seed,
            owning_lob,
            str(creator_id or ""),
            str(creator_title or ""),
            created_by.get("supervisor_id"),
        )
        if not approver_id:
            result.skipped += 1
            result.failed += 1
            result.logs.append(
                f"[{index}] {instruction_id} skip: no eligible approver for "
                f"lob={owning_lob} creator={creator_id}"
            )
            continue

        result.logs.append(
            f"[{index}] {instruction_id} lob={owning_lob} reject as {approver_id}"
        )
        session = _session_for_user(auth, seed, settings, approver_id)
        response = ilm.reject_instruction(
            session,
            instruction_id,
            reason="Rejected via test harness UI",
        )
        if response.status_code in range(200, 300):
            result.succeeded += 1
            result.logs.append(f"  -> reject HTTP {response.status_code} OK")
        else:
            result.failed += 1
            result.logs.append(f"  -> reject HTTP {response.status_code} FAIL")
            detail = response.text.strip()
            if detail:
                result.logs.append(f"     {detail[:300]}")

    result.ok = result.failed == 0
    result.logs.append(
        f"Rejected {result.succeeded} instruction(s) with {result.failed} failure(s)."
    )
    return result


def run_policy_scenario(
    settings: Settings,
    _admin_session: SessionCredentials,
) -> HarnessActionResult:
    result = HarnessActionResult(action="run_policy_scenario", requested=1)
    if error := _require_pat(settings):
        result.logs.append(f"error: {error}")
        result.ok = False
        return result

    seed, auth, ilm = _clients(settings)
    instruction_id: str | None = None
    failures = 0

    result.logs.append("Running instruction lifecycle policy scenario")

    for index, (operation, user_id, expect_success, description) in enumerate(build_scenario()):
        session = _session_for_user(auth, seed, settings, user_id)
        result.logs.append(
            f"[{index + 1}] {description} "
            f"(user={user_id}, op={operation.value}, expect={'OK' if expect_success else 'DENY'})"
        )

        if operation == Operation.CREATE:
            response = ilm.create_instruction(session, build_instruction_payload(owning_lob="FICC"))
            if expect_success and response.status_code == 201:
                instruction_id = response.json()["instruction_id"]
        elif operation == Operation.GET:
            if not instruction_id:
                result.logs.append("  skip: no instruction_id")
                continue
            response = ilm.get_instruction(session, instruction_id)
        elif operation == Operation.LIST:
            response = ilm.list_instructions(session)
        elif operation == Operation.SUBMIT:
            if not instruction_id:
                result.logs.append("  skip: no instruction_id")
                continue
            response = ilm.submit_instruction(session, instruction_id)
        elif operation == Operation.APPROVE:
            if not instruction_id:
                result.logs.append("  skip: no instruction_id")
                continue
            response = ilm.approve_instruction(session, instruction_id)
        elif operation == Operation.LIST_VERSIONS:
            if not instruction_id:
                result.logs.append("  skip: no instruction_id")
                continue
            response = ilm.list_versions(session, instruction_id)
        else:
            raise RuntimeError(f"unsupported operation: {operation}")

        ok = (200 <= response.status_code < 300) == expect_success
        result.logs.append(f"  -> HTTP {response.status_code} {'PASS' if ok else 'FAIL'}")
        if not ok:
            failures += 1
            detail = response.text.strip()
            if detail:
                result.logs.append(f"     {detail[:300]}")

    result.succeeded = 1 if failures == 0 else 0
    result.failed = failures
    result.ok = failures == 0
    result.logs.append(f"Scenario finished with {failures} failure(s).")
    return result


# ---------------------------------------------------------------------------
# Payment actions
# ---------------------------------------------------------------------------

def _payment_clients(
    settings: Settings,
) -> tuple[SeedFile, ZitadelAuthClient, PaymentServiceClient]:
    seed = load_users(settings.users_file)
    auth = auth_client(settings)
    ps = payment_client(settings)
    return seed, auth, ps


def create_payments(
    settings: Settings,
    count: int,
    admin_session: SessionCredentials,
) -> HarnessActionResult:
    result = HarnessActionResult(action="create_payments", requested=count)
    if error := _require_pat(settings):
        result.logs.append(f"error: {error}")
        result.ok = False
        return result

    seed, auth, ps = _payment_clients(settings)

    approved = _fetch_approved_instructions(settings, admin_session)
    # Prefer STANDING so we can reuse them; fall back to SINGLE_USE
    standing = [i for i in approved if i.get("status") == "STANDING"]
    pool = standing if standing else approved

    if not pool:
        result.logs.append(
            "No approved STANDING or SINGLE_USE instructions found. "
            "Run approve-instructions first."
        )
        result.ok = False
        return result

    value_date = (date.today() + timedelta(days=1)).isoformat()
    result.logs.append(
        f"Creating {count} payment(s) against {len(pool)} approved instruction(s)"
    )

    for index, (user_id, amount) in enumerate(
        build_payment_seed_plan(count, seed=seed), start=1
    ):
        try:
            creator = user_by_id(seed, user_id)
        except KeyError:
            result.failed += 1
            result.logs.append(f"[{index}] skip: unknown payment creator {user_id}")
            continue

        matching = [
            instruction
            for instruction in pool
            if instruction.get("owning_lob") in creator.covering_lobs
        ]
        if not matching:
            result.failed += 1
            result.logs.append(
                f"[{index}] skip: no approved instruction for creator {user_id} "
                f"covering {creator.covering_lobs}"
            )
            continue

        instruction = matching[index % len(matching)]
        instruction_id = instruction["instruction_id"]
        owning_lob = instruction.get("owning_lob", "?")

        result.logs.append(
            f"[{index}] create payment  user={user_id}  amount={amount:,.0f}"
            f"  lob={owning_lob}  instruction={instruction_id[:8]}…"
        )
        session = _session_for_user(auth, seed, settings, user_id)
        response = ps.create_payment(session, instruction_id, amount, value_date)

        if response.status_code == 201:
            result.succeeded += 1
            result.logs.append(
                f"  -> HTTP 201 created {response.json()['payment_id']}"
            )
        else:
            result.failed += 1
            result.logs.append(f"  -> HTTP {response.status_code} FAIL")
            detail = response.text.strip()
            if detail:
                result.logs.append(f"     {detail[:300]}")

    result.ok = result.failed == 0
    result.logs.append(
        f"Created {result.succeeded} payment(s) with {result.failed} failure(s)."
    )
    return result


def submit_payments(
    settings: Settings,
    count: int,
    admin_session: SessionCredentials,
) -> HarnessActionResult:
    result = HarnessActionResult(action="submit_payments", requested=count)
    if error := _require_pat(settings):
        result.logs.append(f"error: {error}")
        result.ok = False
        return result

    seed, auth, ps = _payment_clients(settings)

    drafts = _fetch_api_payments(settings, admin_session, status="DRAFT")
    to_process = drafts[:count]

    if not to_process:
        result.logs.append("No DRAFT payments available to submit.")
        return result

    result.logs.append(f"Submitting up to {len(to_process)} payment(s)")

    for index, payment in enumerate(to_process, start=1):
        payment_id = payment["payment_id"]
        owning_lob = payment.get("owning_lob", "?")
        try:
            submitter_id = payment_submitter_for_lob(seed, owning_lob)
        except ValueError as exc:
            result.failed += 1
            result.logs.append(f"[{index}] {payment_id}  lob={owning_lob}  skip: {exc}")
            continue
        result.logs.append(
            f"[{index}] {payment_id}  lob={owning_lob}  submitting as {submitter_id}"
        )
        session = _session_for_user(auth, seed, settings, submitter_id)
        response = ps.submit_payment(session, payment_id)
        if response.status_code in range(200, 300):
            result.succeeded += 1
            result.logs.append(f"  -> submit HTTP {response.status_code} OK")
        else:
            result.failed += 1
            result.logs.append(f"  -> submit HTTP {response.status_code} FAIL")
            detail = response.text.strip()
            if detail:
                result.logs.append(f"     {detail[:300]}")

    result.ok = result.failed == 0
    result.logs.append(
        f"Submitted {result.succeeded} payment(s) with {result.failed} failure(s)."
    )
    return result


def approve_payments(
    settings: Settings,
    count: int,
    admin_session: SessionCredentials,
) -> HarnessActionResult:
    result = HarnessActionResult(action="approve_payments", requested=count)
    if error := _require_pat(settings):
        result.logs.append(f"error: {error}")
        result.ok = False
        return result

    seed, auth, ps = _payment_clients(settings)

    submitted = _fetch_api_payments(settings, admin_session, status="SUBMITTED")
    if not submitted:
        result.logs.append("No SUBMITTED payments available to approve.")
        return result

    def _sort_key(payment: dict) -> tuple[int, str]:
        approvable = _approver_for_payment(seed, payment)
        return (0 if approvable else 1, payment["payment_id"])

    to_process = sorted(submitted, key=_sort_key)[:count]
    result.logs.append(f"Approving up to {len(to_process)} payment(s)")

    for index, payment in enumerate(to_process, start=1):
        payment_id = payment["payment_id"]
        amount = payment.get("amount", 0)
        owning_lob = payment.get("owning_lob", "?")
        created_by = payment.get("created_by") or {}
        creator_id = created_by.get("user_id", "?")
        approver_id = _approver_for_payment(seed, payment)
        if not approver_id:
            result.skipped += 1
            result.failed += 1
            result.logs.append(
                f"[{index}] {payment_id} skip: no eligible approver for "
                f"lob={owning_lob} creator={creator_id} amount={amount:,.0f}"
            )
            continue

        result.logs.append(
            f"[{index}] {payment_id} lob={owning_lob} creator={creator_id} "
            f"amount={amount:,.0f} approve={approver_id}"
        )
        approve_session = _session_for_user(auth, seed, settings, approver_id)
        response = ps.approve_payment(approve_session, payment_id)
        if response.status_code in range(200, 300):
            result.succeeded += 1
            result.logs.append(f"  -> approve HTTP {response.status_code} OK")
        else:
            result.failed += 1
            result.logs.append(f"  -> approve HTTP {response.status_code} FAIL")
            detail = response.text.strip()
            if detail:
                result.logs.append(f"     {detail[:300]}")

    result.ok = result.failed == 0
    result.logs.append(
        f"Approved {result.succeeded} payment(s) with {result.failed} failure(s)."
    )
    return result


def reject_payments(
    settings: Settings,
    count: int,
    admin_session: SessionCredentials,
) -> HarnessActionResult:
    result = HarnessActionResult(action="reject_payments", requested=count)
    if error := _require_pat(settings):
        result.logs.append(f"error: {error}")
        result.ok = False
        return result

    seed, auth, ps = _payment_clients(settings)

    submitted = _fetch_api_payments(settings, admin_session, status="SUBMITTED")
    to_process = submitted[:count]

    if not to_process:
        result.logs.append("No SUBMITTED payments available to reject.")
        return result

    result.logs.append(f"Rejecting up to {len(to_process)} payment(s)")

    for index, payment in enumerate(to_process, start=1):
        payment_id = payment["payment_id"]
        owning_lob = payment.get("owning_lob", "?")
        rejector_id = _rejector_for_payment(seed, payment)
        if not rejector_id:
            result.skipped += 1
            result.failed += 1
            result.logs.append(
                f"[{index}] {payment_id} skip: no eligible rejector for lob={owning_lob}"
            )
            continue

        result.logs.append(f"[{index}] {payment_id} lob={owning_lob} reject={rejector_id}")
        reject_session = _session_for_user(auth, seed, settings, rejector_id)
        response = ps.reject_payment(
            reject_session, payment_id, reason="Rejected via test harness"
        )
        if response.status_code in range(200, 300):
            result.succeeded += 1
            result.logs.append(f"  -> reject HTTP {response.status_code} OK")
        else:
            result.failed += 1
            result.logs.append(f"  -> reject HTTP {response.status_code} FAIL")
            detail = response.text.strip()
            if detail:
                result.logs.append(f"     {detail[:300]}")

    result.ok = result.failed == 0
    result.logs.append(
        f"Rejected {result.succeeded} payment(s) with {result.failed} failure(s)."
    )
    return result


def run_payment_policy_scenario(
    settings: Settings,
    admin_session: SessionCredentials,
) -> HarnessActionResult:
    """OPA payment policy scenario with expected INFO and ALERT security events.

    Creates a payment, exercises CREATE/SUBMIT/APPROVE denials (ALERT), then
    completes the happy path (INFO).
    """
    result = HarnessActionResult(action="run_payment_policy_scenario", requested=1)
    if error := _require_pat(settings):
        result.logs.append(f"error: {error}")
        result.ok = False
        return result

    seed, auth, ps = _payment_clients(settings)
    failures = 0
    scenario = build_payment_scenario()
    expected_denials = sum(1 for _, _, expect_success, _ in scenario if not expect_success)
    expected_successes = sum(1 for _, _, expect_success, _ in scenario if expect_success)

    alerts_before = (
        _count_payment_security_events(settings, severity="ALERT", outcome="failure")
        if settings.verify_security_events
        else -1
    )
    infos_before = (
        _count_payment_security_events(settings, severity="INFO", outcome="success")
        if settings.verify_security_events
        else -1
    )

    result.logs.append("Running payment lifecycle policy scenario")

    # Discover a FICC approved instruction to use throughout the scenario
    approved = _fetch_approved_instructions(settings, admin_session)
    ficc_instructions = [
        i for i in approved
        if i.get("owning_lob") == "FICC"
        and i.get("status") == "STANDING"
    ]
    if not ficc_instructions:
        ficc_instructions = [i for i in approved if i.get("owning_lob") == "FICC"]
    if not ficc_instructions:
        result.logs.append(
            "No approved FICC instruction found. "
            "Run approve-instructions first to seed at least one FICC instruction."
        )
        result.ok = False
        return result

    instruction_id = ficc_instructions[0]["instruction_id"]
    value_date = (date.today() + timedelta(days=1)).isoformat()
    result.logs.append(f"Using FICC instruction {instruction_id}")

    payment_id: str | None = None

    for index, (operation, user_id, expect_success, description) in enumerate(
        scenario, start=1
    ):
        session = _session_for_user(auth, seed, settings, user_id)
        result.logs.append(
            f"[{index}] {description} "
            f"(user={user_id}, op={operation.value}, expect={'OK' if expect_success else 'DENY'})"
        )

        if operation == PaymentOperation.CREATE_PAYMENT:
            response = ps.create_payment(session, instruction_id, 1_000_000.0, value_date)
            if expect_success and response.status_code == 201:
                payment_id = response.json()["payment_id"]
        elif operation == PaymentOperation.SUBMIT_PAYMENT:
            if not payment_id:
                result.logs.append("  skip: no payment_id (earlier CREATE failed)")
                continue
            response = ps.submit_payment(session, payment_id)
        elif operation == PaymentOperation.APPROVE_PAYMENT:
            if not payment_id:
                result.logs.append("  skip: no payment_id (earlier step failed)")
                continue
            response = ps.approve_payment(session, payment_id)
        elif operation == PaymentOperation.REJECT_PAYMENT:
            if not payment_id:
                result.logs.append("  skip: no payment_id")
                continue
            response = ps.reject_payment(session, payment_id, reason="test harness rejection")
        else:
            raise RuntimeError(f"unsupported operation: {operation}")

        ok = (200 <= response.status_code < 300) == expect_success
        result.logs.append(f"  -> HTTP {response.status_code} {'PASS' if ok else 'FAIL'}")
        if not ok:
            failures += 1
            detail = response.text.strip()
            if detail:
                result.logs.append(f"     {detail[:300]}")

    if settings.verify_security_events and alerts_before >= 0:
        alerts_after = _count_payment_security_events(
            settings, severity="ALERT", outcome="failure"
        )
        infos_after = _count_payment_security_events(
            settings, severity="INFO", outcome="success"
        )
        new_alerts = alerts_after - alerts_before
        new_infos = infos_after - infos_before
        result.logs.append(
            f"Security events: +{new_alerts} ALERT (expected {expected_denials}), "
            f"+{new_infos} INFO (expected {expected_successes})"
        )
        if new_alerts < expected_denials:
            failures += 1
            result.logs.append(
                "  FAIL: expected an ALERT security event for each policy denial"
            )
        if new_infos < expected_successes:
            failures += 1
            result.logs.append(
                "  FAIL: expected an INFO security event for each authorized action"
            )

    result.succeeded = 1 if failures == 0 else 0
    result.failed = failures
    result.ok = failures == 0
    result.logs.append(f"Scenario finished with {failures} failure(s).")
    return result


def suspend_instructions(
    settings: Settings,
    count: int,
    admin_session: SessionCredentials,
) -> HarnessActionResult:
    result = HarnessActionResult(action="suspend_instructions", requested=count)
    if error := _require_pat(settings):
        result.logs.append(f"error: {error}")
        result.ok = False
        return result

    standing = _fetch_api_instructions(settings, admin_session, status="STANDING")
    single_use = _fetch_api_instructions(settings, admin_session, status="SINGLE_USE")
    candidates = standing + single_use
    to_process = candidates[:count]

    if not to_process:
        result.logs.append("No STANDING or SINGLE_USE instructions available to suspend.")
        return result

    ilm = ilm_client(settings)
    result.logs.append(f"Suspending up to {len(to_process)} instruction(s)")

    for index, instruction in enumerate(to_process, start=1):
        instruction_id = instruction["instruction_id"]
        status = instruction.get("status", "?")
        owning_lob = instruction.get("owning_lob", "?")
        result.logs.append(
            f"[{index}] {instruction_id} lob={owning_lob} status={status}"
        )
        response = ilm.suspend_instruction(admin_session, instruction_id)
        if response.status_code in range(200, 300):
            result.succeeded += 1
            result.logs.append(f"  -> suspend HTTP {response.status_code} OK")
        else:
            result.failed += 1
            result.logs.append(f"  -> suspend HTTP {response.status_code} FAIL")
            detail = response.text.strip()
            if detail:
                result.logs.append(f"     {detail[:300]}")

    result.ok = result.failed == 0
    result.logs.append(
        f"Suspended {result.succeeded} instruction(s) with {result.failed} failure(s)."
    )
    return result


def reactivate_instructions(
    settings: Settings,
    count: int,
    admin_session: SessionCredentials,
) -> HarnessActionResult:
    result = HarnessActionResult(action="reactivate_instructions", requested=count)
    if error := _require_pat(settings):
        result.logs.append(f"error: {error}")
        result.ok = False
        return result

    suspended = _fetch_api_instructions(settings, admin_session, status="SUSPENDED")
    to_process = suspended[:count]

    if not to_process:
        result.logs.append("No SUSPENDED instructions available to reactivate.")
        return result

    ilm = ilm_client(settings)
    result.logs.append(f"Reactivating up to {len(to_process)} instruction(s)")

    for index, instruction in enumerate(to_process, start=1):
        instruction_id = instruction["instruction_id"]
        owning_lob = instruction.get("owning_lob", "?")
        result.logs.append(f"[{index}] {instruction_id} lob={owning_lob} status=SUSPENDED")
        response = ilm.reactivate_instruction(admin_session, instruction_id)
        if response.status_code in range(200, 300):
            result.succeeded += 1
            final_status = response.json().get("status", "REACTIVATED")
            result.logs.append(
                f"  -> reactivate HTTP {response.status_code} OK ({final_status})"
            )
        else:
            result.failed += 1
            result.logs.append(f"  -> reactivate HTTP {response.status_code} FAIL")
            detail = response.text.strip()
            if detail:
                result.logs.append(f"     {detail[:300]}")

    result.ok = result.failed == 0
    result.logs.append(
        f"Reactivated {result.succeeded} instruction(s) with {result.failed} failure(s)."
    )
    return result
