# TrueFoundry MCP Gateway — `tfy apply` Runbook

**Tenant:** `https://gradelessai.truefoundry.cloud` (account `ailabs2@jaipuria.ac.in`, Pro plan)
**Goal:** apply the reviewed desired-state in `truefoundry/manifests/` so the MCP Gateway runs the
*exact* student/staff Virtual MCPs + Cedar RBAC + PII/secrets/prompt-injection guardrails + rate limits,
with no UI drift. This is the authoritative method — it replaces hand-building in the dashboard.

> **Why CLI, not the UI:** the dashboard can create a server and pick tools, but it **cannot** bind
> guardrail policies, Cedar, rate limits, or the `create_report` approval rule to a server — those live
> only in the manifests. The UI master-toggles also make exact tool sets finicky. `tfy apply` reconciles
> everything from Git in one validated shot and **overwrites** anything created by hand in the UI
> (`jaipuria-moodle-prod`, `jaipuria-moodle-student`, `jaipuria-moodle-staff` created in the browser will
> be reconciled to match these files — that's the intended no-drift outcome).

---

## 0. Prerequisites (do these first)

- [ ] **Tenant-admin access** to the gradelessai tenant (you have it as `ailabs2`).
- [ ] **A checkout at the release SHA.** Ideally **PR #21 (faculty tools) is merged + deployed first**, so
      the source MCP actually exposes `list_faculty / faculty_courses / course_faculty / faculty_feedback`
      that the manifests reference. If not yet deployed, apply still works — those 4 tools just won't appear
      on the source until the MCP is redeployed.
      ```bash
      cd "/Users/mansi.gambhir/Documents/Moodle Analysis/moodle-mcp"
      git fetch origin && git checkout feat/faculty-course-tools   # or main once #21 is merged
      ```
- [ ] **The gateway-origin secret** generated earlier is in `moodle-mcp/.env.gateway.local`
      (`GATEWAY_SHARED_SECRET=<64-hex>`). You'll install it in TWO places in step 5. Never commit it.

---

## 1. Install the CLI + authenticate

```bash
pip install -U "truefoundry"          # or: pipx install truefoundry
tfy login --host https://gradelessai.truefoundry.cloud
```
`tfy login` opens a browser device-flow — approve it as `ailabs2`. Confirm:
```bash
tfy --version
```

> If CI/automation ever needs this, use a **scoped `TFY_API_KEY`** (Access → API Keys) instead of an
> interactive login — never a personal token in shared config.

---

## 2. Pre-flight validation (no tenant changes)

```bash
cd "/Users/mansi.gambhir/Documents/Moodle Analysis/moodle-mcp"
python3 scripts/validate_truefoundry_gateway.py      # must print: TrueFoundry gateway desired state: valid
```
This proves the manifests are internally consistent and match the code's tool inventory, the student
self-tool list, and the approved-staff roster **before** anything hits the tenant.

---

## 3. Dry-run and review the diff

```bash
tfy apply --dir truefoundry/manifests --dry-run --show-diff | tee /tmp/tfy_dryrun_$(date +%Y%m%d).txt
```
Read the diff. You expect to see creates/updates for (in file order `00`→`50`):

| File | Resource |
| --- | --- |
| `00-moodle-remote.yaml` | source `jaipuria-moodle-prod` (deny-by-default, OAuth2 auth-code, DCR) |
| `10-moodle-student-virtual.yaml` | `jaipuria-moodle-student` — 10 self-service tools, `team:everyone` |
| `20-moodle-staff-virtual.yaml` | `jaipuria-moodle-staff` — full data toolset, 12 named staff |
| `30-moodle-user-rate-limit.yaml` | per-user rate limit |
| `31-moodle-report-rate-limit.yaml` | per-user `create_report` limit (60/hr) |
| `40-gateway-data-access.yaml` | own-data/team-metrics/admin-all trace access |
| `41-moodle-access-guardrail-group.yaml` | `moodle-access/cedar-rbac` (Cedar policy embedded) |
| `42-moodle-pii-guardrail-group.yaml` | `moodle-pii/pii-redaction` |
| `43-moodle-security-guardrail-group.yaml` | `moodle-security/secrets` + `/prompt-injection` |
| `50-moodle-guardrails.yaml` | binds Cedar (pre) + PII/secrets/prompt-injection (post) to all 3 servers |

Archive `/tmp/tfy_dryrun_*.txt` as the change record.

---

## 4. Apply

```bash
tfy apply --dir truefoundry/manifests
```
`tfy apply` is declarative/idempotent — re-running converges to the same state. It reconciles the
UI-created `jaipuria-moodle-*` to match the files exactly (correct tool sets, no stray `list_access_requests`
on staff, faculty tools included once deployed).

---

## 5. Install the gateway-origin secret (both sides) — do NOT skip before enforcing

The private-origin boundary (`GATEWAY_ENFORCED`) needs the **same** secret on the gateway and the origin.

**a. TrueFoundry Secret Manager** (store, then reference as an upstream header on `jaipuria-moodle-prod`):
```bash
# read the value WITHOUT echoing it into history:
VAL=$(grep -E '^GATEWAY_SHARED_SECRET=' moodle-mcp/.env.gateway.local | cut -d= -f2-)
tfy secret create --name x-mcp-gateway-secret --value "$VAL"   # name per your tenant convention
```
Then on the MCP server config, add the outbound header `x-mcp-gateway-secret: {{tfy-secret://...}}`
(reference the FQN; never paste the raw value into a manifest).

**b. Cloudflare** (origin side):
```bash
cd moodle-mcp/cloudflare
wrangler secret put GATEWAY_SHARED_SECRET     # paste the SAME value from .env.gateway.local
```

---

## 6. Verify (post-apply, before enforcing)

In the dashboard (MCP Gateway → Registry / Virtual MCPs, and Guardrails):
- [ ] `jaipuria-moodle-prod` present; **Enable new tools by default = OFF**; no end-user collaborators.
- [ ] `jaipuria-moodle-student` (10 tools) and `jaipuria-moodle-staff` (full data set) present; **Best Effort OFF**.
- [ ] Guardrail groups `moodle-access`, `moodle-pii`, `moodle-security` exist and `moodle-mcp-guardrails`
      binds them (pre: cedar; post: pii + secrets + prompt-injection).
- [ ] **Access Control / Collaborators**: `team:everyone` on student; the 12 named staff on staff.
      (TrueFoundry moved collaborators to Access Control — confirm they resolved from the manifest.)
- [ ] Add the **`create_report` single-execution approval** (MCP Tool Approvals, `count: 1`) on the
      encoded staff tool name — do this after creation because the encoded name only exists post-apply.

Smoke test one call through a virtual URL (needs a user to complete the OAuth consent once):
```
https://gateway.truefoundry.ai/mcp/jaipuria-moodle-staff/server
```
Then **Metrics → MCP Metrics** should show tool traffic.

---

## 7. Enforce the private-origin boundary (final cutover step)

Only after the canary passes:
```bash
# Cloudflare: flip the flag and redeploy
# set GATEWAY_ENFORCED=true in cloudflare/wrangler.jsonc vars, then:
cd moodle-mcp/cloudflare && wrangler deploy
```
Verify:
- [ ] Direct `POST https://moodle-mcp.rehearsal-os.app/mcp` **without** the gateway secret → `403 gateway_required`.
- [ ] OAuth discovery (`/.well-known/*`, `/authorize`) still reachable (never gate those).
- [ ] Calls **through** the virtual MCP URLs still succeed.
- [ ] Publish ONLY the two virtual URLs to Jaipuria OS / clients — never the origin.

---

## Known gotchas (seen this session)

- **Connect → "Authorization session mismatch":** usually transient — retry Connect in the same tab,
  already signed into Google. If it persists, prod needs cross-client PKCE: set
  `OAUTH_ALLOW_CROSS_CLIENT_PKCE=true` **and** `OAUTH_REDIRECT_HOSTS=gradelessai.truefoundry.cloud`
  (the code requires both together), then redeploy.
- **PAN leaked the TFY PII guardrail** in a canary (Aadhaar/email/phone masked, synthetic PAN not).
  The in-server `pii.redact` (`enforce` in prod) catches PAN deterministically, so no DPDP breach — but
  verify/raise the India-PAN detector confidence before relying on the TFY net for PAN.
- **Faculty-name canary:** the PII group includes `Person`. Confirm it does **not** redact authorized
  faculty names in the staff tools, or get a privacy decision, before staff rollout.
- **Don't hand-edit in the UI after this** — the next `tfy apply` overwrites UI changes. Git is authoritative.

---

## Rollback

- Remove end-user collaborators from the virtual servers (kills end-user access immediately), or
- `tfy delete` / re-`tfy apply` a previous reviewed manifest set, or
- Set `GATEWAY_ENFORCED=false` + redeploy to drop the origin boundary while you investigate.
- Never publish the source origin URL as a fallback.

---

## Appendix — Staging `stateless_http` regression (Phase 5, needed only before ≥2 replicas)

Default FastMCP HTTP keeps session state in-process, so with 2+ replicas a client's `initialize` and
`tools/call` can land on different pods and break. `stateless_http=True` removes that assumption. It
**cannot** be validated offline — it needs a staging Service with ≥2 replicas. Do NOT enable multi-replica
until every row passes.

**Setup:** deploy to a staging TrueFoundry Service with `stateless_http=True`, `minReplicas: 2`, and
`MCP_REDIS_URL` set (in-process rate limiters do NOT share across replicas — Redis is required for shared limits).

| # | Test | Pass criteria |
| --- | --- | --- |
| 1 | OAuth sign-in (DCR → authorize → Google → callback → token) | Completes; token usable on a *different* replica than it was issued on |
| 2 | `initialize` then `tools/list` then `tools/call` across replicas | All succeed regardless of which pod each lands on (no "session not found") |
| 3 | `whoami` | Returns the correct principal + campus scope under load balancing |
| 4 | `create_report` (queue → `get_report_job` poll → completed) | Job created on one replica, polled/completed via another |
| 5 | Progress/streaming responses | Stream completes without the draining-instance "ASGI callable returned without completing response" error |
| 6 | Shared rate limit | > per-principal limit across replicas is rejected (proves Redis-backed, not per-pod) |
| 7 | Readiness drain | Kill DB access to one replica → its `/readyz` → 503 → LB stops routing to it; others keep serving |
| 8 | Rolling deploy | A rolling update does not drop an in-flight authenticated session |

**Only after all 8 pass twice** (incl. once after a pod restart): set production `minReplicas: 2` + RPS/CPU
autoscaling, with thresholds derived from a one-replica load test — not guessed. If any row fails, stay at
one replica (sticky routing is best-effort and not a substitute).
