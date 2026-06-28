#!/usr/bin/env bash
# Seed the SSI demo stack with instructions, payments, and many ALERT security events.
#
# Prerequisites on the host (before reset):
#   ollama pull snowflake-arctic-embed:m
#   ollama pull llama3:8b
#
# By default performs a full reset (docker compose down -v, up, Zitadel users) then seeds
# via the harness HTTP API plus extra policy-denial calls inside the harness container.
#
# Usage (from anywhere):
#   ./ssi-demo-harness/seed-demo-data.sh
#   ./ssi-demo-harness/seed-demo-data.sh --seed-only
#   ./ssi-demo-harness/seed-demo-data.sh --full-reset
#
# Environment overrides (optional):
#   HARNESS_URL=http://localhost:8091
#   ADMIN_USER=admin-001
#   ADMIN_PASSWORD=Password1!
#   CREATE_INSTRUCTIONS=18
#   APPROVE_INSTRUCTIONS=12
#   INSTRUCTION_POLICY_RUNS=12
#   PAYMENT_POLICY_RUNS=10
#   ZITADEL_WAIT_SECONDS=35
#   COMPOSE_UP_BUILD=1          # set 0 to skip --build on compose up

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

HARNESS_URL="${HARNESS_URL:-http://localhost:8091}"
ADMIN_USER="${ADMIN_USER:-admin-001}"
ADMIN_PASSWORD="${ADMIN_PASSWORD:-Password1!}"

CREATE_INSTRUCTIONS="${CREATE_INSTRUCTIONS:-18}"
APPROVE_INSTRUCTIONS="${APPROVE_INSTRUCTIONS:-12}"
SUBMIT_INSTRUCTIONS="${SUBMIT_INSTRUCTIONS:-4}"
REJECT_INSTRUCTIONS="${REJECT_INSTRUCTIONS:-2}"
INSTRUCTION_POLICY_RUNS="${INSTRUCTION_POLICY_RUNS:-12}"

CREATE_PAYMENTS="${CREATE_PAYMENTS:-12}"
SUBMIT_PAYMENTS="${SUBMIT_PAYMENTS:-8}"
APPROVE_PAYMENTS="${APPROVE_PAYMENTS:-5}"
REJECT_PAYMENTS="${REJECT_PAYMENTS:-2}"
PAYMENT_POLICY_RUNS="${PAYMENT_POLICY_RUNS:-10}"

ZITADEL_WAIT_SECONDS="${ZITADEL_WAIT_SECONDS:-35}"
COMPOSE_UP_BUILD="${COMPOSE_UP_BUILD:-1}"

DO_FULL_RESET=1
DO_SEED=1

usage() {
  sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'
  echo
  echo "Options:"
  echo "  --full-reset   Stop stack, remove volumes, rebuild, re-seed Zitadel (default)"
  echo "  --seed-only    Skip reset; only run harness seed actions (stack must be up)"
  echo "  -h, --help     Show this help"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --full-reset)
      DO_FULL_RESET=1
      shift
      ;;
    --seed-only)
      DO_FULL_RESET=0
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

log() {
  printf '\n>>> %s\n' "$*"
}

summarize_json() {
  python3 -c '
import json, sys
d = json.load(sys.stdin)
print("  ok=%s succeeded=%s failed=%s" % (d.get("ok"), d.get("succeeded"), d.get("failed", 0)))
'
}

reset_stack() {
  log "Stopping stack and removing volumes"
  (cd "${REPO_ROOT}" && docker compose down -v --remove-orphans)

  log "Starting stack"
  if [[ "${COMPOSE_UP_BUILD}" == "1" ]]; then
    (cd "${REPO_ROOT}" && docker compose up -d --build)
  else
    (cd "${REPO_ROOT}" && docker compose up -d)
  fi

  log "Waiting ${ZITADEL_WAIT_SECONDS}s for Zitadel"
  sleep "${ZITADEL_WAIT_SECONDS}"

  log "Seeding Zitadel demo users"
  PAT="$(docker exec zitadel-login cat /zitadel/bootstrap/login-client.pat | tr -d '\n')"
  (cd "${REPO_ROOT}/zitadel-seed" && ZITADEL_PAT="${PAT}" python3 seed.py)
}

harness_login() {
  LOGIN_JSON="$(curl -sf -X POST "${HARNESS_URL}/api/auth/login" \
    -H 'Content-Type: application/json' \
    -d "{\"user_id\":\"${ADMIN_USER}\",\"password\":\"${ADMIN_PASSWORD}\"}")"
  HARNESS_TOKEN="$(printf '%s' "${LOGIN_JSON}" | python3 -c 'import sys,json; print(json.load(sys.stdin)["session_token"])')"
  HARNESS_SESSION_ID="$(printf '%s' "${LOGIN_JSON}" | python3 -c 'import sys,json; print(json.load(sys.stdin)["session_id"])')"
  export HARNESS_TOKEN HARNESS_SESSION_ID
}

harness_action() {
  local action="$1"
  local count="${2:-}"
  log "Harness action: ${action}${count:+ (count=${count})}"
  if [[ -n "${count}" ]]; then
    curl -sf -X POST "${HARNESS_URL}/api/actions/${action}" \
      -H "Authorization: Bearer ${HARNESS_TOKEN}" \
      -H "X-Session-Id: ${HARNESS_SESSION_ID}" \
      -H 'Content-Type: application/json' \
      -d "{\"count\":${count}}" | summarize_json
  else
    curl -sf -X POST "${HARNESS_URL}/api/actions/${action}" \
      -H "Authorization: Bearer ${HARNESS_TOKEN}" \
      -H "X-Session-Id: ${HARNESS_SESSION_ID}" | summarize_json
  fi
}

harness_scenario_loop() {
  local action="$1"
  local runs="$2"
  log "Harness scenario: ${action} x${runs}"
  local i ok=0 fail=0
  for i in $(seq 1 "${runs}"); do
    if curl -sf -X POST "${HARNESS_URL}/api/actions/${action}" \
      -H "Authorization: Bearer ${HARNESS_TOKEN}" \
      -H "X-Session-Id: ${HARNESS_SESSION_ID}" \
      | python3 -c 'import sys,json; sys.exit(0 if json.load(sys.stdin).get("ok") else 1)'; then
      ok=$((ok + 1))
    else
      fail=$((fail + 1))
      echo "  run ${i}: completed with expected step failure(s)"
    fi
  done
  echo "  finished: ${ok} fully passed, ${fail} with step failure(s) (denials may still be recorded)"
}

seed_extra_alerts() {
  log "Extra policy denials (instruction + payment ALERTs)"
  docker exec ssi-demo-harness python3 -u -c "
import os, traceback
os.environ['ILM_URL'] = 'http://instruction-service:8000'
os.environ['PAYMENT_SERVICE_URL'] = 'http://payment-service:8093'
os.environ['ZITADEL_INTERNAL_URL'] = 'http://zitadel-proxy'
os.environ['ZITADEL_HOST_HEADER'] = 'localhost'
os.environ['ZITADEL_SERVICE_PAT_FILE'] = '/zitadel/bootstrap/login-client.pat'
os.environ['USERS_FILE'] = '/app/zitadel-seed/users.yaml'
try:
    from datetime import date, timedelta
    from harness.config import Settings
    from harness.fixtures import build_instruction_payload, load_users
    from harness.helpers import (
        _session_for_user,
        auth_client,
        ilm_client,
        payment_client,
        _fetch_api_instructions,
        _fetch_api_payments,
    )
    settings = Settings()
    seed = load_users(settings.users_file)
    auth = auth_client(settings)
    ilm = ilm_client(settings)
    ps = payment_client(settings)
    admin = _session_for_user(auth, seed, settings, 'admin-001')
    pending = [i for i in _fetch_api_instructions(settings, admin, status='PENDING')]
    inst_alerts = pay_alerts = 0

    for _ in range(15):
        s = _session_for_user(auth, seed, settings, 'ficc-201')
        if ilm.create_instruction(s, build_instruction_payload(owning_lob='FICC')).status_code == 403:
            inst_alerts += 1

    for instr in pending[:20]:
        creator = (instr.get('created_by') or {}).get('user_id')
        if not creator:
            continue
        s = _session_for_user(auth, seed, settings, creator)
        if ilm.approve_instruction(s, instr['instruction_id']).status_code == 403:
            inst_alerts += 1

    for instr in pending[:15]:
        s = _session_for_user(auth, seed, settings, 'fx-201')
        if ilm.approve_instruction(s, instr['instruction_id']).status_code == 403:
            inst_alerts += 1

    approved = [
        i for i in _fetch_api_instructions(settings, admin)
        if i.get('status') in ('STANDING', 'SINGLE_USE')
    ]
    ficc = [i for i in approved if i.get('owning_lob') == 'FICC']
    vd = (date.today() + timedelta(days=1)).isoformat()
    if ficc:
        instr_id = ficc[0]['instruction_id']
        for _ in range(15):
            s = _session_for_user(auth, seed, settings, 'pay-201')
            if ps.create_payment(s, instr_id, 1_000_000.0, vd).status_code == 403:
                pay_alerts += 1
        for p in _fetch_api_payments(settings, admin, status='DRAFT')[:15]:
            s = _session_for_user(auth, seed, settings, 'pay-101')
            if ps.submit_payment(s, p['payment_id']).status_code == 403:
                pay_alerts += 1
        for p in _fetch_api_payments(settings, admin, status='SUBMITTED')[:15]:
            if (p.get('created_by') or {}).get('user_id') == 'pay-101':
                s = _session_for_user(auth, seed, settings, 'pay-101')
                if ps.approve_payment(s, p['payment_id']).status_code == 403:
                    pay_alerts += 1
            if p.get('owning_lob') == 'FICC':
                s = _session_for_user(auth, seed, settings, 'pay-203')
                if ps.approve_payment(s, p['payment_id']).status_code == 403:
                    pay_alerts += 1

    print('  triggered instruction denials:', inst_alerts)
    print('  triggered payment denials:', pay_alerts)
except Exception:
    traceback.print_exc()
    raise SystemExit(1)
"
}

print_mongo_alert_counts() {
  log "MongoDB security event ALERT counts"
  docker exec mongodb mongosh --quiet security_events --eval '
const inst = db["instruction-service"];
const pay = db["payment-service"];
printjson({
  instruction_ALERT: inst.countDocuments({severity:"ALERT"}),
  instruction_INFO: inst.countDocuments({severity:"INFO"}),
  payment_ALERT: pay.countDocuments({severity:"ALERT"}),
  payment_INFO: pay.countDocuments({severity:"INFO"}),
});
'
}

print_harness_status() {
  log "Harness status"
  curl -sf "${HARNESS_URL}/api/status" \
    -H "Authorization: Bearer ${HARNESS_TOKEN}" \
    -H "X-Session-Id: ${HARNESS_SESSION_ID}" \
    | python3 -m json.tool
}

run_seed() {
  log "Logging in to harness as ${ADMIN_USER}"
  harness_login

  log "Base instruction lifecycle"
  harness_action create-instructions "${CREATE_INSTRUCTIONS}"
  harness_action approve-instructions "${APPROVE_INSTRUCTIONS}"
  harness_action submit-instructions "${SUBMIT_INSTRUCTIONS}"
  harness_action reject-instructions "${REJECT_INSTRUCTIONS}"

  harness_scenario_loop run-policy-scenario "${INSTRUCTION_POLICY_RUNS}"

  log "Base payment lifecycle"
  harness_action create-payments "${CREATE_PAYMENTS}"
  harness_action submit-payments "${SUBMIT_PAYMENTS}"
  harness_action approve-payments "${APPROVE_PAYMENTS}"
  harness_action reject-payments "${REJECT_PAYMENTS}"

  harness_scenario_loop run-payment-policy-scenario "${PAYMENT_POLICY_RUNS}"

  seed_extra_alerts
  print_mongo_alert_counts
  print_harness_status

  log "Done — open http://localhost:8091 for harness status"
  log "Instruction security events: http://localhost:8000/ui/security-events/"
  log "Payment security events: http://localhost:8093/ui/security-events/"
}

main() {
  if [[ "${DO_FULL_RESET}" == "1" ]]; then
    reset_stack
  fi

  if [[ "${DO_SEED}" == "1" ]]; then
    run_seed
  fi
}

main "$@"
