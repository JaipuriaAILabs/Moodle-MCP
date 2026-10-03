# Moodle MCP — ops handoff checklist (to launch for ~5,000 Jaipuria users)

The single ordered runbook for the steps **only ops/humans can do** — the in-repo code
work is complete and merged (`5d87d11`), 16 test files green. Do these in order; each
has a done-check. 🔴 = hard blocker · 🟠 = do before wide launch · 🟢 = tuning.

Targets: Render service `jaipuria-moodle-mcp` (`moodle-mcp.tryrehearsal.ai`) · Supabase
**Moodle Data** `sadbfvfcmmxgtatfjfmc` · New Relic **EU** acct `8495484`.
Deeper detail: `PRODUCTION_READINESS.md`, `RECORDING_ENABLEMENT.md`, `GAP_ANALYSIS.md`.

## Status snapshot (2026-09-22)
**✅ Done:** §3 audit backend applied to prod; audit key minted + **validated with a live 204 write**; §4 `create_report` cost cap shipped; §7 retention purge **scheduled** (pg_cron `mcp-security-retention-purge`, daily 03:00 UTC) + `mcp_audit` RLS enabled/locked + privacy notice **drafted** (`docs/PRIVACY_NOTICE.md`); §8 correlation header **merged** (PR #29).
**⏳ In progress:** §3 — pasting `SUPABASE_AUDIT_KEY` + `MCP_AUDIT_HMAC_KEY` into Render + Save/deploy + re-auth (then recording is LIVE).
**⛔ Remaining blockers for a safe 5k launch:** §1 pin OAuth keys (+ cross-client-PKCE), §2 off the free plan, §4 `MCP_REDIS_URL`, §7 **publish** the drafted notice.
**🟠 Do-soon:** §5 New Relic key + alerts, §6 report-queue check, §8 merge PR #30 (Rajika) + gateway deploy, §9 load test + ramp.

---

## 1. Secrets & auth stability (do first — a wrong move here is the outage we already hit)
- [ ] 🔴 **Pin `OAUTH_STORAGE_ENCRYPTION_KEY` + `OAUTH_JWT_SIGNING_KEY`** as permanent Render env values — set once, never regenerate. (A changed encryption key orphaned every DCR client on 2026-09-18 → "client ID not found"; an unstable signing key logs everyone out each deploy.) To rotate later, use a comma-separated keyset `new,old` for a grace window (now supported).
- [ ] 🟠 **Set `OAUTH_ALLOW_CROSS_CLIENT_PKCE=true` and `OAUTH_REDIRECT_HOSTS=<claude.ai connector host>`** on this service — else the hosted Claude.ai connector can hit `mcp_token_exchange_failed` (likely the re-auth pain).
- [ ] 🟢 Confirm `REPORT_LINK_SECRET` (on `moodle-agent`) is set + stable — unset fails closed; rotating it invalidates all live report links.

## 2. Hosting (can't serve 5,000 on free)
- [ ] 🔴 **Move the live Render service off the free plan** to always-on standard; confirm it no longer spins down. Same for the **`moodle-agent`** report service.

## 3. Turn on recording (nothing is recorded today — `mcp_audit` was never applied)
Follow `scripts/RECORDING_ENABLEMENT.md`; in short:
- [ ] 🔴 **Apply** `sql/2026-09-18_mcp_audit_backend.sql` to `sadbfvfcmmxgtatfjfmc` (Supabase SQL editor / `apply_migration`). Done-check: `to_regclass('mcp_audit.tool_calls')` not null, `record_mcp_tool_call` exists, role `mcp_audit_writer` exists.
- [ ] 🔴 **Mint the audit credential**: `SUPABASE_JWT_SECRET=… python3 scripts/mint_audit_jwt.py` → set as **`SUPABASE_AUDIT_KEY`** in Render; set **`MCP_AUDIT_HMAC_KEY`** (`python3 -c "import secrets;print(secrets.token_urlsafe(32))"`).
- [ ] 🔴 **Commit + push the enablement bundle** (a human commits — mass-PII-capture is gated for the agent): `render.yaml` (capture flags), `sql/2026-09-18_mcp_audit_backend.sql`, `scripts/`. Then set in the Render dashboard (render.yaml doesn't sync to the manually-created service): `MCP_CAPTURE_IDENTITY/ARGUMENTS/RESULTS/CLIENT_IP=true`, `MCP_TRUST_PROXY_HEADERS=true`, `MCP_CAPTURE_ARGS_MAX_BYTES=32768`, `MCP_CAPTURE_RESULT_MAX_BYTES=262144`.
- [ ] 🟠 Set **`MCP_REQUIRE_AUDIT=true`** only *after* the two above are verified (fail-closed: refuses to serve a call it can't record; if the backend is missing it errors/boot-fails).
- [ ] Done-check: after a tool call, `select * from mcp_audit.v_activity order by occurred_at desc limit 5;` shows `user_email`, `arguments`, `result`, `source_ip`.

## 4. Scale & abuse limits
- [ ] 🔴 **Set `MCP_REDIS_URL`** — the per-principal tool limiter *and* the new `create_report` cost cap only hold across instances with a shared store; without it, horizontal scaling breaks both.
- [ ] 🟠 Do **not** raise `MCP_IP_RATE_LIMIT` to trust the jaipuria-os egress — it runs on Cloudflare Workers, so its outbound IP is a shared CF range, not a trustable single origin. Rely on the per-principal 90/min cap (keyed on the authenticated email); keep the per-IP cap as a flood guard only. See PRODUCTION_READINESS 0.4.
- [ ] 🟢 Tune `MCP_CREATE_REPORT_LIMIT` (default 60/user/hour) if faculty legitimately generate larger batches.

## 5. Monitoring & alerting
- [ ] 🟠 Set **`NEW_RELIC_LICENSE_KEY`** — the **EU Ingest-License** key for acct 8495484 (NOT a User/`NRAK-` or Insights key; must be the EU-region key, else the OTLP endpoint 401/403s and spans are silently dropped). One switch turns on **all three signals**: tool + `mcp.auth` **spans**, host/process + app **metrics** (`mcp.tool.calls`/`.duration`, `mcp.auth.calls`, CPU/mem/RSS), and (opt-in via `MCP_OTEL_LOGS=true`) **logs**. Traces + metrics default on; each toggles via `MCP_OTEL_TRACES/_METRICS/_LOGS`. Set `MCP_SERVICE_INSTANCE_ID` to the instance id once scaled.
- [ ] 🟠 **Verify ingest** after setting the key: confirm service `jaipuria-moodle-mcp` appears in NR (EU), or grep Render logs for OTLP export errors — export failures are swallowed, so a wrong key/region shows as silence, not an error.
- [ ] 🟠 Run `NEW_RELIC_USER_API_KEY=… ALERT_EMAIL=… bash monitoring/newrelic_uptime_alert.sh` (uptime monitor + policy), then add the span + metric NRQL conditions from `monitoring/README.md`.
- [ ] 🟢 W3C `traceparent` is honoured for end-to-end traces — if jaipuria-os forwards a `traceparent` on its MCP calls, OS→MCP stitches into one distributed trace (no MCP change needed). jaipuria-os uses Cloudflare Workers Observability, not OTel/NR, so this is a nice-to-have, not expected.

## 6. Report generation at scale
- [ ] 🟠 Confirm `AGENT_REPORT_QUEUE=true` + `AGENT_SHARED_SECRET` (≥32 chars) so generation is queued, not synchronous, under load.

## 7. Governance & privacy (required under the accepted all-access model)
- [ ] 🔴 **Publish a privacy notice** — every verified Jaipuria account (incl. students) can read any student's data and generate reports, and activity is logged with identity. Record this accepted decision (already on epic AIA-1012 / `GAP_ANALYSIS.md` G1).
- [ ] 🟠 **Schedule retention**: pg_cron `select public.purge_expired_mcp_security_data();` daily (180-day purge).
- [ ] 🟠 **Lock the `mcp_audit` schema** to admins only; keep raw PII out of New Relic.
- [ ] 🟢 Shorten `REPORT_LINK_TTL_DAYS` from 90 → 7–30 days for student-PII links.

## 8. jaipuria-os side (Rajika)
- [ ] 🟠 Prompt correlation is a **jaipuria-os kernel feature**, not config: the shared MCP client (`packages/mcp-shared/src/client.ts` `#headers()`) sends no `X-Request-Id`, and the per-turn/prompt id lives in the agent loop (`workshop-backend` overseer). To join prompts to `mcp_audit.tool_calls.request_id`, the client must stamp a per-call `X-Request-Id` plumbed down from the agent turn. Affects every connector + crosses the agent→gatekeeper RPC boundary → small, reviewed kernel change (mind the "never log headers/prompts" rule). The MCP already reads `x-request-id`; no MCP change needed.
- [ ] 🟢 Decide `jaipuriaschools.ac.in` handling (jaipuria-os may admit it as USER; MCP denies — confirm the graceful-denial UX is acceptable).

## 9. Verify & ramp
- [ ] Re-add the Moodle MCP connector in Claude.ai / jaipuria-os (a deploy drops the session); `whoami` returns `campuses: all`.
- [ ] Smoke: health 200, `/mcp` 401, a `get_student` by name, one `create_report`, then `v_activity` shows the rows.
- [ ] Ramp the cohort **150 → 500 → 5,000** with New Relic + audit dashboards watched; load-test the target concurrency first.

---
**Blocking set for a safe launch:** §1 (keys), §2 (hosting), §3 (recording), §4 (Redis), §7 privacy notice. Everything else is do-soon or tuning.
