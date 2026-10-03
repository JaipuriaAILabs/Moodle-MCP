# Turn on full MCP activity recording — enablement runbook

Recording is currently **OFF end to end**: the `mcp_audit` backend was never applied
to prod, no audit credential is set, and the capture flags are off. These steps turn
it on. Do them in order; each is verifiable. Steps 1–4 are one-time; step 5 is the
deploy. Nothing persists until steps 1–3 are all done.

Project: **Moodle Data** `sadbfvfcmmxgtatfjfmc` · Service: Render `jaipuria-moodle-mcp`.

---

### 1. Apply the audit backend (DB) — creates the tables the MCP writes to
Run `sql/2026-09-18_mcp_audit_backend.sql` against the Moodle Data project (Supabase
SQL editor, or `apply_migration`). Idempotent; it does **not** touch the live
`reporting_readonly` / OAuth grants.

Verify:
```sql
select to_regclass('mcp_audit.tool_calls') is not null as table_ok,
       exists(select 1 from pg_proc where proname='record_mcp_tool_call') as rpc_ok,
       exists(select 1 from pg_roles where rolname='mcp_audit_writer') as role_ok;
-- expect: t | t | t
```

### 2. Mint the audit credential — least-privilege writer JWT
```bash
SUPABASE_JWT_SECRET='<Moodle Data → Settings → API → JWT Secret>' \
    python3 scripts/mint_audit_jwt.py            # prints the JWT
```
This role can only execute `record_mcp_tool_call` — no student-data access. Same
custom-role JWT mechanism as the existing `reporting_readonly` key.

Sanity-check it can write (and nothing else) via PostgREST:
```bash
curl -sS -X POST "https://<project>.supabase.co/rest/v1/rpc/record_mcp_tool_call" \
  -H "apikey: <SUPABASE_ANON_KEY>" -H "Authorization: Bearer <MINTED_JWT>" \
  -H "Content-Type: application/json" -d '{
    "p_event_id":"00000000-0000-0000-0000-000000000001","p_user_subject":"selftest",
    "p_tool_name":"selftest","p_campus_scope":null,"p_outcome":"attempt",
    "p_error_code":null,"p_duration_ms":1,"p_request_id":"selftest",
    "p_session_subject":null,"p_client_subject":null,"p_metadata":{}}'      # expect: 200/204
```

### 3. Set the Render env vars (dashboard is authoritative — render.yaml does not sync to this service)
| Key | Value |
|-----|-------|
| `SUPABASE_AUDIT_KEY` | the JWT from step 2 |
| `MCP_AUDIT_HMAC_KEY` | any 32+ char high-entropy secret (`python3 -c "import secrets;print(secrets.token_urlsafe(32))"`) — pseudonymises user/session/client subjects |
| `MCP_CAPTURE_IDENTITY` | `true` — real email/name/campuses (AIA-1210 traceability) |
| `MCP_CAPTURE_ARGUMENTS` | `true` — which student/campus was queried (the "who viewed whom" signal) |
| `MCP_CAPTURE_RESULTS` | **`false`** — MEASURED LEVEL: don't duplicate returned marks/attendance into the log |
| `MCP_CAPTURE_CLIENT_IP` | `true` — source IP (trusted rightmost XFF hop) |
| `MCP_TRUST_PROXY_HEADERS` | `true` |
| `MCP_CAPTURE_ARGS_MAX_BYTES` | `32768` |

> **Recording level ("to an extent", 2026-09-21):** capture WHO did WHAT (identity + arguments + source IP + connection events) but NOT the result payloads. Full accountability with far less PII. To later capture results too, set `MCP_CAPTURE_RESULTS=true` (+ `MCP_CAPTURE_RESULT_MAX_BYTES=262144`).
| `MCP_REQUIRE_AUDIT` | `true` **only after steps 1–2 are verified** (fail-closed: the server refuses to serve a tool call it cannot record; if the audit backend is missing, tools will error) |

### 4. (Prompt correlation) add the request-id header from the client harness
So each MCP audit row joins to the harness message that triggered it.

### 5. Deploy the repo change & verify live
Commit + push the `render.yaml` / `config.py` / migration change to `main` (auto-deploy),
then after any real tool call:
```sql
select occurred_at, tool_name, outcome, user_email, source_ip, arguments, result
from mcp_audit.v_activity order by occurred_at desc limit 5;
```
Expect `user_email`, `arguments`, `result`, `source_ip` populated.

---

**Governance (do before wide launch):** restrict the `mcp_audit` schema to admins;
add the privacy-notice line (activity + queried-record identifiers are logged); keep
raw PII out of New Relic; schedule `select public.purge_expired_mcp_security_data();`
(180-day retention) via pg_cron. See `docs/DATA_CAPTURE_PLAN.md`.
