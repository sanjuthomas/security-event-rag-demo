# ZITADEL Seed

Loads demo users from `users.yaml` into ZITADEL via the v2 User Management API.

Used after a fresh Docker Compose start (or volume reset) when only bootstrap users exist.

## Users

Includes middle office creators, FICC/FX/DESK approvers, and the **`etl-reader`** service account (MIDDLE_OFFICE — used by `security-event-qdrant-etl` to read instructions without emitting VIEW security events).

Default password: **`Password1!`** (see `defaults.password` in `users.yaml`).

Login names: `{user_id}@ssi.local` (e.g. `mo-100@ssi.local`).

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

User metadata (`subject_user_id`, `title`, `roles`, `lob`, `supervisor_id`) is stored in ZITADEL and mapped to ILM `Subject` on JWT login.

## ZITADEL console

http://localhost:8080/ui/console
