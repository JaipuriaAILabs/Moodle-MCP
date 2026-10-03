# Moodle MCP — production readiness plan (5,000 Jaipuria users via jaipuria-os)

Scope: take the Moodle MCP from "works for a demo / a few faculty" to "safe and
reliable for ~5,000 students + professors through jaipuria-os." Grouped by priority with
owner and rough effort. **P0 = launch blocker.** Synthesised from the Sept 2026
hardening + client-integration + activity-recording work.

> Updated 2026-10-01: repository security closure and current validation evidence are in
> `SECURITY_AUDIT_2026-10-01.md`. Items below marked as code-complete still require their stated
> production configuration or institutional approval.

Legend — owner: **ops** (Render/Supabase/env, not doable from code), **eng** (this
repo), **rajika** (jaipuria-os repo), **decision** (data owner).

---

## P0 — Launch blockers

**0.1 Move off the Render free plan.** `render.yaml` declares `plan: standard`, but the
LIVE service still runs the **free plan** (spin-down) per its own header comment. Free
= cold-start tool-call hangs + no concurrency headroom → unusable at 5,000. → Set the
live service to an always-on paid plan; confirm it never spins down; size for expected
concurrency. *(ops · ½ day)*

**0.2 Pin the OAuth keys; they caused a real outage.** `OAUTH_STORAGE_ENCRYPTION_KEY`
and `OAUTH_JWT_SIGNING_KEY` must be **stable, permanent** values. A changed encryption
key orphaned every registered client ("client ID not found", forced re-add) on
2026-09-18. Rotation is now safe via the comma-separated keyset (MultiFernet, shipped
`018f4c3`), and an unstable signing key logs everyone out each deploy. → Confirm both
are fixed dashboard values; document them as never-regenerate. *(ops · 1 h + eng done)*

**0.3 Stand up activity recording.** The `mcp_audit` backend was never applied to prod,
so **nothing is recorded today**. → Follow `scripts/RECORDING_ENABLEMENT.md`: apply
`sql/2026-09-18_mcp_audit_backend.sql`, mint the `mcp_audit_writer` JWT, set the audit +
capture env. *(ops/eng · ½ day)*

**0.4 Fix rate limiting for the jaipuria-os topology.** jaipuria-os runs on **Cloudflare
Workers**, so its outbound MCP `fetch` egresses from **Cloudflare's shared IP ranges** — not
one stable, trustable IP. You therefore **cannot** allowlist or raise the per-IP cap for
"the harness origin" (it is shared with the whole internet), and unless the OS forwards the
real end-user IP in `X-Forwarded-For`, every OS user collapses onto a handful of CF egress
IPs → the per-IP cap (`MCP_IP_RATE_LIMIT` = 1200/min) becomes a shared ceiling. → The real
control is the **per-principal** limit (90/min, keyed on the authenticated email), which
already bounds each user regardless of egress IP; keep `MCP_IP_RATE_LIMIT` purely as a flood
guard, do **not** raise it to trust an egress. For horizontal scale set `MCP_REDIS_URL` so
the per-principal limiter + `create_report` cost cap hold across instances (the in-process
limiter diverges with >1 instance). Direct Claude.ai connectors still benefit from the
campus-NAT headroom already configured. *(ops + eng · ½ day)*

**0.4a `create_report` cost cap (done 2026-09-21).** Per-principal budget on the one
money-spending tool (`_enforce_report_budget`, default 60/user/hour via
`MCP_CREATE_REPORT_LIMIT` / `_WINDOW_SECONDS`), so no single account can drive runaway
LLM generation under open access. Redis-backed when `MCP_REDIS_URL` is set (so set it —
0.4 — for the cap to hold across instances). *(eng — shipped)*

**0.5 Governance sign-off for RBAC + recording.** OFF/SHADOW retain historical all-campus
access for non-student Jaipuria accounts, but roster students can no longer inherit that
fallback: they are denied or hard-bounded to their own student/campus/batch. ENFORCE supports
explicit role/campus educator grants. Before launch, choose the mode and whether direct student
access is enabled; lock `mcp_audit` to admins; publish a privacy notice; keep raw PII out of New
Relic; confirm 180-day retention (`purge_expired_mcp_security_data`). Identity/argument/result
capture remains separately opt-in. *(decision + ops; eng shipped)*

---

## P1 — High (before or immediately after launch)

**1.0 Claude.ai connector: cross-client PKCE flag (found in review).** `OAUTH_ALLOW_CROSS_CLIENT_PKCE`
now defaults **off**, so the OAuthProxy no longer tolerates Claude.ai's multi-node
pattern (register on one node, authorize/redeem across others) unless BOTH
`OAUTH_ALLOW_CROSS_CLIENT_PKCE=true` **and** the redeeming host is in `OAUTH_REDIRECT_HOSTS`.
Left off, the hosted Claude.ai connector can hit `mcp_token_exchange_failed` — a likely
contributor to the re-auth pain. Cryptographically it's a hardening (default-deny,
bounded), so keep it off in general but **set both on the deployments that serve the
Claude.ai connector**. *(ops · 15 min)*

**1.1 Report generation at scale.** `create_report` in sync mode blocks under load;
queue mode (migration 0014, applied) + a worker scales it. Confirm `AGENT_REPORT_QUEUE`
+ `AGENT_SHARED_SECRET`, and that the **moodle-agent** service is also off the free
plan. S3 report caching (shipped) limits regen cost. *(ops + eng · 1 day)*

**1.2 Monitoring & alerting (code complete; ops activation required).** The MCP emits
PII-safe OpenTelemetry traces/metrics with bounded tool, outcome, role, effective campus-scope,
and shadow-decision dimensions; `NR_ALERTS.md` contains the alert queries. Configure the New Relic
license/OTLP environment and create the production health, error-rate, p95, would-deny and uptime
alerts. The `mcp_audit` ledger + `v_activity` view remain the detailed activity source.
*(ops; eng shipped)*

**1.3 CI gate (done).** GitHub Actions runs the repository tests on PRs/pushes and now includes
`pip-audit` plus Bandit; Dependabot covers Python and Actions dependencies. *(eng shipped)*

**1.4 Prompt correlation (jaipuria-os kernel, Rajika).** The MCP already reads `x-request-id`; the join needs jaipuria-os to stamp a per-turn `X-Request-Id` on its MCP calls. This is a kernel change in `packages/mcp-shared/src/client.ts` + the agent loop (not config) — see OPS_HANDOFF §8.
*(rajika · 1 h)*

**1.5 `jaipuriaschools.ac.in` handling.** jaipuria-os may admit that domain as USER, but the MCP
denies it (not a subdomain of `jaipuria.ac.in`). Confirm that's intended (schools staff
have no MBA data) and that the denial is a clean, explained message, not an error.
*(decision · 15 min)*

**1.6 Deploy hygiene.** `/health` zero-downtime deploys are set. Confirm the live
service actually reconnects OAuth sessions across deploys now that keys are pinned
(0.2) — i.e. the "re-auth on every deploy" symptom is gone. *(ops · verify)*

---

## P2 — Hardening / scale tail

- **2.1 Google tokeninfo per request.** `verify_token` calls Google on every request →
  latency + quota at 5,000 concurrent. Confirm/enable token caching. *(eng)*
- **2.2 S3 offload for oversized capture.** Payloads > 256 KB store a size-marked
  preview; add a Supabase Storage offload (pointer in the row) if truly-complete large
  payloads are required. *(eng · 1 day)*
- **2.3 Deploy the gateway** (`rehearsal-mcp-gateway`, PR #1) for a single trusted ingress and
  another authorization layer — also resolves 0.4 (rate-limit origin). Application-level
  educator campus RBAC and student self-scope now exist; database RLS still needs the separate
  per-request claim design described in `ACCESS_AND_PII_MODEL.md`. *(eng/ops)*
- **2.4 Scheduled purge.** pg_cron `select public.purge_expired_mcp_security_data();`
  daily, so audit + OAuth state respect retention automatically. *(ops)*
- **2.5 Load test** the target concurrency (ramp 150 → 500 → 5,000) before the full
  cohort; watch DB connections, tokeninfo latency, audit write throughput. *(eng)*
- **2.6 Backups/DR** posture for the audit ledger and OAuth store confirmed. *(ops)*

---

## Launch checklist (ordered)
1. [ ] Live service on always-on paid plan, no spin-down (0.1)
2. [ ] `OAUTH_STORAGE_ENCRYPTION_KEY` + `OAUTH_JWT_SIGNING_KEY` pinned & documented (0.2)
3. [ ] Audit backend applied + credential + capture env set; `v_activity` shows rows (0.3)
4. [ ] `MCP_REDIS_URL` set; per-IP limit raised or gateway enforced (0.4)
5. [ ] Governance: `mcp_audit` locked to admins, privacy notice, retention (0.5)
6. [ ] Report queue + worker; moodle-agent off free plan (1.1)
7. [ ] New Relic health/error/latency alerts live (1.2)
8. [x] CI running tests plus dependency/static security scans on PRs (1.3)
9. [ ] jaipuria-os `X-Request-Id` header deployed; join verified (1.4)
10. [ ] Cohort ramp 150 → 500 → 5,000 with monitoring (2.5)

## Status snapshot (2026-09-18)
- **Done & deployed:** all-Jaipuria access; name-or-id resolution (`c0cd4d9`); report
  directness; OAuth rotation-tolerant keyset (`018f4c3`); activity-capture code (flag-
  gated); 206 tests green.
- **Ready, awaiting ops:** audit backend migration + recording enablement (0.3);
  capture-ON `render.yaml` (uncommitted — enabling mass PII capture is classifier-gated
  from here, so a human commits it).
- **Not started:** hosting plan flip, Redis, monitoring, CI, gateway, load test.

## Status update (2026-10-01)

- **Code complete:** student self/campus/batch isolation, educator RBAC modes, durable authorization
  audit dimensions, PII-safe OTel telemetry, FastMCP 3.4.5 security upgrade, CI security gates,
  signed identity-free access notifications, and report capability-link rate limiting/auditing.
- **Awaiting ops/decision:** paid-plan verification, stable production keys, Redis/gateway choice,
  migration/capture/retention activation, real grant seeding and ENFORCE cutover, New Relic alert
  creation, live privacy notice, prompt correlation, restore drill, and cohort load test.
