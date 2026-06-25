# OPA Policy Seed

Version-controlled **Rego policies** uploaded to OPA on Docker Compose startup.

ILM calls `POST /v1/data/ssi/instruction_lifecycle/allow` before every instruction create or mutation.

## Layout

```
policies/
└── ssi/
    ├── common.rego              # Shared helpers (roles, LOB, dates)
    ├── approval_matrix.rego     # Who may approve whom by title + LOB
    ├── lifecycle_rules.rego     # Valid state transitions
    └── instruction_lifecycle.rego  # allow rules per action
```

## Authorization model

| Actor | Roles | Scope |
|-------|-------|-------|
| Middle office | `INSTRUCTION_CREATOR`, `MIDDLE_OFFICE` | Create, update, submit, delete (draft/pending), view all LOBs |
| Profit center | `INSTRUCTION_APPROVER` + `lob` | Approve, reject, suspend, use, view matching `owning_lob` |

Valid LOB values: `FICC`, `FX`, or `DESK_<name>`.

**Out of scope:** Treasury liquidity instructions.

### Actions governed

`CREATE`, `UPDATE`, `DELETE`, `SUBMIT`, `APPROVE`, `REJECT`, `SUSPEND`, `REACTIVATE`, `USE`, `VIEW`

Policy denials surface as HTTP 403 on ILM and `ALERT` security events on Kafka.

## Evaluate locally

```bash
curl -s http://localhost:8181/v1/data/ssi/instruction_lifecycle/allow \
  -H 'Content-Type: application/json' \
  -d '{
    "input": {
      "action": "CREATE",
      "subject": {
        "roles": ["INSTRUCTION_CREATOR", "MIDDLE_OFFICE"],
        "title": "Analyst",
        "user_id": "mo-100"
      },
      "instruction": {
        "owning_lob": "FICC",
        "status": "DRAFT",
        "type": "STANDING",
        "effective_date": "2026-06-24T00:00:00Z",
        "end_date": "2027-06-24T00:00:00Z",
        "created_by": { "user_id": "mo-100", "title": "Analyst" }
      },
      "account": { "owning_lob": "FICC" }
    }
  }'
```

## Docker

The `opa-policy-seed` service runs once after `opa` starts and uploads policies from the mounted `policies/` directory.

OPA API: http://localhost:8181
