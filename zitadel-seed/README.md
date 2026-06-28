# ZITADEL Seed

Loads demo users from `users.yaml` into ZITADEL via the v2 User Management API.

Used after a fresh Docker Compose start (or volume reset) when only bootstrap users exist.

## Users

Includes:

- **Middle office** — SSI instruction creators (`mo-100`, `mo-101`, …)
- **Profit center approvers** — FICC, FX, DESK_RATES (`ficc-201`, `fx-300`, …)
- **Front office** — payment submitters per desk (`fo-ficc-101`, `fo-fx-101`, `fo-rates-101`)
- **Payment creators / approvers** — middle office payment staff (`pay-101` … `pay-400`) with amount-limit clubs and `covering_lobs`
- **Service accounts** — `etl-reader` (ILM reads, excluded from VIEW events), `svc-instruction` (instruction service → authz), `svc-payment` (payment service → authz and ILM)

Default password: **`Password1!`** (see `defaults.password` in `users.yaml`).

Login names: `{user_id}@ssi.local` (e.g. `mo-100@ssi.local`).

### Payment amount-limit clubs

| Group | Max payment (USD) |
|-------|-------------------|
| `UP_TO_100_MILLION_CLUB` | $100 M |
| `UP_TO_1_BILLION_CLUB` | $1 B |
| `UP_TO_100_BILLION_CLUB` | $100 B |

## Run

```bash
PAT=$(docker exec zitadel-login cat /zitadel/bootstrap/login-client.pat | tr -d '\n')
cd zitadel-seed
ZITADEL_PAT="$PAT" python3 seed.py
```

Options:

```bash
python3 seed.py --dry-run          # print actions without writing
python3 seed.py --file users.yaml  # alternate seed file
```

## Environment

| Variable | Default |
|----------|---------|
| `ZITADEL_URL` | `http://localhost:8080` |
| `ZITADEL_PAT` | required — Org Owner or login-client PAT |

User metadata (`subject_user_id`, `title`, `roles`, `lob`, `supervisor_id`, `covering_lobs`, `groups`) is stored in ZITADEL and mapped to application `Subject` on JWT login.

## ZITADEL console

http://localhost:8080/ui/console
