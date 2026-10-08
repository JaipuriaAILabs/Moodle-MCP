# Jaipuria Moodle Reports MCP

A **Jaipuria Google-account-accessible Model Context Protocol (MCP) server** that makes the Jaipuria
`student-report-system` data queryable in plain language. Connect it to any MCP host (a dashboard,
Codex, ChatGPT, Claude.ai, Claude CLI) and ask about student marks, attendance, subjects, cohort
analytics, longitudinal trends, at-risk students, and report accuracy — every ingested student.
Students are restricted to their own enrolment record. Staff access is whitelist-only: eight named
users have cross-campus data access and the four campus deans are restricted to their own campus.

**Endpoint:** `https://moodle-mcp.rehearsal-os.app/mcp` · **Health:** `/health` · **Source tools:** 30
**Repo:** `github.com/mansigambhir-1313/Moodle-MCP` · **Owner:** Jaipuria AI Labs

---

## Overview

The pipeline in [`moodle-agent`](../moodle-agent) ingests Moodle data, computes analytics, and
generates validated student reports into a Supabase project. This MCP is the **read side** of that
project for authorized Jaipuria account holders: it exposes the raw data and the pipeline's outputs
as 30 identity, access, query, status and report-generation tools that a host LLM routes on.

It is **data-first** — the primary surface is the raw gradebook and attendance (queryable for
*every* student, report or not); the generated reports and their two-scheme accuracy scores are a
secondary layer. Data access is read-only; `create_report` is the sole action and delegates report
generation to the authenticated agent service. This MCP never ingests or emails.

Design lineage: the [Rehearsal MCP](https://github.com/JaipuriaAILabs/rehearsal-mcp) patterns
(bounded caches, routing-contract docstrings, response budgets, secret stripping, graceful
degradation), adapted from that server's per-student RLS model to student self-access plus explicit
campus and cross-campus staff grants.

### Where it fits

- **Upstream:** the shared `student-report-system` Supabase project (tables `students`, `courses`,
  `enrolments`, `marks`, `attendance_sessions`, `student_reports`, `report_accuracy`), written by
  `moodle-agent`.
- **Downstream:** any MCP host — Codex, ChatGPT/Claude connectors, a faculty dashboard, or CLI.

---

## What makes it exclusive

- **Longitudinal, not just snapshot** — one run holds every trimester (T1–T6). Tools like
  `student_trajectory` and `declining_students` catch a student sliding term-over-term, which a
  point-in-time query never shows.
- **Single-pane views** — `student_360` and `cohort_pulse` return a whole student / whole cohort in
  one call, ready for a dashboard drawer or landing screen.
- **Accuracy as first-class data** — every generated report carries a two-scheme validation score
  (faithfulness panel + two-turn LLM judge). Ask *"which reports are flagged and why?"*
- **Teaching & curriculum signals** — `section_compare` (A-vs-B fairness), `assessment_breakdown`
  (quiz vs assignment vs project), `subject_difficulty` (curriculum pressure points).

---

## Tools (30)

Every data tool is `SELECT`-only, campus-scoped to the caller, bounded, and carries a
`WHAT / USE WHEN / DO NOT USE / RETURNS` routing docstring. `create_report` is separately marked
as a non-destructive write action.

### Students — raw data (primary)
| Tool | What it returns |
|---|---|
| `list_students` | Roster for a campus/batch (± section), every ingested student |
| `get_student` | One student's complete record — per-subject component marks + attendance |
| `student_marks` | Flat, component-level gradebook rows for a student |
| `student_attendance` | Per-subject attendance (present / sessions / %) for a student |

### Subjects — raw data (primary)
| Tool | What it returns |
|---|---|
| `list_subjects` | Subjects/courses for a scope, with trimester, sections, enrolment |
| `subject_performance` | A subject's cohort marks, pass rate, attendance, per-component means |
| `section_compare` | Section-vs-section means + spread (teaching/marking signal) |
| `assessment_breakdown` | Cohort performance by assessment kind (quiz/assignment/project…) |
| `subject_difficulty` | Subjects ranked hardest-first (pass rate + zeros) |

### Insights — longitudinal & single-pane (hero)
| Tool | What it returns |
|---|---|
| `student_trajectory` | A student's marks/attendance trend across trimesters + label |
| `student_360` | One-call student view: percentile rank, trend, risk flags, accuracy |
| `cohort_pulse` | One-call cohort KPIs: marks, attendance, pass rate, at-risk, distribution |
| `watchlist` | Auto intervention list — reasons + suggested action, ranked |
| `declining_students` | Cohort-wide biggest term-over-term mark drops (early warning) |
| `campus_performance_report` | Cross-cohort campus KPIs, risks, and teaching signals |

### Analytics & at-risk (primary)
| Tool | What it returns |
|---|---|
| `marks_overview` | Cohort marks snapshot — mean, pass rate, distribution, zeros |
| `attendance_overview` | Cohort attendance — mean, counts below 75% / 65% |
| `top_performers` | Highest overall marks in a scope |
| `cohort_compare` | Campus-vs-campus means for a batch |
| `at_risk_students` | Composite risk ranking (zeros + attendance + failing marks) |
| `attendance_watch` | Students below an attendance threshold |
| `zero_alerts` | Students with a recorded zero (most urgent) |

### Reports (secondary)
| Tool | What it returns |
|---|---|
| `get_student_report` | The generated narrative report for a student |
| `report_data_availability` | Whether enough source data exists to generate a report |
| `get_report_job` | Status/result for a durable report job created by `create_report` |
| `whoami` | The caller's principal and allowed campuses |

### Action (write)
| Tool | What it does |
|---|---|
| `create_report` | Queues one non-destructive report job; returns a request id for `get_report_job` |

### Identity and access
| Tool | What it does |
|---|---|
| `resolve_identities` | Resolves names/IDs inside the caller's existing data scope |
| `request_access` | Files a request only when self-service access is enabled (off in production) |
| `list_access_requests` | Lists the access queue for registry administrators only |

The production TrueFoundry Gateway hides both access-administration tools and exposes separate
student and authorized-staff Virtual MCP Servers. See
[`docs/TRUEFOUNDRY_MCP_GATEWAY.md`](docs/TRUEFOUNDRY_MCP_GATEWAY.md).
The AIA-1012 architecture decision and capability-by-capability disposition are documented in
[`docs/AIA_1012_TRUEFOUNDRY_ARCHITECTURE.md`](docs/AIA_1012_TRUEFOUNDRY_ARCHITECTURE.md).

See [`docs/INNOVATION_ROADMAP.md`](docs/INNOVATION_ROADMAP.md) for Phase-3 ideas
(`attendance_eligibility`, `attendance_marks_link`, `anomalies`, `roster_health`).

---

## Quickstart

### Connect Codex (deployed server)

This repository contains a managed Codex plugin bundle under `plugins/moodle-mcp`:
`.codex-plugin/plugin.json` supplies presentation metadata and `.mcp.json` points at the production
server. The checked-in `.agents/plugins/marketplace.json` exposes that bundle for a workspace admin
to import from GitHub. Install `moodle-mcp`, start a new Codex desktop task, and complete the Google
OAuth prompt. The server uses dynamic client registration and PKCE, so no static bearer token
belongs in the plugin.

Because the plugin declares `.mcp.json`, workspace distribution is desktop-only even though the
server is remote HTTPS. See [`docs/TEAM_ROLLOUT.md`](docs/TEAM_ROLLOUT.md) for the admin import,
pilot, release-gate, and rollback steps.

See [`docs/CODEX_GAP_ANALYSIS.md`](docs/CODEX_GAP_ANALYSIS.md) for the compatibility and security
review.

### Connect a static-token host (legacy/non-OAuth deployment)
```bash
claude mcp add moodle --transport http https://moodle-mcp.rehearsal-os.app/mcp \
  --header "Authorization: Bearer <your MCP_TOKENS value>"
```
Then ask, in plain language:
> "cohort pulse for jaipur 2024-26" · "who's declining" · "build my watchlist" ·
> "show JJ24PG001's full record" · "hardest subjects" · "compare sections of Wealth Management"

### Run locally
```bash
cd moodle-mcp
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # fill in the vars below
uvicorn server:app --port 8899
curl localhost:8899/health      # {"status":"ok",...}
```

### Smoke test (real MCP handshake + live queries)
```bash
MCP_URL="http://localhost:8899/mcp" MCP_TOKEN="<a token>" python test_client.py
```

---

## Configuration

`config.py` (pydantic-settings, reads `.env` + env vars). `validate_config()` is a fail-closed
boot check on the Supabase vars.

| Variable | Description | Where to get it |
|---|---|---|
| `SUPABASE_URL` | Report project URL (`https://sadbfvfcmmxgtatfjfmc.supabase.co`) | Supabase → Settings → API |
| `SUPABASE_DATA_KEY` | Custom `reporting_readonly` JWT; legacy service-role fallback is temporary | Supabase signing key + SQL role |
| `SUPABASE_OAUTH_STORAGE_KEY` | Custom JWT restricted to encrypted `mcp_oauth_kv` CRUD | Supabase signing key + SQL role |
| `SUPABASE_AUDIT_KEY` | Custom JWT restricted to the audit RPC | Supabase signing key + SQL role |
| `SUPABASE_ANON_KEY` | Gateway key used with the three custom-role JWTs | Supabase → Settings → API |
| `MCP_TOKENS` | JSON map of faculty tokens → `{name, campuses}` (see below) | You generate it |
| `MCP_ADMIN_TOKEN` | Single all-campus break-glass token (alternative to `MCP_TOKENS`) | You generate it |
| `MCP_REQUIRE_GOOGLE_OAUTH` | Production fail-closed guard: require both Google OAuth credentials and never downgrade to static auth | `true` in production |
| `REPORT_PUBLIC_BASE_URL` | Base for report links (default `https://reports.tryrehearsal.ai`) | — |
| `MCP_SERVER_BASE_URL` | Public URL of this service (optional) | Render dashboard |
| `MCP_RATE_LIMIT` | Tool calls allowed per token per window (default `90`) | — |
| `MCP_RATE_WINDOW_SECONDS` | Rate-limit window in seconds (default `60`) | — |
| `MCP_RBAC_MODE` | Defaults to `enforce`; `public.mcp_faculty` is the authoritative whitelist | — |
| `MCP_STUDENT_SELF_ACCESS` | Enables the self-only student principal and strict tool allowlist (code default `false`; production manifest `true`) | — |
| `MCP_ACCESS_REQUEST_WEBHOOK_URL` | Optional HTTPS approver relay; receives role/campus only | Approved relay |
| `MCP_ACCESS_REQUEST_WEBHOOK_SECRET` | 32+ character HMAC key paired with the webhook URL | You generate it |

Malformed `MCP_TOKENS` (bad JSON or wrong shape) **fails the boot loudly** rather than silently
locking everyone out; short tokens log a warning. **All logging goes to stderr; audit lines record
who / which tool / campus scope / outcome and never contain token contents, student ids, or PII.**

---

## Access model

Google email verification is required and lookalike domains are rejected. Production runs in
`enforce` mode: educators receive only their active registry role/campuses and unlisted users are
denied. Student-roster accounts are never granted cohort-wide access in any mode: they are denied
unless `MCP_STUDENT_SELF_ACCESS=true`, then they can call only reviewed self-service tools and every
student-bearing query is forced to their own student id, campus, and batch. The same scope is carried
to PostgREST in `X-MCP-Scope` for fail-closed RLS defense-in-depth. An explicit educator grant wins
for a legitimate dual-role TA.
`off` and `shadow` retain historical all-campus educator access only; `shadow` also records the
would-be enforced decision for cutover analysis. See `docs/ACCESS_AND_PII_MODEL.md`.

Generate a per-campus token block:
```bash
python3 -c "import secrets; print('mcp_'+secrets.token_urlsafe(24))"   # one per faculty
```
```jsonc
// MCP_TOKENS (single-line JSON in the env var)
{
  "mcp_...indore": {"name": "Indore TNP",       "campuses": ["indore"]},
  "mcp_...office": {"name": "Programme Office",  "campuses": null}      // null = all campuses
}
```
The Supabase credential stays server-side and is never handed to the host. `create_report` does
not write through that credential; it calls the separately authenticated report service.

---

## Architecture

```
MCP host (Codex / ChatGPT / Claude / dashboard)
        │  MCP over HTTP + Bearer <faculty token>
        ▼
server.py (FastMCP /mcp, /health)
  get_authenticated_service()  → verify token → MoodleService(allowed_campuses)
        │
  tools/* (7 modules, 27 tools) — each: Params model + _impl(svc,…) + register()
        │  every query .in_("campus", allowed) ; strip_secrets ; response budgets
        ▼
Supabase (read service role) — students · courses · enrolments · marks ·
                               attendance_sessions · student_reports · report_accuracy
```

Full design: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

### Key files
| Path | Purpose |
|---|---|
| `server.py` | FastMCP app, `whoami`, `/health`, auth dependency, tool wiring |
| `config.py` | Settings + `validate_config()` |
| `supabase_client.py` | Read-only `MoodleService`, campus scoping, run resolution |
| `tools/common.py` | Shared helpers: `courses_for`, `marks_for`, `cohort_rollup`, caches |
| `tools/students.py` · `subjects.py` · `insights.py` | Primary data tools |
| `tools/analytics.py` · `at_risk.py` | Cohort rollups |
| `tools/reports.py` · `actions.py` | Cached report reads + on-demand generation |
| `cache.py` · `guardrails.py` · `annotations.py` | TTL cache, budgets/scoping, tool hints |
| `test_client.py` | End-to-end MCP client smoke test |

### Caches (OOM-safe — bounded `TTLCache` only)
`_run_cache` (latest final run per scope), `_rollup_cache` / `_marks_cache` (cohort raw-data
rollups). Cohort tools page past PostgREST's 1000-row cap and cache the result for 5 min.

---

## Deployment

- **Render** (`render.yaml` blueprint or Docker): paid always-on web instances, shared Redis,
  `uvicorn server:app`, health check `/health`. Configure the split Supabase credentials, OAuth,
  audit HMAC, and signed report-queue secret described in
  [`docs/SECURITY_SCALABILITY_RELEASE.md`](docs/SECURITY_SCALABILITY_RELEASE.md).
- **Docker:** `docker build -t moodle-mcp . && docker run -p 8000:8000 --env-file .env moodle-mcp`
- The production blueprint uses a **Standard** instance to remove free-tier cold starts and permit
  horizontal scaling after load-test evidence supports it.

| Environment | URL | Notes |
|---|---|---|
| Production | `https://moodle-mcp.rehearsal-os.app` | Cloudflare deployment; `main` auto-deploys |
| Local | `http://localhost:8899` | `uvicorn server:app --port 8899` |

Full test/deploy steps: [`DEPLOY.md`](DEPLOY.md).

---

## Runbooks

**Rotate access tokens** — regenerate `MCP_TOKENS` (same generator), update the Render env var; the
service restarts and old tokens stop working. Re-issue the new tokens to faculty.

**Add a per-campus faculty** — add one `"mcp_...": {"name": "...", "campuses": ["<campus>"]}` entry
to `MCP_TOKENS`, redeploy, hand them their token.

**Add a new tool** — follow `docs/ARCHITECTURE.md` §11: add a `Params` model + `_impl(svc,…)` +
`register()`, campus-scope every query, `strip_secrets`, write the routing docstring, register in
`server.py`. Reuse the raw-data helpers in `tools/common.py`.

**Unexpected cold start / first request slow** — verify the live service actually uses the paid
blueprint plan and that the health check is passing; `/health` is safe for an uptime probe.

**Verify a deploy** — `curl <url>/health`, then
`MCP_URL="<url>/mcp" MCP_TOKEN="<token>" python test_client.py`.

---

## Security & safety

Two enforcement layers wrap every tool centrally (`security.py`), so a new tool can't
accidentally run unauthenticated or leak a stack trace:

- **`TransportGuard` (ASGI):** every `/mcp` request is, before JSON-RPC: **body-capped**
  (`MCP_MAX_BODY_BYTES` → 413), **per-IP rate-limited** (`MCP_IP_RATE_LIMIT` → 429, fail-open) to
  blunt unauthenticated floods / token guessing, and **auth-gated** — a valid bearer or real
  **HTTP 401 + `WWW-Authenticate`**, blocking tool enumeration. `/health` (now just `{"status":"ok"}`,
  no version fingerprint) stays open; CORS preflight passes.
- **`GuardMiddleware` (FastMCP `on_call_tool`):** per-call **rate limit** keyed by the **token**
  (hashed, so two tokens sharing a name don't share a budget), **audit log** (who/tool/scope/outcome,
  no secrets/PII), and a **catch-all error boundary** — combined with `mask_error_details=True`, any
  unexpected exception returns a generic message while full detail is logged server-side only (no
  Supabase URL / schema / key leak).

Token resolution is **cached and constant-time** (`hmac.compare_digest`). Tokens support an optional
per-token **`expires`** (ISO date/datetime) so a grant can be **revoked by date without a redeploy**.
Boot is **fail-closed**: malformed token config, a token `<24` chars (unless `ALLOW_WEAK_TOKENS`), or
a bad `expires` format all crash the process loudly.

### Least-privilege DB roles
The MCP reads with the key in `SUPABASE_DATA_KEY`. Use a **SELECT-only** credential instead of
the full `service_role` key (which bypasses RLS and can write): apply
[`sql/2026-08-26_reporting_readonly_role.sql`](sql/2026-08-26_reporting_readonly_role.sql), then mint
a JWT with `{"role":"reporting_readonly"}` signed with the project JWT secret and set it as the key.
PostgREST then runs every query as a role that **physically cannot write**. The server logs a warning
at boot whenever it detects a full `service_role` key still in use. OAuth state and audit delivery
use separate `mcp_oauth_writer` and `mcp_audit_writer` credentials.

**Data invariants:** read-only data queries · one explicitly annotated report-generation action ·
campus-scope every query · uniform `{"found": false}`
misses (no existence oracle) · explicit field projections + secret stripping (run ids / storage
keys / hashes / emails never leave the server) · service-role key server-side only · response
budgets + paging · bounded caches only (OOM-safe). Detail in `docs/ARCHITECTURE.md` §3, §11.

Verified in testing: tokenless → 401; bad token → 401; a `jaipur`-scoped token cannot read `noida`
cohort data (`available:false`) and a cross-campus student is a uniform miss; the rate limiter
blocks past the window; an internal exception is masked from the client but logged server-side.
