#!/usr/bin/env python3
"""Upload all Rego policies under policies/ to a running OPA server."""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

POLICIES_DIR = Path(os.environ.get("POLICIES_DIR", "/policies"))
OPA_URL = os.environ.get("OPA_URL", "http://opa:8181").rstrip("/")
OPA_WAIT_TIMEOUT = int(os.environ.get("OPA_WAIT_TIMEOUT", "60"))
UPLOAD_RETRIES = int(os.environ.get("UPLOAD_RETRIES", "5"))
RETRY_DELAY_SECONDS = float(os.environ.get("RETRY_DELAY_SECONDS", "2"))


def wait_for_opa() -> None:
    deadline = time.monotonic() + OPA_WAIT_TIMEOUT
    health_url = f"{OPA_URL}/health"

    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(health_url, timeout=2) as response:
                if response.status == 200:
                    print(f"OPA is ready at {OPA_URL}")
                    return
        except (urllib.error.URLError, TimeoutError):
            pass
        time.sleep(1)

    raise SystemExit(f"OPA not reachable at {OPA_URL} within {OPA_WAIT_TIMEOUT}s")


def collect_policies(root: Path) -> list[Path]:
    if not root.is_dir():
        raise SystemExit(f"policies directory not found: {root}")
    return sorted(root.rglob("*.rego"))


def policy_id(policy_path: Path, root: Path) -> str:
    return policy_path.relative_to(root).as_posix()


def upload_policy(policy_path: Path, root: Path) -> None:
    policy_key = policy_id(policy_path, root)
    url = f"{OPA_URL}/v1/policies/{policy_key}"
    request = urllib.request.Request(
        url,
        data=policy_path.read_bytes(),
        method="PUT",
        headers={"Content-Type": "text/plain"},
    )

    with urllib.request.urlopen(request, timeout=30) as response:
        if response.status not in (200, 204):
            raise RuntimeError(f"unexpected status {response.status} for {policy_key}")


def list_policy_ids() -> list[str]:
    list_url = f"{OPA_URL}/v1/policies"
    with urllib.request.urlopen(list_url, timeout=10) as response:
        payload = json.loads(response.read().decode())

    result = payload.get("result", [])
    if not isinstance(result, list):
        return []
    return [item["id"] for item in result if isinstance(item, dict) and item.get("id")]


PREFERRED_DELETE_ORDER = (
    "ssi/instruction_lifecycle.rego",
    "ssi/lifecycle_rules.rego",
    "ssi/approval_matrix.rego",
    "ssi/common.rego",
)


def delete_policy(policy_id: str) -> bool:
    url = f"{OPA_URL}/v1/policies/{policy_id}"
    request = urllib.request.Request(url, method="DELETE")
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status in (200, 204)
    except urllib.error.HTTPError:
        return False


def clear_policies() -> None:
    remaining = set(list_policy_ids())
    if not remaining:
        print("no existing policies to clear")
        return

    deleted = 0
    while remaining:
        progress = False
        ordered = [policy_id for policy_id in PREFERRED_DELETE_ORDER if policy_id in remaining]
        ordered.extend(sorted(remaining - set(ordered)))

        for policy_id in ordered:
            if policy_id not in remaining:
                continue
            if delete_policy(policy_id):
                remaining.remove(policy_id)
                deleted += 1
                progress = True

        if not progress:
            raise SystemExit(
                "could not clear existing OPA policies; restart the opa service and retry"
            )

    print(f"cleared {deleted} existing policy file(s)")


def upload_all(policies: list[Path], root: Path) -> list[str]:
    errors: list[str] = []

    for policy_path in policies:
        key = policy_id(policy_path, root)
        try:
            upload_policy(policy_path, root)
            print(f"uploaded {key}")
        except urllib.error.HTTPError as exc:
            body = exc.read().decode(errors="replace")
            errors.append(f"{key}: HTTP {exc.code} {body.strip()}")
        except Exception as exc:  # noqa: BLE001 - report and continue
            errors.append(f"{key}: {exc}")

    return errors


def main() -> None:
    wait_for_opa()

    policies = collect_policies(POLICIES_DIR)
    if not policies:
        raise SystemExit(f"no .rego files found under {POLICIES_DIR}")

    print(f"found {len(policies)} policy file(s) under {POLICIES_DIR}")

    clear_policies()

    for attempt in range(1, UPLOAD_RETRIES + 1):
        errors = upload_all(policies, POLICIES_DIR)
        if not errors:
            print("all policies uploaded successfully")
            return

        print(
            f"attempt {attempt}/{UPLOAD_RETRIES} failed for {len(errors)} file(s)",
            file=sys.stderr,
        )
        for error in errors:
            print(f"  {error}", file=sys.stderr)

        if attempt < UPLOAD_RETRIES:
            time.sleep(RETRY_DELAY_SECONDS)

    raise SystemExit(1)


if __name__ == "__main__":
    main()
