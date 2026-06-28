# Search text profiles

Each YAML file declares which fields are flattened into **`search_text`** — the string
embedded as the dense vector and indexed for BM25 in Qdrant.

| File | Qdrant `source` | Wired to builder |
|------|-----------------|------------------|
| `instruction_security_event.yaml` | `instruction_security_event` | **Yes** |
| `instruction_state.yaml` | `instruction_state` | **Yes** |
| `payment_security_event.yaml` | `payment_security_event` | **Yes** |
| `payment_fact.yaml` | `payment_fact` | **Yes** |

## What is *not* in these files

Full JSON documents remain in the Qdrant **payload** (`security_event`,
`instruction_snapshot`, `payment_snapshot`, etc.) and in the admin UI. Only fields
listed under `includes` (and shared `profiles`) feed the embedding string.

Paths use dot notation from the profile's `context_root` document (usually `merged`
for instruction security events, or the raw Kafka fact/event dict for others).

## Editing

After changing a profile, run:

```bash
cd ssi-indexer && python3 -m pytest tests/test_search_profiles.py -q
```

View wired field lists in the **Search text profiles** panel on the ETL admin UI
(`GET /api/search-profiles`).
