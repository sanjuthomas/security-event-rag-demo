# Instruction Lifecycle Manager

REST API for **SSI settlement route template** lifecycle — domestic and international wires.

An instruction defines the **route** (funding account, debtor/creditor, bank chain, currency, validity, approval). It is **not** a payment message — no amount or value date on the template.

Middle office analysts create instructions **on behalf of** P&L profit centers (`owning_lob`). Treasury bank-liquidity instructions are **out of scope**.

## URLs (Docker)

| URL | Description |
|-----|-------------|
| http://localhost:8000/docs | OpenAPI |
| http://localhost:8000/ui/ | Instruction browser |
| http://localhost:8000/ui/security-events/ | Security event monitor (Mongo change stream) |
| http://localhost:8000/api/v1/instructions | REST API |

## Authentication

Production path uses **ZITADEL JWT** (`AUTH_MODE=jwt` in Docker). The test harness and ETL use ZITADEL session tokens.

For local header-based testing without JWT, set `AUTH_MODE=headers` and pass subject headers:

| Header | Middle office | Profit center |
|--------|---------------|---------------|
| `X-Subject-User-Id` | `mo-100` | `ficc-201` |
| `X-Subject-Title` | `Analyst` | `Associate` |
| `X-Subject-Roles` | `INSTRUCTION_CREATOR,MIDDLE_OFFICE` | `INSTRUCTION_APPROVER` |
| `X-Subject-Lob` | omit | `FICC` |

Demo users are defined in `zitadel-seed/users.yaml` (password `Password1!`).

## Owning profit center (`owning_lob`)

| Value | Meaning |
|-------|---------|
| `FICC` | Fixed income, currencies & commodities |
| `FX` | Foreign exchange desk |
| `DESK_<name>` | Other profit centers, e.g. `DESK_RATES` |

## Instruction schema (summary)

| Field | Notes |
|-------|-------|
| `instruction_type` | `STANDING` or `SINGLE_USE` |
| `wire_scope` | `DOMESTIC` or `INTERNATIONAL` |
| `currency` | ISO 4217 route currency (required) |
| `funding_account`, `debtor`, `creditor`, agents | Settlement route |
| `effective_date`, `end_date` | Template validity |
| `created_by`, `approved_by`, `rejected_by` | Lifecycle parties (copied on each version) |

No `instructed_amount`, `payment_identification`, or remittance fields.

## Security events (SIEM)

Every authorized create/read/mutation emits a document to MongoDB `security_events.instruction-lifecycle-manager` and publishes to Kafka topic `instruction-security-events`.

| Outcome | Severity | When |
|---------|----------|------|
| Authorized action | `INFO` | OPA allowed |
| Policy denial | `ALERT` | OPA denied before any write |

Events use ECS-style fields (`event`, `actor`, `resource`, `source`).

**Excluded actors:** Service user `etl-reader` does not emit VIEW events (prevents ETL → Kafka feedback loop). Configure via `SECURITY_EVENT_EXCLUDED_USER_IDS`.

## Example: create instruction

```bash
curl -s -X POST http://localhost:8000/api/v1/instructions \
  -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer <zitadel-session-token>' \
  -H 'X-Session-Id: <session-id>' \
  -d '{
    "instruction_type": "SINGLE_USE",
    "owning_lob": "FICC",
    "wire_scope": "DOMESTIC",
    "currency": "USD",
    "funding_account": {
      "account_id": "DDA-FICC-01",
      "account_name": "FICC Client Payments",
      "owning_lob": "FICC"
    },
    "debtor": { "name": "Client Fund A", "postal_address": { "country": "US" } },
    "debtor_account": {
      "identification_scheme": "PROPRIETARY",
      "identification": "DDA-FICC-01",
      "currency": "USD"
    },
    "debtor_agent": {
      "financial_institution": {
        "scheme": "CLEARING_SYSTEM",
        "identification": "021000021",
        "clearing_system_id": "USABA"
      }
    },
    "creditor": { "name": "Counterparty LLC", "postal_address": { "country": "US" } },
    "creditor_account": {
      "identification_scheme": "PROPRIETARY",
      "identification": "9988776655",
      "currency": "USD"
    },
    "creditor_agent": {
      "financial_institution": {
        "scheme": "CLEARING_SYSTEM",
        "identification": "011401533",
        "clearing_system_id": "USABA"
      }
    },
    "charge_bearer": "SHAR",
    "effective_date": "2026-06-24T00:00:00Z",
    "end_date": "2027-06-24T00:00:00Z"
  }'
```

## Run locally

```bash
cd instruction-lifecycle-manager
pip install -e .
uvicorn instruction_lifecycle_manager.main:app --reload --port 8000
```

Requires MongoDB, OPA, and (for JWT mode) ZITADEL — see root `docker-compose.yml`.
