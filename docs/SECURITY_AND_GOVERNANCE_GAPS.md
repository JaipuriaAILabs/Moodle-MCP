# Moodle MCP — Security & MCP-Governance Gaps + Production-Readiness

Consolidated from a 4-dimension code audit (auth/secrets, injection/deps, MCP governance/abuse,
audit-log/PII/ops) + live DB verification, mapped to **OWASP LLM Top 10 (2026)**. Target: ~5,000
Jaipuria users over student PII, DPDP (India) applies. Reviewed at `main` (deployed).

## Verdict — Conditionally GO

The MCP is **architecturally production-grade**. The central security property holds: **authority
lives in deterministic code, not the model/prompt** — the server executes typed, RBAC-scoped tool
calls, so a prompt-injected or "fooled" model (or client) can never exceed the authenticated user's
own access. **No unconditional P0 code vulnerability was found.** The launch-gating items are
**governance decisions and ops config**, not code defects.

**NO-GO until:** (G1) the DPDP privacy notice is published, and (G2) the access posture is decided
(enforce RBAC, or a signed risk-acceptance of all-campus access). **Strongly recommended before
ramp:** (G4) observability on, (G5) Redis before >1 instance.

## What is already solid (verified — do not regress)

- **AuthN/identity:** GoogleProvider OAuthProxy binds identity by doing the Google code-exchange with
  our own client secret → claims are trustworthy; constant-time token compare; MultiFernet key
  rotation without orphaning; `mask_error_details=True`; tokeninfo URLs (live tokens) kept out of
  logs. The FastMCP-3.4.5 claims regression + audience key are fixed and test-pinned this session.
- **AuthZ / isolation:** per-user OAuth identity → RBAC; `apply_student`/`apply_campus` bound **every**
  query (authorize-before-retrieval); students hard-scoped to their own enrolment id(s) (7/0 isolation
  tests); `create_report` forces self; `get_report_job` re-checks self (fixed this session).
- **Injection:** no raw SQL / `eval` / `exec` / `subprocess`; PostgREST builder everywhere;
  `find_student` or-filter sanitised; agent-API URL = charset-validated + DB-resolved id + `quote()`;
  `report_url` is an exact-route https allowlist; pydantic bounds on every tool param.
- **Audit / PII / DPDP controls:** fail-**closed** audit (`MCP_REQUIRE_AUDIT=true`, no tool runs
  without a durable attempt record); HMAC pseudonymisation; **RLS on `mcp_audit` verified live**;
  **180-day retention cron verified live** (`cron.job` daily 03:00 UTC); **no student PII reaches New
  Relic** (verified — tool/outcome/scope-class/role only); capture flags default privacy-safe.
- **MCP governance:** clean read-only vs generate annotation split, nothing mis-annotated, dead
  `_search_impl` unregistered; `create_report` is the only LLM-cost tool, gated by capability +
  per-principal 60/hr budget + self-forcing + campus deny; HMAC-signed internal agent calls; HostGuard,
  body caps, security headers, no permissive CORS, OOM-bounded limiters.
- **Secrets:** least-privilege `SUPABASE_*` keys (reporting_readonly / oauth_writer / audit_writer /
  anon), no committed secrets (`*.local` gitignored), CI runs pip-audit + bandit on every push.

## Gaps (prioritized, OWASP-mapped)

### P0 — launch-gating (governance / compliance, not code)
| # | Gap | OWASP | Status / owner |
|---|---|---|---|
| **G1** | **DPDP privacy notice unpublished while identity+args+source-IP capture is LIVE** (`render.yaml` MCP_CAPTURE_* = true → cleartext who-viewed-whom + PII in the ledger). `docs/PRIVACY_NOTICE.md` is DRAFT, DPO contact TBD. | LLM02 | **OPEN — user/DPO (AIA-1392).** Publish the notice + DPO contact before go-live, **or** lower capture (identity-only/off) until it's published. |
| **G2** | **Access posture: `MCP_RBAC_MODE=off` → every `jaipuria.ac.in` staff account reads all ~5k students across all campuses + can generate reports.** This is the user-accepted "G1 all-access" interim; enforce is built and in shadow, blocked on the real faculty roster. Sub-gap: a not-yet-rostered student inherits all-campus (roster latency becomes a security control). | LLM03 / ASI03 | **OPEN — decision + roster.** Load faculty roster → shadow-confirm → `MCP_RBAC_MODE=enforce` (default-deny/pending), **or** sign a risk-acceptance of all-campus access. Runbook: `docs/RBAC_ENFORCE_CUTOVER.md`. |

### P1
| # | Gap | OWASP | Status / owner |
|---|---|---|---|
| **G3** | Dependency hygiene — `cryptography` was unpinned (first-party import), `starlette` floor <0.40 (CVE-2024-47874), no lockfile. | LLM04 | **FIXED (pins)** this change; **OPEN:** commit a lockfile (uv/pip-tools) + deploy from it — eng. |
| **G4** | Observability may be inert — `NEW_RELIC_LICENSE_KEY` unset → no spans/metrics/alerts. (`/health` uptime cron exists live.) | — | **OPEN — ops.** Set the EU ingest key, verify spans, add the NRQL alert conditions (`monitoring/README.md`). |
| **G5** | Rate/cost limits are **per-instance** without `MCP_REDIS_URL`; scaling `plan: standard` >1 instance multiplies per-IP + per-principal + the `create_report` **LLM-spend** budget. | LLM06 | **OPEN — ops.** Set `MCP_REDIS_URL` before `numInstances>1`, or pin a single instance + document. |
| **G6** | PII tokeniser HMAC key could fall back to the audit key (enumerable `student_id` → brute-forceable `student_ref`; coupled rotation). | LLM02 | **FIXED** this change — boot now requires a dedicated `MCP_PII_HMAC_KEY` when `MCP_PII_TOKENIZE=true`. (Feature dormant.) |

### P2 — hardening (reported; not blind-applied to live auth code without verifying prod env)
| # | Gap | OWASP | Note |
|---|---|---|---|
| G7 | Middleware "per-principal" limiter keys on the **token**, not identity → multiple sessions = multiple 90/min budgets. | LLM06 | Key on `principal_subject`. Small, test carefully. |
| G8 | Cohort tools load up to 100k rows into memory to aggregate → concurrent-call **memory-DoS** on a small instance. | LLM06 | Push aggregation into a Postgres RPC/rollup, or count-guard + concurrency cap. Bigger refactor. |
| G9 | `MCP_TRUST_PROXY_HEADERS` and `MCP_REQUIRE_AUDIT` both **default false**; env drift silently weakens IP attribution / reverts audit to fail-open. | — | Add boot-time assertions (prod must have both true). |
| G10 | `_source_ip` always takes rightmost XFF regardless of the trust-proxy flag. | — | Gate on `trust_proxy_headers`. Harmless under Render's single proxy. |
| G11 | Faculty/student email lookups use `.ilike` (treats `%`/`_` as wildcards). | — | Exact-match (`.eq` lower-cased). Near-zero risk (OAuth-verified emails). |
| G12 | `report_rows`/`one_report` select `*` (pulls `student_email`); output rebuilt from a whitelist so no current leak. | LLM02 | Explicit columns and/or `strip_secrets` on report outputs (defense-in-depth). |
| G13 | Agent `narrative`/`subject_table` passed through unbounded. | LLM10/06 | Low (single-student). Add a byte cap. |
| G14 | Crypto-key entropy not enforced. | — | **PARTIAL FIX** — boot now **warns** on <32-char keys (not fail, to avoid bricking a running service). |
| G15 | Audience guard is defense-in-depth behind the proxy's code-exchange. | LLM08 | Mostly covered — re-synced to flat `aud` + `test_oauth_claims` this session; add a lib-shape regression assert. |
| G16 | `create_report` `idempotentHint:true` is inaccurate for `refresh`/random-select paths. | — | Set `idempotentHint:false` or document (hosts may over-optimize). |
| G17 | `resolve_identities` registered while PII dormant (campus/self-scoped → harmless). | — | Optionally gate registration on `pii.enabled()`. |
| G18 | Audit partitions end 2027-07-01 (default partition catches overflow). | — | Add a partition-maintenance cron. |
| G19 | `mcp_audit` **partition** tables have RLS disabled (base tables RLS on; schema GRANT revoked from anon/authenticated → partitions unreachable by app roles). | — | Enable RLS on partitions (defense-in-depth) or rely on the revoke. Low risk. |
| G20 | `request_access` (~90/min) each fires an approver notification; DB de-dupes the row, not the notifications. | — | De-dupe/rate the notification. Dormant until SMTP set. |
| G21 | Live cron schedule (retention + health) exists in prod but not committed to repo SQL. | — | Commit the schedule SQL for reproducibility. |

## OWASP LLM Top 10 (2026) coverage
| ID | Risk | Status |
|---|---|---|
| LLM01 | Prompt Injection | **Contained** — server runs typed, RBAC-scoped tool calls, not model instructions; harness-side injection is the client's (jaipuria-os) concern. |
| LLM02 | Sensitive Info Disclosure | **Finding G1** (notice) + G6/G12; strong controls (no PII→NR, audit RLS, pseudonymisation, authorize-before-retrieval). |
| LLM03 | Excessive Agency | **Finding G2** (posture); strong (read-only tools, least-priv DB key, gated single write). |
| LLM04 | Supply Chain | **Finding G3** (deps pinned; lockfile pending). |
| LLM05 | Data/Model Poisoning | **N/A** — no training/fine-tune; roster is DB-sourced. |
| LLM06 | Unbounded Consumption | **Finding G5/G8** (Redis-at-scale, cohort memory). |
| LLM07 | Misinformation | **Pass** — reports are advisory; no model claim drives authz/state. |
| LLM08 | Hidden Context Exposure | **Pass** — no secrets in prompts/tool schemas; authz never in the prompt. |
| LLM09 | Vector/Embedding | **N/A** — no vector store. |
| LLM10 | Improper Output Handling | **Mostly pass** — report HTML rendered in moodle-agent (esc/CSP); MCP returns JSON; `report_url` allowlisted; G13 passthrough. |

Agentic (ASI): ASI03 Identity/Privilege — per-user scoped identity (good); agent-API uses admin creds
but scopes by actor + MCP self-check (G2 note). ASI06 Memory poisoning — N/A.

## Production-readiness checklist
- [ ] **G1** Publish DPDP privacy notice + DPO contact (or reduce capture until then). — **blocker**
- [ ] **G2** Decide access posture: enforce RBAC (needs faculty roster) or sign all-access risk. — **blocker**
- [ ] **G4** Set `NEW_RELIC_LICENSE_KEY`, verify spans, add alert conditions. — pre-ramp
- [ ] **G5** Set `MCP_REDIS_URL` before scaling >1 instance (or pin single + document). — pre-scale
- [ ] **G3** Commit a dependency lockfile; deploy from it. — pre-ramp
- [x] Dependency CVE floors (cryptography, starlette) pinned — this change
- [x] PII-key separation enforced at boot — this change
- [x] Low-entropy key warning at boot — this change
- [x] FastMCP-3.4.5 auth regression fixed + verified live — earlier this session
- [x] Student isolation audited (all tools) + `get_report_job` self-check — earlier this session
- [x] Audit RLS + 180-day retention cron verified enforced in prod — this review
- [ ] P2 hardening (G7–G21) — backlog, non-blocking

## Top 3 fixes, priority order
1. **G1** publish the privacy notice (unblocks DPDP processing already happening).
2. **G2** resolve the access posture (enforce RBAC is the least-privilege answer; it's built and shadow-tested, blocked only on the faculty roster).
3. **G4+G5** turn on observability and set Redis before any horizontal scale.
