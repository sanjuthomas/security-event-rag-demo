# SSI Demo Harness

Web UI and CLI helpers for driving **instruction** and **payment** lifecycles with **ZITADEL OIDC** authentication.

Use it to generate realistic traffic that flows through Kafka into the indexer and chat pipeline, including OPA policy demo scenarios that verify `ALERT` and `INFO` security event counts.

## URL

http://localhost:8091

## Actions (UI)

Each action has a count field and a run button:

### Instructions

| Action | Description |
|--------|-------------|
| Create instructions | Seed N instructions via ILM API (varied LOB / type / currency) |
| Submit | Submit draft instructions |
| Approve | Approve pending instructions (matching OPA approval matrix) |
| Reject | Reject pending instructions |
| Run scenario | Fixed 8-step OPA scenario (success + expected denials) |

The instruction scenario verifies MongoDB security event count increases and includes steps such as creator self-approval denial and wrong-LOB read denial.

### Payments

| Action | Description |
|--------|-------------|
| Create payments | Seed N payments against approved instructions |
| Submit payments | Front-office users submit DRAFT payments |
| Approve payments | Funding approvers approve SUBMITTED payments |
| Run payment scenario | Fixed 7-step OPA scenario (DRAFT → SUBMIT → APPROVE with denials) |

The payment scenario verifies MongoDB counts: **+4 ALERT** and **+3 INFO** events in `security_events.payment-service`:

1. `pay-101` creates FICC payment (DRAFT, INFO)
2. `pay-201` (approver only) tries to create → DENY (ALERT)
3. `pay-101` (middle office) tries to submit → DENY (ALERT)
4. `fo-ficc-101` submits payment → SUBMITTED (INFO)
5. `pay-101` tries to approve own payment → DENY (ALERT)
6. `pay-203` (FX-only) tries to approve → DENY (ALERT)
7. `pay-201` approves → APPROVED (INFO)

Requires at least one approved FICC instruction (run instruction create + approve first, or the full instruction scenario).

## Authentication

Uses ZITADEL **Session API** with the login-client PAT from bootstrap volume. Demo users are in `zitadel-seed/users.yaml` (password `Password1!`).

Seed users after a fresh stack:

```bash
PAT=$(docker exec zitadel-login cat /zitadel/bootstrap/login-client.pat | tr -d '\n')
cd zitadel-seed && ZITADEL_PAT="$PAT" python3 seed.py
```

Includes service accounts **`etl-reader`**, **`svc-instruction`**, and **`svc-payment`** (not used by the harness UI).

## Instruction payloads

Fixtures build the **SSI route template** schema (`currency` field, no payment amounts). See `src/harness/fixtures.py`.

## Configuration (Docker)

| Variable | Default |
|----------|---------|
| `ILM_URL` | `http://instruction-service:8000` |
| `PAYMENT_SERVICE_URL` | `http://payment-service:8093` |
| `ZITADEL_URL` | `http://zitadel-proxy` |
| `ZITADEL_HOST_HEADER` | `localhost` |
| `USERS_FILE` | `/app/zitadel-seed/users.yaml` |
| `MONGODB_URI` | `mongodb://mongodb:27017/?replicaSet=rs0` |
| `SECURITY_EVENTS_COLLECTION` | `instruction-service` |
| `PAYMENT_SECURITY_EVENTS_COLLECTION` | `payment-service` |

## Run locally

```bash
cd ssi-demo-harness
pip install -e .
ssi-demo-harness-ui   # :8091
```

CLI entry point: `ssi-demo-harness`

## Docker

```bash
docker compose up -d ssi-demo-harness
```

Requires ILM, payment service, and ZITADEL running.
