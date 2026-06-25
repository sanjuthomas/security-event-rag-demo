# Security Event Test Harness

Web UI and CLI helpers for end-to-end testing of the instruction lifecycle API with **ZITADEL OIDC** authentication.

Use it to generate realistic lifecycle traffic (create → submit → approve/reject) that flows through Kafka into the ETL and chat pipeline.

## URL

http://localhost:8091

## Actions (UI)

Each action has a count field and a run button:

| Action | Description |
|--------|-------------|
| Create instructions | Seed N instructions via ILM API (varied LOB / type / currency) |
| Submit | Submit draft instructions |
| Approve | Approve pending instructions (matching OPA approval matrix) |
| Reject | Reject pending instructions |
| Run scenario | Fixed multi-step OPA scenario (success + expected denials) |

## Authentication

Uses ZITADEL **Session API** with the login-client PAT from bootstrap volume. Demo users are in `zitadel-seed/users.yaml` (password `Password1!`).

Seed users after a fresh stack:

```bash
PAT=$(docker exec zitadel-login cat /zitadel/bootstrap/login-client.pat | tr -d '\n')
cd zitadel-seed && ZITADEL_PAT="$PAT" python3 seed.py
```

Includes service user **`etl-reader`** (used by the ETL, not the harness UI).

## Instruction payloads

Fixtures build the **SSI route template** schema (`currency` field, no payment amounts). See `src/harness/fixtures.py`.

## Configuration (Docker)

| Variable | Default |
|----------|---------|
| `ILM_URL` | `http://instruction-lifecycle-manager:8000` |
| `ZITADEL_URL` | `http://zitadel-proxy` |
| `ZITADEL_HOST_HEADER` | `localhost` |
| `USERS_FILE` | `/app/zitadel-seed/users.yaml` |

## Run locally

```bash
cd security-event-test-harness
pip install -e .
security-event-test-harness-ui   # :8091
```

CLI entry point: `security-event-test-harness`

## Docker

```bash
docker compose up -d security-event-test-harness
```

Requires ILM and ZITADEL running.
