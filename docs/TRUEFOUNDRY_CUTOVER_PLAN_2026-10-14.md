# TrueFoundry — End-State Cutover Plan

**Date:** 2026-10-14 · **Tenant:** `https://gradelessai.truefoundry.cloud` (`ailabs2@jaipuria.ac.in`, Pro)
**Goal:** take TrueFoundry from *configured-but-dormant* to the *active, enforced* path — the gateway
is the only door to the MCP, staff/students are identity-mapped, the report generator routes its LLM
calls through the model gateway, and MCP + model + guardrail metrics populate the TFY dashboard.

This plan supersedes the status in `TRUEFOUNDRY_IMPLEMENTATION_LOG.md` (that doc predates the `tfy apply`
we ran and under-reports). For the apply/enforce mechanics see `TRUEFOUNDRY_APPLY_RUNBOOK.md`; for whether
TFY is *required* at all, see `TRUEFOUNDRY_INTEGRATION_PLAN_2026-10-13.md` §G (it is an enhancement, not a
prerequisite — the MCP already has OAuth + campus/student RBAC + scoped RLS + audit + in-server PII).

---

## 0. Where we are (VERIFIED live via `tfy apply --dry-run` 2026-10-14)

**CONFIGURATION — ~95% live** ✅ (dry-run against the tenant returned "no changes" for each, i.e. already
applied): source `jaipuria-moodle-prod` · virtual `jaipuria-moodle-student` · virtual `jaipuria-moodle-staff`
· guardrail groups `moodle-access`/cedar, `moodle-pii`, `moodle-security` · binding `moodle-mcp-guardrails`
· `gateway-data-access-config`. Plus: Model Gateway live (OpenRouter + 2 models, playground-verified);
in-server PII redaction `enforce` LIVE in prod; code shipped (manifests + validator, `GatewayEnforce`,
faculty tools); PRs #21/#22/#26/#27 merged. **Yesterday's `tfy apply` genuinely landed the whole config.**

**Config drift to reconcile (one re-apply):** the live `jaipuria-moodle-staff` toolset is the OLD set —
**missing `create_report` + the 4 faculty tools**, still has `list_access_requests`; and `onboarding`
(PR #28) isn't in `manifests_core` yet. A re-apply after Phase 0 fixes both.

**ACTIVATION — not done (this is all that's left; the config is NOT the gap):** `GATEWAY_ENFORCED=false`
(origin accepts direct traffic — gateway bypassable) · `GATEWAY_SHARED_SECRET` absent both sides · inbound
identity (Jaipuria Google IdP → TFY) not registered · source OAuth consent ("Connect") not confirmed in the
dashboard · `moodle-agent` not routed through the AI gateway (no VAT) · `create_report` approval rule unset ·
rate-limit manifests `30/31` invalid type (never applied) · no traffic through the gateway yet (so MCP
metrics empty).

**Net: the *configuration* is essentially done and live; only the *cutover* (identity → connect → enforce →
migrate clients) remains.** The dashboard can confirm the two things the CLI can't: the source's Connect/
consent status, and whether any traffic has flowed (MCP Metrics).

---

## Guiding principles (read before starting)

1. **Enforce LAST.** Flipping `GATEWAY_ENFORCED=true` makes the origin reject any client still on the
   direct URL (your Claude connector). Prove the gateway end-to-end, migrate clients, *then* enforce.
2. **The origin stays open** until Phase 5. Nothing a user relies on breaks before then.
3. **Every phase has a verification gate.** Do not proceed on red.
4. **Instant rollback at the only risky step** (Phase 5): `GATEWAY_ENFORCED=false` + redeploy.
5. **Modular stop points.** Phase 3 (model gateway for the agent) is independent of and higher-value
   than Phases 1–2+5 (MCP-gateway enforcement). You may stop after Phase 3.

## Ownership legend
🧑‍💻 **me** (code, Cloudflare `wrangler deploy`, verification) · 👤 **you** (Cloudflare *secrets*, TFY
dashboard, `tfy apply` via `!`, OAuth consent/sign-in) · 🏢 **Rehearsal team** (deploy of
`reports.tryrehearsal.ai`) · 👀 **rajika** (PR review). *I cannot log into the TFY dashboard — tenant
steps are yours; I prepare everything and guide each click.*

## Dependency order
```
Phase 0 (code)  ─┬─►  Phase 1 (connect gateway) ─► Phase 2 (staff identity) ─┐
                 │                                                            ├─► Phase 4 (guardrail
                 └─►  Phase 3 (agent model gateway)  ──────────────────────── ┘    decisions) ─► Phase 5
                                                                                   (ENFORCE) ─► Phase 6
```
Phase 3 runs in parallel with 1–2 (model gateway ≠ MCP gateway).

---

## Phase 0 — Code prep (🧑‍💻 me · no tenant, zero prod risk)

| # | Step | Detail |
| --- | --- | --- |
| 0.1 | Rewrite `manifests/30` + `31` | Replace the invalid `tenant-rate-limit-config/mcp` type with `gateway-rate-limiting-config` (rules[] / limit_to / unit / rate_limit_applies_per). Keep per-user + `create_report` 60/hr intent. |
| 0.2 | Add `onboarding` to the applied subset | Add the tool to `truefoundry/manifests_core/` (the subset driving `tfy apply`); `manifests/` already has it from PR #28. Re-run the validator. |
| 0.3 | Prepare the agent VAT env template | Add `TRUEFOUNDRY_*` keys to `moodle-agent`'s `.env.truefoundry.local` / render env template (values filled in Phase 3). |
| 0.4 | Correct `TRUEFOUNDRY_IMPLEMENTATION_LOG.md` | Bring it to the real state (this plan is the forward view). |

**Gate:** `scripts/validate_truefoundry_gateway.py` passes; PR raised + reviewed (👀 rajika).
**Rollback:** n/a (nothing deployed).

## Phase 1 — Connect the gateway to the origin (👤 TFY + 🧑‍💻 CF · origin stays OPEN)

| # | Step | Owner |
| --- | --- | --- |
| 1.1 | If the source "Connect" throws *Authorization session mismatch*: set `OAUTH_ALLOW_CROSS_CLIENT_PKCE=true` **and** `OAUTH_REDIRECT_HOSTS=gradelessai.truefoundry.cloud` in `cloudflare/wrangler.jsonc` (they must go together) → redeploy. Additive; direct-origin OAuth unaffected. | 🧑‍💻 me |
| 1.2 | In TFY, complete the **source OAuth consent** on `jaipuria-moodle-prod` (sign in as a jaipuria account, approve). | 👤 you |
| 1.3 | Re-run `tfy apply` (reconciles `onboarding`, the rewritten rate limits, and exact tool sets). | 👤 you (`!tfy apply`) |

**Gate:** source shows **Connected**; a call through the **student** virtual URL
`https://gateway.truefoundry.ai/mcp/jaipuria-moodle-student/server` returns a `whoami` result; **MCP
Metrics** begins showing tool traffic.
**Rollback:** none needed (origin still open, nothing enforced).

## Phase 2 — Inbound identity for staff (👤 TFY tenant-admin)

| # | Step |
| --- | --- |
| 2.1 | Register **Jaipuria Google** as an Identity Provider (Settings → Security & Access → Identity Providers). |
| 2.2 | Create/choose a TFY **team**; map the IdP → team; add the 12 staff emails (same list as the Cedar policy). |
| 2.3 | Grant the team on **`jaipuria-moodle-staff`** (Access Control). |
| 2.4 | Add the **`create_report` single-execution approval** (MCP Tool Approvals, `count: 1`) on the encoded staff tool name — only exists post-apply. |

**Gate:** a staff member signs in via Jaipuria Google → reaches the **staff** virtual MCP → `whoami`
returns their cross-campus/dean scope through the gateway.

## Phase 3 — Route the agent's LLM calls through the model gateway (👤 TFY + 🏢 deploy · PARALLEL, highest value)

| # | Step | Owner |
| --- | --- | --- |
| 3.1 | Create a **Virtual Account** + **Virtual Account Token**; grant it the report model + the pii/secrets/prompt-injection guardrails. | 👤 you |
| 3.2 | Set `TRUEFOUNDRY_API_KEY` (VAT) + `TRUEFOUNDRY_BASE_URL` + `TRUEFOUNDRY_MODEL` + `TRUEFOUNDRY_REQUIRED=1` in the agent's secret store; **remove the direct OpenRouter key** from the agent. | 👤 / 🏢 |
| 3.3 | Redeploy `reports.tryrehearsal.ai`. | 🏢 Rehearsal team |

**Gate:** generate one report → TFY **Model Metrics** shows the call; confirm the model received the
pseudonym (`Aarav`), never a real name (token-then-stitch); no direct OpenRouter key remains in the agent.
**Note:** the agent is core Rehearsal infra — 3.3 is gated on that team. Code is already in `app/onepager/build.py`.

## Phase 4 — Guardrail hardening decisions (👤 you / security)

| # | Step |
| --- | --- |
| 4.1 | Raise/verify the TFY **India-PAN** detector (a canary showed synthetic PAN leaking past it) → re-run the PAN/Aadhaar/email/phone canary; all must mask. (In-server `_PAN_RE` already catches PAN; this is the secondary net.) |
| 4.2 | **Faculty-name `Person` canary:** `moodle-pii` includes `Person`; confirm authorized faculty names in staff tools are not wrongly masked, or make a privacy decision. |

**Gate:** canaries pass; sign-off recorded.

## Phase 5 — ENFORCE the private-origin boundary (👤 secret + 🧑‍💻 deploy · the cutover, LAST)

| # | Step | Owner |
| --- | --- | --- |
| 5.1 | Install `GATEWAY_SHARED_SECRET` (the 64-hex in `moodle-mcp/.env.gateway.local`): Cloudflare `wrangler secret put GATEWAY_SHARED_SECRET`, **and** TFY — add outbound header `x-mcp-gateway-secret: {{tfy-secret://…}}` on `jaipuria-moodle-prod`. | 👤 you |
| 5.2 | Set `GATEWAY_ENFORCED=true` in `wrangler.jsonc` → redeploy. | 🧑‍💻 me |
| 5.3 | **Verify:** direct `POST /mcp` without the secret → **403 gateway_required**; a call *through* the gateway → works; `/.well-known/*` + `/authorize` still open. | 🧑‍💻 me |
| 5.4 | Re-point clients (Claude connector + Rehearsal OS) to the **gateway virtual URLs**; publish ONLY those, never the origin. | 👤 you |

**Gate:** clients work through the gateway; direct origin 403s; MCP metrics/traces populate in TFY.
**Rollback (instant):** set `GATEWAY_ENFORCED=false` → redeploy (origin open again in ~1 min).

## Phase 6 — Hygiene (👤 you)

| # | Step |
| --- | --- |
| 6.1 | **Rotate the OpenRouter key** (it was exposed in chat). Update the TFY provider with the new key; the old one dies. With the agent now on a VAT, the raw OpenRouter key lives only in the TFY provider config. |

---

## Risks & notes
- **Biggest risk:** enforcing before clients are migrated → connector outage. Mitigated by ordering + the instant flag rollback.
- **Source OAuth consent is the linchpin** — if "Connect" can't complete even after 1.1, the gateway can't proxy; stop and debug there (don't attempt later phases).
- **The connector's recent 502s are client-side cache** from the earlier outage; they clear once it reconnects to a healthy origin.
- **You can stop after Phase 3** and still get the highest-value piece (model gateway + DPDP pseudonym routing + model/guardrail observability) without touching MCP-gateway enforcement.
- **What I can execute directly:** Phase 0 entirely; 1.1, 5.2–5.3 (Cloudflare deploys); all verification. Everything in the TFY dashboard, Cloudflare *secrets*, `tfy apply`, OAuth consent/sign-in, and the agent deploy are yours / the Rehearsal team's — I prepare and guide each.

## Quick status checklist
- [ ] 0.1 rate-limit manifests rewritten · [ ] 0.2 onboarding in manifests_core · [ ] 0.3 agent env template · [ ] 0.4 log corrected
- [ ] 1.1 cross-client PKCE (if needed) · [ ] 1.2 source connected · [ ] 1.3 re-applied · [ ] student URL smoke test
- [ ] 2.1 IdP registered · [ ] 2.2 team mapped · [ ] 2.3 staff granted · [ ] 2.4 approval rule · [ ] staff smoke test
- [ ] 3.1 VAT · [ ] 3.2 agent env · [ ] 3.3 agent redeploy · [ ] report-through-gateway verified
- [ ] 4.1 PAN detector · [ ] 4.2 Person canary
- [ ] 5.1 secret both sides · [ ] 5.2 enforced · [ ] 5.3 403 verified · [ ] 5.4 clients migrated
- [ ] 6.1 OpenRouter key rotated

---

# Appendix A — Dashboard runbook, grounded in the TrueFoundry MCP docs (read 2026-10-14)

Sources: `/docs/ai-gateway/mcp/mcp-server-getting-started`, `/mcp-gateway-auth-security`,
`/virtual-mcp-server`, `/connect-mcp-from-ide`.

## A.0 The auth model — the single most important thing to understand
TrueFoundry separates **two** authentications, and they are independent:

- **INBOUND** (end user → the gateway): the caller must present a **TrueFoundry-recognized identity** —
  TrueFoundry OAuth (browser sign-in), a Personal/Virtual Account token, **or an external IdP (Google)
  JWT** that TrueFoundry is configured to accept. *This is the piece that is not yet set up.*
- **OUTBOUND** (gateway → our Moodle MCP origin): already configured as **OAuth2 Authorization Code**
  (3LO) — each end user consents once to **our** Google OAuth, and that per-user token is what our MCP's
  RBAC/RLS uses. The gateway stores and auto-refreshes it.

So a staff member hitting a virtual URL authenticates **twice**: once to TFY (inbound), once to our
Moodle OAuth (outbound consent, one-time). Our per-campus/per-student authorization rides on the
**outbound** token — TrueFoundry is the ingress/policy/observability layer, not the authority.

## A.1 ⚠️ Key decision + scaling caveat (this reshapes Phase 2 and gates Phase 5)
Mapping Google → a TFY **team** requires, per the docs, that **the target users already exist in the
tenant**. That is fine for the **~12 staff** but **does not scale to ~3,000 students**. Combined with the
fact that **`GATEWAY_ENFORCED` is origin-wide** (it forces *everyone* through the gateway), this gives:

- ✅ **Staff gateway is practical now** — 12 known users, manageable inbound identity.
- ⚠️ **Student gateway at scale is not** with the team-mapping model — options are **Token Passthrough**
  (gateway forwards the Google JWT; our MCP would need to accept it directly — it uses OAuthProxy today,
  so this needs validation), or **resolve-to-Virtual-Account** (loses per-user TFY attribution; our MCP
  still enforces per-user via the outbound token), or **keep students off the gateway** (direct origin /
  Rehearsal OS).
- 🔴 **Therefore: do NOT run Phase 5 (origin-wide enforce) until the student path is decided** — enforcing
  would lock out every student. **Recommended: run a STAFF PILOT first** (Phases 1–2 for staff only,
  observe in TFY), and make the student-inbound decision before any enforcement.

## A.2 Register / connect the source (confirms Phase 1)
1. Left sidebar → **MCP Gateway** → **Add Server** → **Connect any Remote MCP Server**.
   *(Our `jaipuria-moodle-prod` is already registered via `tfy apply`; this is for reference / edits.)*
2. Fields: **Name**, **Display Name**, **Description**, **URL** (`https://moodle-mcp.rehearsal-os.app/mcp`),
   **Collaborators**, **Auth Data**. Our **Auth Data = OAuth2 → Authorization Code** (Token URL,
   Registration URL, Code Challenge = **S256**, JWT Source = **Access Token**; no client secret — DCR).
3. **Connect:** click **Connect Now** → complete our Google consent screen → the tools appear. *(A request
   without consent returns 401 `McpAuthRequiredError` + an authorization URL — expected before consent.)*
   → **this is Phase 1.2.** If it errors with a redirect/session mismatch, apply Phase 1.1 (cross-client PKCE).

## A.3 Inbound identity — register Jaipuria Google as an IdP (this is Phase 2.1–2.2)
**Settings → Security & Access → Identity Providers → “+”**. Enter the **issuer URL**, **allowed
audiences**, **JWKS URI**, and a **Provider** value. Under **Resolve Token To**:
- **Team/User** (for humans): set the **Team Claim** → map Google-signed-in staff to a TFY team. *Target
  users must already exist in the tenant* (pre-create the 12 staff).
- **Virtual Account** (machine / the scale workaround): set **Name Claim** (+ optional **Organization Claim**).

Then grant that team on the server (A.4).

## A.4 Access control — grant who can use each server (Phase 2.3)
Open the MCP server (or **virtual** server) → **Collaborators** tab → **+ Add Collaborators** → add the
team/users → role **MCP Server User** (`mcp-server-user`) to use, **MCP Server Manager** to administer.
Access can be scoped per-server and per-tool. *(`tfy apply` already set `team:everyone` on student and the
12 named staff on staff; the IdP mapping is what makes those identities real.)*

## A.5 Virtual MCP servers (reference — already applied)
Created via the dashboard (Supademo walkthrough) or `tfy apply`. **Best Effort Mode** OFF (we want a hard
fail if the source is down). Tools are renamed `<tool>_<server-id>` at the virtual layer — this is why the
`create_report` approval rule (2.4) must target the **encoded** name, which only exists post-apply.

## A.6 The real client URL + how clients connect (corrects Phase 1/5 — do NOT hardcode)
- URL format is `https://<gateway>/<tenant>/mcp/<server>/server` — **copy the exact URL from each server's
  “How To Use” tab** (it is the source of truth; our earlier `gateway.truefoundry.ai/mcp/<server>/server`
  guess may be missing the tenant segment).
- **Default client auth = “Sign in with TrueFoundry” (OAuth)** — the client config is just the URL, no
  token; the client opens a browser, the user signs in to TFY and clicks **Approve**, then completes our
  Moodle OAuth consent once. (Claude Code: `claude mcp add --transport http <name> <url>`; Claude Desktop:
  add a custom/remote connector with that URL. A Personal Access Token header is the fallback for headless.)

## A.7 Reset / rollback inside TFY
MCP server detail → three-dot menu next to **Edit** → **Revoke all tokens** → confirm **Revoke All**. This
clears all stored OAuth tokens + auth overrides for the server (everyone re-consents). Recorded in
**Activity Logs**. Note: it only clears tokens at the gateway — to invalidate upstream, also revoke in the
provider (our Google OAuth / Supabase OAuth store).

## A.8 Grounded sequence to “make it live” (staff pilot)
1. **Phase 1** — confirm the source is **Connected** (A.2; apply cross-client PKCE if the consent errors),
   re-apply manifests, smoke-test the **student** URL from its How-To-Use tab (team:everyone needs no IdP).
2. **Phase 2 (staff)** — register Google IdP (A.3) → pre-create/ map the 12 staff to a team → grant on
   `jaipuria-moodle-staff` (A.4) → add the `create_report` approval on the encoded name → a staff member
   connects with the **How-To-Use** URL and verifies `whoami` + a scoped query through the gateway.
3. **Observe** MCP + guardrail metrics in TFY for the staff pilot.
4. **Decide the student path** (A.1) **before** Phase 5. Only then install the gateway secret both sides and
   flip `GATEWAY_ENFORCED=true` (origin-wide) — with the instant flag rollback ready.
