# Moodle MCP — gap analysis (what's required/claimed vs what's actually enforced)

Companion to `PRODUCTION_READINESS.md`. That doc plans the work; this one is an honest
audit of **gaps** — where the shipped/enforced reality diverges from the AIA-1012 epic
acceptance criteria, the security model, or the "record everything for 5,000 users"
goal. Grounded in the code as of `2e0482c` (2026-09-21). Ordered by severity.

Legend: **REQ** = requirement/expectation · **NOW** = shipped/enforced today · **GAP**.

---

## 🔵 ACCEPTED RISK (data-owner decision — 2026-09-21)

### G1. No role-based access — every Jaipuria ID (incl. ~2,800 students) has all-campus, faculty-level read + report generation
- **REQ** (AIA-1012/1013/1016 as originally written): "faculty/student/admin tool permissions, **deny-by-default**, immediate revocation, prove **allowed AND denied** journeys, per-tool RBAC through the gateway."
- **NOW**: `security.principal_from_claims` short-circuits any `jaipuria.ac.in` (+ subdomain) account to `campuses=None` (**all campuses**) *before* the student-roster deny — so the deny applies only to non-Jaipuria domains. Verified: students (who are `@jaipuria.ac.in`) get the same all-campus grant as faculty. `whoami` exposes no role; there is no faculty/student/admin distinction in the MCP.
- **Exposure**: any of ~2,800 students can read any other student's marks/attendance across all 5 campuses, and can `create_report` for anyone.
- **DECISION (2026-09-21):** the data owner has **accepted this risk** and chosen to keep all-Jaipuria all-access for now (no in-MCP student self-scope, no gateway gating on this MCP). This is a conscious, informed choice made after the exposure was flagged. Consequences recorded so the epic and the 5 Oct review assess the **actual** model, not the original RBAC intent:
  - The Moodle MCP's per-user RBAC / deny-by-default / "denied journeys" acceptance in AIA-1012 is **rescoped**: this MCP intentionally grants uniform all-campus read + `create_report` to every verified Jaipuria account. The only enforced boundaries are the **domain gate** (verified `jaipuria.ac.in`) and **read-only-ness** of all tools except `create_report`.
  - Per-tool RBAC (AIA-1013/1016 gateway) remains a **platform-level** capability for other/future MCPs; it is not gating this MCP by decision.
  - Vaibhav's adversarial review should validate the domain gate, the read-only surface, `create_report` scoping to in-grant targets, and audit attributability — **not** student-vs-faculty denial, which is intentionally absent here.
  - Residual controls that still matter under this model: audit every access (G2), publish a privacy notice + retention (G3), and cost-cap `create_report` (G4).

---

## 🟠 HIGH

### G2. Recording — backend now LIVE (2026-09-21); awaiting the audit key + flags
- **REQ**: user/email-attributable traces of every MCP call, retained with defined access.
- **DONE**: the `mcp_audit` backend is **now applied to prod** (`mcp_audit.tool_calls` + `record_mcp_tool_call` RPC + `mcp_audit_writer` role + `v_activity` view — verified). Capture code + connection events (`on_initialize`) are on `main`.
- **REMAINING (ops, 2 env steps)**: set `SUPABASE_AUDIT_KEY` (mint via `scripts/mint_audit_jwt.py`) + `MCP_AUDIT_HMAC_KEY`, and the measured `MCP_CAPTURE_*` flags (identity/arguments/client_ip = true, results = false) + `MCP_TRUST_PROXY_HEADERS`. The DB is ready; nothing persists until the key is set.

### G3. Governance — retention SCHEDULED + notice drafted (2026-09-21)
- **REQ**: lawful, governed capture with defined retention + a notice.
- **DONE**: 180-day retention is now **auto-enforced** — `pg_cron` job `mcp-security-retention-purge` runs `purge_expired_mcp_security_data()` daily at 03:00 UTC (verified active). Privacy notice **drafted**: `docs/PRIVACY_NOTICE.md` (measured level — identity + action + IP + connections, no result payloads).
- **REMAINING**: publish the notice (institution/legal); the `mcp_audit.consents` table is present but a consent *flow* is still unwired (optional under the accepted model).

### G4. Hosting + scale not launch-ready
- **NOW**: live Render service still on the **free plan** (spin-down) despite `render.yaml: standard`; `MCP_REDIS_URL` unset (in-process rate limiter diverges across instances); Google `tokeninfo` called per request (now traced via `mcp.auth`, but **not cached/mitigated**); no load test.
- **PARTIALLY CLOSED (2026-09-21):** `create_report` now has a **per-principal budget** (`_enforce_report_budget`, default 60/user/hour via `MCP_CREATE_REPORT_LIMIT`/`_WINDOW_SECONDS`, Redis-backed when configured) — bounds worst-case LLM spend/load under the open-access model. Verified by `tests/test_report_budget.py`.
- **GAP (remaining)**: cannot serve 5,000 concurrently (free plan); horizontal scaling still needs `MCP_REDIS_URL` for a shared limiter (incl. the new report budget); auth latency uncached; no load test.

### G5. Report links — VERIFIED acceptable (public capability links by design), one TTL knob
- **REQ** (AIA-1355): "another user cannot retrieve an artifact by guessing or replaying its URL."
- **INVESTIGATED (2026-09-21, `moodle-agent` `app/web/{tokens,shortlinks}.py` + `app/api.py`):** report links are intentionally **public — the token IS the credential** (students have no login). This is not a gap; the guess/replay requirement is met by construction:
  - **Unforgeable / non-enumerable**: `/r`·`/rv` are **Fernet** tokens (AES-128-CBC + HMAC-SHA256; a tampered token fails to decrypt); `/s` is a **192-bit** random id stored only as a SHA-256 digest. You cannot craft or increment another student's link.
  - **Per-student scoped**: each token/id resolves to exactly one `{campus,batch,trimester,student_id}` — no IDOR pivot.
  - **Expiring + revocable**: Fernet TTL + per-row `expires_at`/`revoked_at`; rotating `REPORT_LINK_SECRET` revokes every outstanding `/r`·`/rv` link at once.
  - **No oracle / hardened page**: every invalid/expired/tampered link → generic 404; per-IP rate limit on all three routes; report page served `Cache-Control: private, no-store`, `X-Frame-Options: DENY`, no-script CSP, no PII in the URL, host-guarded.
- **Residual (not a bug, worth tuning):** (a) **TTL is 90 days** (`REPORT_LINK_TTL_DAYS`) — long for student PII; consider 7–30 days so a forwarded link lapses sooner. (b) It remains **bearer-of-link** (anyone with the URL sees that one student's report until expiry) — inherent to a login-less share link; revocation exists. (c) Confirm **`REPORT_LINK_SECRET` is set + stable** in prod (unset fails closed; rotating it invalidates all live links — same discipline as the OAuth keys).

---

## 🟡 MEDIUM

### G6. Test coverage is unit/mock-only — no integration, RLS, or load tests
- **NOW**: 15 plain-assert files, all mocked. The campus-scoping AND-filter, `mcp_audit` RLS (admin-only reads), the SECURITY DEFINER RPC, and the OAuth/DCR flow are verified only by **reasoning against mocks**, never against a real (test) Supabase/PostgREST or a running server.
- **PARTIALLY CLOSED (2026-09-22):** `mcp_audit` lockdown is now **proven against live prod**, not just reasoned — `anon`/`authenticated` have **no schema USAGE** (can't reach any audit table), RLS is on for every base table, and even `mcp_audit_writer` has **no SELECT** (write-only via the SECURITY DEFINER RPC; only admin/`service_role` can read). Repeatable proof: `sql/verify_mcp_audit_lockdown.sql`.
- **GAP (remaining)**: no end-to-end auth test against a running server; no gateway-routing test (gateway undeployed); no concurrency/load test; partition tables have RLS off (harmless — parent RLS + no grants + no schema usage cover them; optional defense-in-depth noted in the verify script). CI still only exercises the unit suite.

### G7. Client-harness integration incomplete
- **GAP**: (a) the `X-Request-Id` correlation header is a documented, **uncommitted draft** in the harness — prompts aren't yet joinable to tool calls (Rajika). (b) `OAUTH_ALLOW_CROSS_CLIENT_PKCE` defaults off → the Claude.ai connector's cross-node token exchange can fail (likely re-auth-pain contributor). (c) `jaipuriaschools.ac.in` is admitted by the harness as USER but **denied by the MCP** (not a subdomain of `jaipuria.ac.in`) — graceful-denial UX unverified; decision pending.

### G8. "Record everything" is only half — prompts aren't captured MCP-side
- **NOW**: the MCP records tool calls + args + results. The user's **prompts/questions live only in the client harness**.
- **GAP**: without the G7(a) correlation, "everything they did" can't be reconstructed end-to-end; oversized (>256 KB) result payloads are truncated (no S3 offload built).

### G9. Eval not trustworthy yet (AIA-1380)
- **NOW**: reports are accurate; the Scheme-1 faithfulness panel produces false flags; fixes are in the working tree, **uncommitted** (per data-owner direction).
- **GAP**: `overall_pct` is an unvalidated triage signal until the panel is fixed + calibrated against human labels; no fleet-wide accuracy sweep.

---

## 🟢 LOW / hygiene

- **G10. Config drift**: `render.yaml` is documentation-only for the manually-created live service (env doesn't sync) — repo intent and live env can silently diverge; no reconciliation check.
- **G11. Release gate**: everything has been pushed straight to `main`; CI was just added and hasn't yet gated a PR. No documented rollback plan.
- **G12. Admin surfaces undeployed**: the admin policy MCP (AIA-1016) and reference integrations (AIA-1014) exist locally but aren't deployed, so policy authorship + revocation can't be demonstrated.

---

## Status of the headline decision
**G1 is decided (2026-09-21): accepted + rescoped** — all-Jaipuria all-access stays; the
RBAC/denied-journey acceptance for *this* MCP is formally rescoped (see G1 above), and the
decision is recorded on epic AIA-1012. The remaining gaps (G2–G12) are executable ops/eng
work already tracked in `PRODUCTION_READINESS.md`. Under the accepted model, the highest-
value residual controls are **audit (G2)**, **privacy notice + retention (G3)**, and a
**`create_report` cost cap (G4)** — because every user can read everything and generate reports.

---

## Fresh security + data-capture pass (2026-09-21)

### 🟠 G13. A client harness that lets every USER add their own MCP servers — data-exfiltration surface
- **Found (historical, re the former LibreChat-based client):** `interface.mcpServers: { use: true, create: true }` synced into the USER role at startup meant **every** USER could register an **arbitrary MCP server** from the UI. Re-check whichever client is adopted next for the same setting.
- **Risk**: a user can point such a client at a **malicious/external MCP** and have their conversation context / tool outputs (incl. any student data pulled via the Moodle MCP) sent to an endpoint they control — an exfiltration/SSRF-shaped surface at 5,000-user scale. Not specific to our MCP, but it widens the blast radius of the open-access Moodle data.
- **Recommendation**: on whichever client is adopted, ensure USER cannot self-register MCP servers (admins only), **or** gate it behind a faculty/admin sub-role. Decision for Rajika/data-owner. *(client config)*

### ✅ G14. Connection capture — FIXED (2026-09-21)
- **Was**: `GuardMiddleware` implemented only `on_call_tool`, so the audit ledger counted tool *invocations*, not *connections* — a connect-but-no-call left no trace (gap vs AIA-1210 "connection counts").
- **Fix**: added `GuardMiddleware.on_initialize` — records a **`connect`** event (with identity + source_ip when capture is on) once per MCP session handshake. Best-effort + post-hoc: a connection is never blocked or failed by the audit write. So the ledger now shows **who connected**, not only who called a tool. Verified by `tests/test_activity_capture_integration.py` (§4 — connect recorded, carries identity/ip, never args/result, survives an audit failure).
- Still surfaces only once recording is turned on (G2).
