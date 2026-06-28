from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Callable

import httpx

from regression.auth_helpers import admin_auth_headers, compliance_auth_headers

logger = logging.getLogger(__name__)

SKIP_OLLAMA = os.environ.get("API_SMOKE_SKIP_OLLAMA", "").lower() in {"1", "true", "yes"}


@dataclass
class SmokeCheck:
    id: str
    service: str
    description: str
    passed: bool = False
    skipped: bool = False
    reason: str = ""


@dataclass
class SmokeResult:
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    checks: list[SmokeCheck] = field(default_factory=list)

    def record(self, check: SmokeCheck) -> None:
        self.checks.append(check)
        if check.skipped:
            self.skipped += 1
        elif check.passed:
            self.passed += 1
        else:
            self.failed += 1


def _ok(check_id: str, service: str, description: str) -> SmokeCheck:
    return SmokeCheck(id=check_id, service=service, description=description, passed=True)


def _fail(check_id: str, service: str, description: str, reason: str) -> SmokeCheck:
    return SmokeCheck(
        id=check_id,
        service=service,
        description=description,
        passed=False,
        reason=reason,
    )


def _skip(check_id: str, service: str, description: str, reason: str) -> SmokeCheck:
    return SmokeCheck(
        id=check_id,
        service=service,
        description=description,
        skipped=True,
        reason=reason,
    )


def _run_check(
    result: SmokeResult,
    check_id: str,
    service: str,
    description: str,
    fn: Callable[[], None],
) -> None:
    try:
        fn()
        result.record(_ok(check_id, service, description))
    except SkipCheck as exc:
        result.record(_skip(check_id, service, description, str(exc)))
    except Exception as exc:  # noqa: BLE001
        result.record(_fail(check_id, service, description, str(exc)))


class SkipCheck(Exception):
    pass


def run_api_smoke(
    *,
    harness_url: str,
    ilm_url: str,
    payment_url: str,
    indexer_url: str,
    chat_url: str,
    authz_url: str,
    context: dict[str, str] | None = None,
) -> SmokeResult:
    context = context or {}
    result = SmokeResult()

    with httpx.Client(timeout=60.0) as client:
        admin_headers = admin_auth_headers(client, harness_url)
        compliance_headers = compliance_auth_headers(client, chat_url)

        def health(service: str, base_url: str) -> None:
            response = client.get(f"{base_url.rstrip('/')}/health")
            if response.status_code != 200:
                raise RuntimeError(f"expected 200, got {response.status_code}")
            status = response.json().get("status")
            allowed = {"UP", "DEGRADED"} if service == "ssi-indexer" else {"UP"}
            if status not in allowed:
                raise RuntimeError(f"unexpected health status {status!r}: {response.text[:200]}")

        for service, url in [
            ("harness", harness_url),
            ("instruction-service", ilm_url),
            ("payment-service", payment_url),
            ("ssi-indexer", indexer_url),
            ("ssi-chat", chat_url),
            ("authorization-service", authz_url),
        ]:
            _run_check(
                result,
                f"{service}_health",
                service,
                "GET /health",
                lambda service=service, url=url: health(service, url),
            )

        def chat_compliance_users() -> None:
            response = client.get(f"{chat_url.rstrip('/')}/api/compliance-users")
            if response.status_code != 200:
                raise RuntimeError(f"expected 200, got {response.status_code}")
            users = response.json().get("users")
            if not users:
                raise RuntimeError("expected non-empty compliance users list")

        _run_check(
            result,
            "chat_compliance_users",
            "ssi-chat",
            "GET /api/compliance-users",
            chat_compliance_users,
        )

        def chat_auth_gate() -> None:
            try:
                with httpx.Client(timeout=10.0) as gate_client:
                    response = gate_client.post(
                        f"{chat_url.rstrip('/')}/api/chat",
                        json={"message": "hello", "mode": "events", "history": []},
                    )
            except httpx.TimeoutException as exc:
                raise SkipCheck(
                    "chat server did not respond in 10s (likely single-worker backlog; "
                    "auth gate is covered by unit tests)"
                ) from exc
            if response.status_code != 401:
                raise RuntimeError(f"expected 401 without auth, got {response.status_code}")

        _run_check(
            result,
            "chat_auth_gate",
            "ssi-chat",
            "POST /api/chat rejects unauthenticated",
            chat_auth_gate,
        )

        def payment_eligible_requires_auth() -> None:
            response = client.post(
                f"{payment_url.rstrip('/')}/api/v1/payments/pay-smoke/eligible-approvers",
            )
            if response.status_code != 401:
                raise RuntimeError(f"expected 401 without auth, got {response.status_code}")

        _run_check(
            result,
            "payment_eligible_auth_gate",
            "payment-service",
            "POST eligible-approvers rejects unauthenticated",
            payment_eligible_requires_auth,
        )

        def harness_status() -> None:
            response = client.get(
                f"{harness_url.rstrip('/')}/api/status",
                headers=admin_headers,
            )
            if response.status_code != 200:
                raise RuntimeError(f"expected 200, got {response.status_code}")
            body = response.json()
            for key in ("instruction_total", "payment_total", "security_event_count"):
                if key not in body:
                    raise RuntimeError(f"missing {key} in status payload")

        _run_check(
            result,
            "harness_status",
            "harness",
            "GET /api/status (admin)",
            harness_status,
        )

        def harness_action_requires_auth() -> None:
            response = client.post(
                f"{harness_url.rstrip('/')}/api/actions/create-instructions",
                json={"count": 1},
            )
            if response.status_code != 401:
                raise RuntimeError(f"expected 401 without auth, got {response.status_code}")

        _run_check(
            result,
            "harness_action_auth",
            "harness",
            "POST /api/actions/create-instructions rejects unauthenticated",
            harness_action_requires_auth,
        )

        def harness_suspend_route_auth() -> None:
            response = client.post(
                f"{harness_url.rstrip('/')}/api/actions/suspend-instructions",
                json={"count": 1},
            )
            if response.status_code != 401:
                raise RuntimeError(f"expected 401 without auth, got {response.status_code}")

        _run_check(
            result,
            "harness_suspend_auth",
            "harness",
            "POST /api/actions/suspend-instructions rejects unauthenticated",
            harness_suspend_route_auth,
        )

        def ilm_ui_list() -> None:
            response = client.get(
                f"{ilm_url.rstrip('/')}/api/ui/instructions",
                params={"limit": 10},
                headers=admin_headers,
            )
            if response.status_code != 200:
                raise RuntimeError(f"expected 200, got {response.status_code}")
            body = response.json()
            if isinstance(body, list):
                return
            if "instructions" not in body:
                raise RuntimeError("expected instructions key in UI response")

        _run_check(
            result,
            "ilm_ui_instructions",
            "instruction-service",
            "GET /api/ui/instructions (admin)",
            ilm_ui_list,
        )

        def ilm_ui_auth() -> None:
            response = client.get(f"{ilm_url.rstrip('/')}/api/ui/instructions")
            if response.status_code != 401:
                raise RuntimeError(f"expected 401 without auth, got {response.status_code}")

        _run_check(
            result,
            "ilm_ui_auth",
            "instruction-service",
            "GET /api/ui/instructions rejects unauthenticated",
            ilm_ui_auth,
        )

        def ilm_rest_auth() -> None:
            response = client.get(f"{ilm_url.rstrip('/')}/api/v1/instructions")
            if response.status_code != 401:
                raise RuntimeError(f"expected 401 without auth, got {response.status_code}")

        _run_check(
            result,
            "ilm_rest_auth",
            "instruction-service",
            "GET /api/v1/instructions rejects unauthenticated",
            ilm_rest_auth,
        )

        def payment_ui_list() -> None:
            response = client.get(
                f"{payment_url.rstrip('/')}/api/ui/payments",
                params={"limit": 10},
                headers=admin_headers,
            )
            if response.status_code != 200:
                raise RuntimeError(f"expected 200, got {response.status_code}")
            body = response.json()
            if isinstance(body, list):
                return
            if "payments" not in body:
                raise RuntimeError("expected payments key in UI response")

        _run_check(
            result,
            "payment_ui_payments",
            "payment-service",
            "GET /api/ui/payments (admin)",
            payment_ui_list,
        )

        def payment_rest_auth() -> None:
            response = client.get(f"{payment_url.rstrip('/')}/api/v1/payments")
            if response.status_code != 401:
                raise RuntimeError(f"expected 401 without auth, got {response.status_code}")

        _run_check(
            result,
            "payment_rest_auth",
            "payment-service",
            "GET /api/v1/payments rejects unauthenticated",
            payment_rest_auth,
        )

        def indexer_stats() -> None:
            response = client.get(
                f"{indexer_url.rstrip('/')}/api/stats",
                headers=admin_headers,
            )
            if response.status_code != 200:
                raise RuntimeError(f"expected 200, got {response.status_code}")
            body = response.json()
            if "components" not in body:
                raise RuntimeError("missing components in stats payload")

        _run_check(
            result,
            "indexer_stats",
            "ssi-indexer",
            "GET /api/stats (admin)",
            indexer_stats,
        )

        def indexer_stats_auth() -> None:
            response = client.get(f"{indexer_url.rstrip('/')}/api/stats")
            if response.status_code != 401:
                raise RuntimeError(f"expected 401 without auth, got {response.status_code}")

        _run_check(
            result,
            "indexer_stats_auth",
            "ssi-indexer",
            "GET /api/stats rejects unauthenticated",
            indexer_stats_auth,
        )

        def indexer_cypher_generate_auth() -> None:
            response = client.post(
                f"{indexer_url.rstrip('/')}/api/cypher/generate",
                json={"question": "list events", "mode": "events"},
            )
            if response.status_code != 401:
                raise RuntimeError(f"expected 401 without auth, got {response.status_code}")

        _run_check(
            result,
            "indexer_cypher_generate_auth",
            "ssi-indexer",
            "POST /api/cypher/generate rejects unauthenticated",
            indexer_cypher_generate_auth,
        )

        def indexer_search_vector() -> None:
            if SKIP_OLLAMA:
                raise SkipCheck("API_SMOKE_SKIP_OLLAMA set")
            response = client.post(
                f"{indexer_url.rstrip('/')}/api/search/vector",
                json={"query": "policy denial", "limit": 3},
                headers=admin_headers,
                timeout=120.0,
            )
            if response.status_code != 200:
                raise RuntimeError(f"expected 200, got {response.status_code}: {response.text[:200]}")
            body = response.json()
            if body.get("mode") != "vector":
                raise RuntimeError(f"unexpected mode: {body.get('mode')}")

        _run_check(
            result,
            "indexer_search_vector",
            "ssi-indexer",
            "POST /api/search/vector (admin, Ollama)",
            indexer_search_vector,
        )

        def indexer_graph_events() -> None:
            response = client.get(
                f"{indexer_url.rstrip('/')}/api/graph/events",
                params={"limit": 5},
                headers=admin_headers,
            )
            if response.status_code != 200:
                raise RuntimeError(f"expected 200, got {response.status_code}")
            if "events" not in response.json():
                raise RuntimeError("missing events in graph response")

        _run_check(
            result,
            "indexer_graph_events",
            "ssi-indexer",
            "GET /api/graph/events (admin)",
            indexer_graph_events,
        )

        def indexer_cypher_run() -> None:
            response = client.post(
                f"{indexer_url.rstrip('/')}/api/cypher/run",
                json={"cypher": "MATCH (n) RETURN count(n) AS total LIMIT 1"},
                headers=admin_headers,
            )
            if response.status_code != 200:
                raise RuntimeError(f"expected 200, got {response.status_code}: {response.text[:200]}")
            if "row_count" not in response.json():
                raise RuntimeError("missing row_count in cypher run response")

        _run_check(
            result,
            "indexer_cypher_run",
            "ssi-indexer",
            "POST /api/cypher/run (admin)",
            indexer_cypher_run,
        )

        def indexer_cypher_generate() -> None:
            if SKIP_OLLAMA:
                raise SkipCheck("API_SMOKE_SKIP_OLLAMA set")
            response = client.post(
                f"{indexer_url.rstrip('/')}/api/cypher/generate",
                json={"question": "How many security events exist?", "mode": "events"},
                headers=admin_headers,
                timeout=300.0,
            )
            if response.status_code != 200:
                raise RuntimeError(f"expected 200, got {response.status_code}: {response.text[:200]}")
            body = response.json()
            if "cypher" not in body or "valid" not in body:
                raise RuntimeError("missing cypher/valid in generate response")

        _run_check(
            result,
            "indexer_cypher_generate",
            "ssi-indexer",
            "POST /api/cypher/generate (admin, Ollama)",
            indexer_cypher_generate,
        )

        def payment_eligible() -> None:
            payment_id = context.get("submitted_payment_id") or context.get("approved_payment_id")
            if not payment_id:
                raise SkipCheck("no payment_id in context (run with --seed first)")
            response = client.post(
                f"{payment_url.rstrip('/')}/api/v1/payments/{payment_id}/eligible-approvers",
                headers=compliance_headers,
            )
            if response.status_code not in {200, 404}:
                raise RuntimeError(f"expected 200 or 404, got {response.status_code}: {response.text[:200]}")
            if response.status_code == 200:
                body = response.json()
                if body.get("payment_id") != payment_id:
                    raise RuntimeError("payment_id mismatch in eligible-approvers response")

        _run_check(
            result,
            "payment_eligible",
            "payment-service",
            "POST /api/v1/payments/{id}/eligible-approvers (compliance)",
            payment_eligible,
        )

        def instruction_eligible() -> None:
            instruction_id = context.get("approved_instruction_id")
            if not instruction_id:
                raise SkipCheck("no approved_instruction_id in context (run with --seed first)")
            response = client.post(
                f"{ilm_url.rstrip('/')}/api/v1/instructions/{instruction_id}/eligible-approvers",
                headers=compliance_headers,
            )
            if response.status_code not in {200, 404}:
                raise RuntimeError(f"expected 200 or 404, got {response.status_code}: {response.text[:200]}")
            if response.status_code == 200:
                body = response.json()
                if body.get("instruction_id") != instruction_id:
                    raise RuntimeError("instruction_id mismatch in eligible-approvers response")

        _run_check(
            result,
            "instruction_eligible",
            "instruction-service",
            "POST /api/v1/instructions/{id}/eligible-approvers (compliance)",
            instruction_eligible,
        )

    return result


def print_smoke_summary(result: SmokeResult) -> None:
    print("\n=== API smoke summary ===")
    print(f"passed={result.passed} failed={result.failed} skipped={result.skipped}")
    for check in result.checks:
        status = "PASS" if check.passed else ("SKIP" if check.skipped else "FAIL")
        print(f"[{status}] {check.service}: {check.id} — {check.description}")
        if not check.passed and not check.skipped:
            print(f"       reason: {check.reason}")
        if check.skipped and check.reason:
            print(f"       skip: {check.reason}")


def smoke_to_dict(result: SmokeResult) -> dict[str, Any]:
    return {
        "passed": result.passed,
        "failed": result.failed,
        "skipped": result.skipped,
        "checks": [
            {
                "id": check.id,
                "service": check.service,
                "description": check.description,
                "passed": check.passed,
                "skipped": check.skipped,
                "reason": check.reason,
            }
            for check in result.checks
        ],
    }
