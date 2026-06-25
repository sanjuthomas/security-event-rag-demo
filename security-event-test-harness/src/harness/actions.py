from __future__ import annotations

from harness.config import Settings
from harness.fixtures import SeedFile, build_instruction_payload, load_users
from harness.helpers import (
    Operation,
    _approver_for_instruction,
    _fetch_ui_instructions,
    _session_for_user,
    auth_client,
    build_scenario,
    build_seed_plan,
    ilm_client,
)
from harness.ilm_client import InstructionLifecycleClient
from harness.results import HarnessActionResult
from harness.zitadel_auth import ZitadelAuthClient


def _require_pat(settings: Settings) -> str | None:
    if settings.zitadel_service_pat:
        return None
    return "ZITADEL_SERVICE_PAT is required for session login"


def _clients(settings: Settings) -> tuple[SeedFile, ZitadelAuthClient, InstructionLifecycleClient]:
    seed = load_users(settings.users_file)
    auth = auth_client(settings)
    ilm = ilm_client(settings)
    return seed, auth, ilm


def create_instructions(settings: Settings, count: int) -> HarnessActionResult:
    result = HarnessActionResult(action="create_instructions", requested=count)
    if error := _require_pat(settings):
        result.logs.append(f"error: {error}")
        result.ok = False
        return result

    seed, auth, ilm = _clients(settings)
    result.logs.append(f"Creating {count} instruction(s)")

    for index, (user_id, owning_lob, instruction_type, currency) in enumerate(
        build_seed_plan(count), start=1
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


def submit_instructions(settings: Settings, count: int) -> HarnessActionResult:
    result = HarnessActionResult(action="submit_instructions", requested=count)
    if error := _require_pat(settings):
        result.logs.append(f"error: {error}")
        result.ok = False
        return result

    seed, auth, ilm = _clients(settings)
    submitter_id = "mo-100"
    drafts = _fetch_ui_instructions(settings, status="DRAFT")
    to_process = drafts[:count]

    if not to_process:
        result.logs.append("No DRAFT instructions available to submit.")
        return result

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


def approve_instructions(settings: Settings, count: int) -> HarnessActionResult:
    result = HarnessActionResult(action="approve_instructions", requested=count)
    if error := _require_pat(settings):
        result.logs.append(f"error: {error}")
        result.ok = False
        return result

    seed, auth, ilm = _clients(settings)
    submitter_id = "mo-100"

    drafts = _fetch_ui_instructions(settings, status="DRAFT")
    pending = _fetch_ui_instructions(settings, status="PENDING")
    candidates = drafts + pending
    if not candidates:
        result.logs.append("No DRAFT or PENDING instructions available to approve.")
        return result

    def _sort_key(instruction: dict) -> tuple[int, str]:
        creator_title = (instruction.get("created_by") or {}).get("title", "")
        approvable = _approver_for_instruction(instruction["owning_lob"], creator_title)
        return (0 if approvable else 1, instruction["instruction_id"])

    candidates.sort(key=_sort_key)
    to_process = candidates[:count]
    result.logs.append(f"Approving up to {len(to_process)} instruction(s)")

    for index, instruction in enumerate(to_process, start=1):
        instruction_id = instruction["instruction_id"]
        owning_lob = instruction["owning_lob"]
        status = instruction["status"]
        creator_title = (instruction.get("created_by") or {}).get("title", "")
        approver_id = _approver_for_instruction(owning_lob, creator_title)
        if not approver_id:
            result.skipped += 1
            result.failed += 1
            result.logs.append(
                f"[{index}] {instruction_id} skip: no seeded approver for "
                f"lob={owning_lob} creator_title={creator_title!r}"
            )
            continue

        result.logs.append(
            f"[{index}] {instruction_id} lob={owning_lob} status={status} "
            f"creator_title={creator_title} submit={submitter_id} approve={approver_id}"
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


def reject_instructions(settings: Settings, count: int) -> HarnessActionResult:
    result = HarnessActionResult(action="reject_instructions", requested=count)
    if error := _require_pat(settings):
        result.logs.append(f"error: {error}")
        result.ok = False
        return result

    seed, auth, ilm = _clients(settings)
    pending = _fetch_ui_instructions(settings, status="PENDING")
    to_process = pending[:count]

    if not to_process:
        result.logs.append("No PENDING instructions available to reject.")
        return result

    result.logs.append(f"Rejecting up to {len(to_process)} instruction(s)")

    for index, instruction in enumerate(to_process, start=1):
        instruction_id = instruction["instruction_id"]
        owning_lob = instruction["owning_lob"]
        creator_title = (instruction.get("created_by") or {}).get("title", "")
        approver_id = _approver_for_instruction(owning_lob, creator_title)
        if not approver_id:
            result.skipped += 1
            result.failed += 1
            result.logs.append(
                f"[{index}] {instruction_id} skip: no seeded approver for lob={owning_lob}"
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


def run_policy_scenario(settings: Settings) -> HarnessActionResult:
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
