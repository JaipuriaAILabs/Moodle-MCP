# TrueFoundry — Implementation Log (Moodle MCP)

**Last updated:** 2026-10-13
**Tenant:** `https://gradelessai.truefoundry.cloud` (`ailabs2@jaipuria.ac.in`, Pro plan)
**Scope of this doc:** a detailed, accurate record of *everything implemented* for the TrueFoundry
integration — code in the repos **and** the live tenant state — with what is NOT yet done clearly marked.

TrueFoundry is used as a **managed ingress + policy + observability layer**. It does **not** replace the
MCP's own authorization: per-user OAuth, campus/student RBAC, Supabase RLS/read-only role, the audit
ledger, and the in-server PII pseudonymizer all remain authoritative. TrueFoundry adds, in four places:

1. **MCP policy gateway** — register the MCP as a source, expose curated student/staff Virtual MCPs.
2. **Model (AI) gateway** — route `moodle-agent` report-generation LLM calls through TrueFoundry.
3. **Guardrails** — PII/secrets/prompt-injection + Cedar RBAC, bound pre/post tool call.
4. **Observability** — Model + MCP + Guardrail metrics on the TrueFoundry dashboard.

Legend: ✅ implemented & verified · 🟡 implemented in code/config, not yet live · ⛔ not started / gated.

---

## A. Code implemented — `moodle-mcp` repo

### A1. Gateway desired-state manifests — `truefoundry/` ✅ (in Git; `tfy apply` pending)
Declarative, secret-free, `tfy apply`-able (JSON-as-YAML). Validated by
`scripts/validate_truefoundry_gateway.py` (passes). Files:

| File | What it implements |
| --- | --- |
| `manifests/00-moodle-remote.yaml` | Source server `jaipuria-moodle-prod` → `https://moodle-mcp.rehearsal-os.app/mcp`. `enable_tools_by_default: false` (deny-by-default), TLS verify on, **OAuth2 Authorization Code** (custom provider; our `/authorize` `/token` `/register`; PKCE **S256**; `jwt_source: access_token`), no end-user collaborators, explicit `enabled_tools` allowlist. |
| `manifests/10-moodle-student-virtual.yaml` | Virtual MCP `jaipuria-moodle-student` — 10 self-service tools, `best_effort_mode: false`, collaborator `team:everyone`. |
| `manifests/20-moodle-staff-virtual.yaml` | Virtual MCP `jaipuria-moodle-staff` — full data toolset (all tools except `request_access`/`list_access_requests`), 12 named staff collaborators. |
| `manifests/30-moodle-user-rate-limit.yaml` | Per-user rate limit on the source; `log_request_body_on_block: false`. |
| `manifests/31-moodle-report-rate-limit.yaml` | Per-user `create_report` limit = 60/hour. |
| `manifests/40-gateway-data-access.yaml` | Trace/metric access: everyone sees own-data + team metrics; all-data traces = tenant-admin only. |
| `manifests/41-moodle-access-guardrail-group.yaml` | Guardrail group `moodle-access` → `cedar-rbac` (Cedar policy embedded; enforce). |
| `manifests/42-moodle-pii-guardrail-group.yaml` | Guardrail group `moodle-pii` → `pii-redaction` (tfy-pii, mutate, enforce). |
| `manifests/43-moodle-security-guardrail-group.yaml` | Guardrail group `moodle-security` → `secrets` (mutate) + `prompt-injection` (validate). |
| `manifests/50-moodle-guardrails.yaml` | Binding policy `moodle-mcp-guardrails`: **pre**-invoke `moodle-access/cedar-rbac`; **post**-invoke `moodle-pii/pii-redaction` + `moodle-security/secrets` + `moodle-security/prompt-injection`, on all three servers. |
| `policies/moodle-access.cedar` | Default-deny Cedar policy: `team:everyone` → student tools on the student server + source; the 12 named staff → full staff toolset. Source of truth for the policy embedded in `41`. |
| `README.md` | Apply/preview instructions + secret-handling rules. |

Supporting code: `scripts/validate_truefoundry_gateway.py` (cross-checks manifests vs the code's tool
inventory, student self-tool list, approved-staff roster, and that the embedded Cedar == the `.cedar`
file); `tests/test_truefoundry_gateway.py`.

### A2. Private-origin boundary — `gateway_trust.py` + `config.py` ✅ (code live; flag OFF in prod)
- `gateway_trust.py` → `GatewayEnforce` ASGI middleware: when `GATEWAY_ENFORCED`, `/mcp` is accepted
  **only** if the request carries header `x-mcp-gateway-secret` matching an accepted secret; otherwise
  `403 gateway_required`. OAuth/discovery paths stay open.
- `config.py`: `gateway_enforced` (`GATEWAY_ENFORCED`, default **false**), `gateway_shared_secret`
  (`GATEWAY_SHARED_SECRET`), `gateway_secrets()` (comma-split for rotation). **Boot validation:**
  `GATEWAY_ENFORCED=true` requires `GATEWAY_SHARED_SECRET` with every value ≥ 32 chars.
- A 64-hex secret is generated locally in `moodle-mcp/.env.gateway.local` (gitignored), ready to install
  in both TFY Secret Manager and Cloudflare. **`GATEWAY_ENFORCED` is currently `false` in prod wrangler.**

### A3. AI-Gateway route in the one-pager generator — `onepager/build_report.py` 🟡 (reference impl)
Report generation can route through the TrueFoundry AI Gateway instead of OpenRouter directly:
- Flag-gated on `TRUEFOUNDRY_API_KEY` + `TRUEFOUNDRY_BASE_URL` (`_truefoundry_enabled()`); can be made
  mandatory with `TRUEFOUNDRY_REQUIRED=1` (`_validate_truefoundry()` fails closed: HTTPS-only base URL,
  requires prompt-injection + secrets guardrails when advanced guardrails are required).
- Sends OpenAI-compatible `POST {BASE}/chat/completions` with `Authorization: Bearer <key>`,
  `X-TFY-GUARDRAILS` (input/output groups), `X-TFY-LOGGING-CONFIG: {"enabled": false}` (no request-body
  logging), identity-free metadata, bounded timeout/retries.
- **Token-then-stitch for names:** a fixed `PSEUDONYM = "Aarav"` is sent to the model; the real first
  name is stitched back after generation (word-bounded) so the LLM never sees a real student name.
- Credentials load from gitignored `.env.truefoundry.local`. This is the reference implementation; the
  production generator is in `moodle-agent` (see section B).

### A4. In-server PII redaction — `pii.py` (the PRIMARY control; TFY is secondary) ✅ (`enforce` in prod)
`pii.redact()` runs at the `GuardMiddleware` chokepoint on every tool result (`MCP_PII_REDACTION_MODE=
enforce`). Deterministic regexes catch identifiers regardless of any LLM/gateway:
`_EMAIL_RE`, `_PHONE_RE` (India mobile), `_ENROLMENT_RE`, `_PAN_RE = [A-Z]{5}\d{4}[A-Z]`, `_AADHAAR_RE`,
plus roster-name tokenisation (`student_ref = "S_"+HMAC`). **This is why the TFY PII guardrail is only a
secondary net** — the MCP masks PII before anything leaves it.

### A5. Faculty/course tools exposed to the gateway — PR #21 🟡 (awaiting merge+deploy)
`tools/faculty.py` adds staff-only, campus-scoped `list_faculty`, `faculty_courses`, `course_faculty`,
`faculty_feedback`. Added to the gateway desired-state (source `enabled_tools`, staff virtual server,
Cedar staff allowlist). Until PR #21 is deployed, the source won't expose these 4 tools, so `tfy apply`
will simply not surface them yet.

### A6. Docs
`docs/TRUEFOUNDRY_MCP_GATEWAY.md`, `docs/AIA_1012_TRUEFOUNDRY_ARCHITECTURE.md`,
`docs/TRUEFOUNDRY_INTEGRATION_PLAN_2026-10-13.md`, `docs/TRUEFOUNDRY_APPLY_RUNBOOK.md`, and this log.

---

## B. Code implemented — `moodle-agent` repo (production report generator)

- `app/config.py` — selects the TrueFoundry base URL/key/model in production; rejects partial/insecure
  config (fails closed without TrueFoundry when required).
- `app/onepager/build.py` — the **production** generator. Same pattern as A3: `POST /chat/completions`
  via the AI Gateway, `X-TFY-GUARDRAILS` (bilateral), `X-TFY-LOGGING-CONFIG {"enabled": false}`,
  identity-free metadata, web search disabled, bounded retries, and **token-then-stitch (`PSEUDONYM
  "Aarav"`)** so the real student name is never sent to the model. (This closed the real DPDP gap — the
  live generator previously sent the real name straight to OpenRouter.)
- `render.yaml` — requires the gateway; deliberately omits any direct provider credential.

Status: implemented in the working tree; goes live when the agent redeploys with `TRUEFOUNDRY_*` set and
a Virtual Account Token (see C2).

---

## C. Tenant state — what is actually live in `gradelessai.truefoundry.cloud` (2026-10-13)

### C1. Model Gateway ✅ (configured live this session)
- **Provider account:** OpenRouter (API key supplied by the user; **should be rotated** — it was exposed).
- **Models enabled:** `openrouter/google-gemini-flash-latest`, `openrouter/deepseek-v4-flash-latest`
  (Test Credentials: 2 passed).
- **Gateway base URL:** `https://gateway.truefoundry.ai` (OpenAI-compatible).
- **Live proof:** 2 real `/chat/completions` calls through the gateway → Metrics shows 2 LLM calls,
  ~$0.003, 0 errors.

### C2. AI Gateway for `moodle-agent` ⛔ (provider ready; VAT + wiring pending)
Still to do (tenant + agent deploy): create a **Virtual Account**, grant it the report model + guardrails,
store its VAT as `TRUEFOUNDRY_API_KEY` in the agent's secret store, set the base URL + model id, add
model-level spend/rate limits, and confirm no direct OpenRouter key remains in the agent.

### C3. Guardrails ✅ (PII group live) / 🟡 (others via `tfy apply`)
- **Live:** group `moodle-pii` → config `pii-redaction` — **PII/PHI (Azure), Mutate, Fail Open**,
  categories **Email · Phone · India PAN · India Aadhaar** (**Person excluded** so the pseudonym survives).
- **Live proof + finding:** a PAN/Aadhaar canary call showed Aadhaar/email/phone **masked** but the
  **synthetic PAN leaked** (TFY India-PAN detector didn't fire). No DPDP breach — the in-server `_PAN_RE`
  catches PAN deterministically — but the TFY net's PAN detection needs raising before it's relied on.
- **Not yet applied:** `moodle-access` (Cedar), `moodle-security` (secrets + prompt-injection), and the
  binding policy `50` — these come via `tfy apply`.

### C4. MCP Gateway ✅/🟡
- **Source `jaipuria-moodle-prod`** ✅ registered (Remote, **OAuth2 Authorization Code**; TrueFoundry
  auto-discovered `/authorize` `/token` `/register` via DCR — no client secret; PKCE S256). Status =
  "Connect" (OAuth consent not yet completed — see gotcha below).
- **Virtual `jaipuria-moodle-student`** ✅ created — 10 self-service tools, Best Effort OFF, Connected.
- **Virtual `jaipuria-moodle-staff`** 🟡 configured in the UI with 28 read-only tools, one click from
  creation. NOTE: UI master-toggles made the exact set hard to pin (it includes the admin-gated
  `list_access_requests` and lacks `create_report`). **`tfy apply` will reconcile both virtual servers to
  the exact manifest tool sets** — the UI build proved the flow but `tfy apply` is authoritative.

### C5. Observability ✅/🟡
- **Live:** Model Metrics + Guardrail Metrics populated (from the gateway calls above). TrueFoundry's
  dashboard is populated by traffic flowing through the gateway — it is NOT an app-side exporter.
- **New Relic removed** from the MCP (PR #22): generic OTLP is now off-by-default; runtime observability
  = Cloudflare Workers analytics; governance record = Supabase `mcp_audit` ledger; tool-traffic analytics
  = this TFY dashboard once enforced.
- **Pending:** MCP Metrics populate only after `GATEWAY_ENFORCED=true` + clients on the virtual URLs.

---

## D. Not yet implemented / gated (the honest remainder)

- ⛔ `tfy apply` of the gateway manifests (virtual MCPs exact sets + Cedar + secrets/prompt-injection
  guardrails + rate limits + bindings). → see `docs/TRUEFOUNDRY_APPLY_RUNBOOK.md`.
- ⛔ `GATEWAY_ENFORCED=true` + `x-mcp-gateway-secret` installed in TFY and Cloudflare `GATEWAY_SHARED_SECRET`.
- ⛔ MCP deployed as a **TrueFoundry Service** (workspace + GitHub App + deploy credential) — currently the
  gateway fronts the Cloudflare origin `moodle-mcp.rehearsal-os.app`.
- ⛔ `moodle-agent` Virtual Account Token wiring (C2).
- ⛔ The `create_report` single-execution **approval rule** (encoded staff tool name, post-apply).
- ⛔ Inbound identity-provider mapping (Jaipuria Google IdP → TFY users/teams) for end users.
- ⛔ PRs **#21** (faculty tools) + **#22** (remove New Relic) merge + deploy.
- 🔎 **Decisions required before staff rollout:** raise/verify the TFY **India-PAN** detector; the
  **faculty-name `Person`** canary (ensure authorized faculty names aren't wrongly masked).

---

## E. Environment-variable reference (TrueFoundry-related)

| Var | Where | Purpose |
| --- | --- | --- |
| `GATEWAY_ENFORCED` | moodle-mcp (Cloudflare) | gate `/mcp` behind the gateway secret (default false) |
| `GATEWAY_SHARED_SECRET` | moodle-mcp (Cloudflare) + TFY Secret Mgr | the shared `x-mcp-gateway-secret` (≥32 chars; comma-list for rotation) |
| `TRUEFOUNDRY_API_KEY` | moodle-agent / onepager | VAT (machine) for the AI Gateway |
| `TRUEFOUNDRY_BASE_URL` | moodle-agent / onepager | `https://gateway.truefoundry.ai/...` OpenAI-compatible base |
| `TRUEFOUNDRY_MODEL` | moodle-agent / onepager | gateway model id (e.g. `openrouter/google-gemini-flash-latest`) |
| `TRUEFOUNDRY_REQUIRED` | moodle-agent / onepager | fail closed if TFY not configured |
| `TRUEFOUNDRY_INPUT_GUARDRAILS` / `_OUTPUT_GUARDRAILS` | onepager | `X-TFY-GUARDRAILS` groups (e.g. `moodle-pii/pii-redaction`) |
| `TRUEFOUNDRY_PROMPT_INJECTION_GUARDRAIL` / `_SECRETS_GUARDRAIL` | onepager | advanced-guardrail refs when required |
| `MCP_PII_REDACTION_MODE` | moodle-mcp | in-server PII (primary control) `off\|shadow\|enforce` (prod: enforce) |

Secrets live only in gitignored `.env.*.local` files and the respective platform secret stores — never in
Git, manifests, or shell history.
