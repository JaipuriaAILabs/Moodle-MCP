# TrueFoundry MCP Gateway production rollout

This runbook implements the AIA-1012 decision in
[`AIA_1012_TRUEFOUNDRY_ARCHITECTURE.md`](AIA_1012_TRUEFOUNDRY_ARCHITECTURE.md). Jaipuria OS remains
the harness; TrueFoundry replaces both the proposed custom gateway and any legacy model-gateway
path.

## Security outcome

TrueFoundry becomes the only supported discovery and policy ingress for Moodle MCP, but it does not
replace Moodle MCP authorization. The source service still resolves the Google identity and applies
student-ID, campus and batch filters on every query. Gateway controls are an independent narrowing
layer and cannot widen a source-side grant.

The design deliberately rejects a shared Virtual Account Token for interactive users. A shared token
would collapse twelve staff identities and every student into one principal. The remote server uses
per-user OAuth authorization-code flow with DCR and PKCE instead, so the upstream Moodle token still
represents the human who initiated the call.

## Repository desired state

| Control | State in this branch |
| --- | --- |
| Remote MCP registry entry | `truefoundry/manifests/00-moodle-remote.yaml` |
| New-tool fail closed | `enable_tools_by_default: false`; access-admin tools excluded |
| Student virtual MCP | exactly the ten reviewed self-only tools |
| Staff virtual MCP | exactly the 28 data/report tools; twelve named collaborators |
| Partial-backend behavior | Best Effort disabled on both virtual servers |
| Per-user MCP rate limit | 90 calls/minute and 2,000/hour, enforced without body logging |
| Report cost limit | 60 `create_report` calls/hour/user, matching the source service cap |
| Trace/data visibility | own traces; team aggregate metrics; tenant-admin investigation access |
| Cedar pre-tool policy | declarative, default-deny student/staff tool matrix |
| Guardrail definitions | declarative Cedar, PII mutation, secret mutation and prompt-injection validation |
| Guardrail binding | Cedar pre-tool; PII, secrets and prompt-injection post-tool |
| Origin bypass resistance | shared header secret, accepted only on `/mcp`; origin fails boot if enforcement has no strong secret |
| Drift test | manifest inventory, OAuth, collaborators, limits and Cedar coverage |

`request_access` and `list_access_requests` stay disabled at the gateway because production
self-service access is off and none of the twelve data users is a registry administrator.

## Tenant activation

1. Configure the TrueFoundry CLI for the production tenant. Do not paste a PAT into a shell history
   or commit it.
2. Run `python3 scripts/validate_truefoundry_gateway.py`.
3. Run `tfy apply --dir truefoundry/manifests --dry-run --show-diff` and review the resolved diff.
4. Apply in staging, exercise the canary matrix below, then run `tfy apply --dir
   truefoundry/manifests` against production.
5. Store a generated secret in TrueFoundry, inject it into the remote server as
   `x-mcp-gateway-secret`, and set the same value as the Cloudflare
   `GATEWAY_SHARED_SECRET`. Verify the proxy can initialize before enabling
   `GATEWAY_ENFORCED=true`; then verify a direct `/mcp` request receives `403 gateway_required`.
6. Publish only the `jaipuria-moodle-student` and `jaipuria-moodle-staff` proxy URLs. Keep the source
   server without end-user collaborators so callers cannot bypass the curated surfaces.
7. In each server's Tools tab, verify **Enable new tools by default** is off.

The repository does not contain a tenant credential, so these control-plane writes cannot be made by
CI or by a local checkout until a tenant administrator authenticates `tfy`.

## Policies to attach

### MCP Tool Pre-Invoke

The checked-in `truefoundry/manifests/41-moodle-access-guardrail-group.yaml` creates
`moodle-access/cedar-rbac` from the reviewed policy in
`truefoundry/policies/moodle-access.cedar`. The checked-in
`truefoundry/manifests/50-moodle-guardrails.yaml` binds it to the production source and both curated
virtual servers. Run these phases:

1. Staging: Audit and compare decisions to the application audit ledger.
2. Production canary: Enforce for the test student and one dean.
3. Production: Enforce, fail closed. A Cedar outage must not silently widen access.

The Cedar policy controls tool entitlement only. It intentionally does not attempt to parse nested
`params.campus` or prove student ownership. Source-side RBAC and Supabase RLS remain authoritative for
those row decisions.

### MCP Tool Post-Invoke

The `42` and `43` manifests provision every selector named by `50-moodle-guardrails.yaml`. They
attach PII/PHI mutation plus Secrets Detection mutation and Prompt Injection validation to tool
results, all with fail-closed enforcement.
Keep the application pseudonymizer enabled: the post-tool policy is a second boundary, not a
replacement.

### Human approval

Create a named-tool approval policy for the staff virtual server's encoded `create_report` tool with
single-execution validity. Add the review team as `MCP Server Approver` and configure the approved
notification channel. Do not attach this approval to the student virtual server: students may only
generate their own report and are already source-rate-limited.

Virtual MCP tool names receive a stable suffix only after creation. Copy the exact encoded tool name
from the staff server's Tools tab before saving the approval policy.

## Privacy, observability and performance

- Disable request/response body logging and **Log Request Body on Block**. Keep only identity,
  server/tool, result class, guardrail verdict, latency and rate-limit metadata.
- Restrict trace/data access to the privacy and platform teams. Export operational OpenTelemetry
  metrics to New Relic without prompt, result or detector-evidence bodies.
- Export traces and metrics as separate OpenTelemetry signals so either can be disabled without
  changing the other. The destination and auth header are tenant secrets and are not committed.
- Use TrueFoundry data routing only after the privacy owner approves the storage region, retention
  and metadata conditions. Do not route request or response bodies.
- Preserve `x-tfy-feedback-target-id` for approved QA ratings without copying report contents into a
  second review store.
- Alert on authentication failures, Cedar denials, PII/secret blocks, tool error rate, p95 latency,
  report queue latency and rate-limit utilization.
- Keep exact and semantic response caching off for student data. Cross-user academic-response caches
  are not an acceptable optimization.
- Keep Virtual MCP Best Effort off. A missing security-critical source must fail the request rather
  than silently present a partial tool universe.
- Use Ask TFY during weekly rollout review to identify MCP calls without guardrail coverage, then
  confirm every suggested change through a reviewed configuration diff.

## Features deliberately not enabled

- **Token passthrough:** Moodle MCP validates its own OAuth issuer, not a TrueFoundry IdP JWT.
- **Shared VAT for people:** destroys end-user attribution and would defeat student isolation.
- **OpenAPI-to-MCP for the report API:** would create a parallel path around the audited MCP RBAC and
  HMAC report-job contract.
- **Semantic caching:** can return one student's output for a merely similar prompt.
- **Exact gateway caching:** duplicates the identity-scoped application cache in a wider boundary.
- **Remote Agent / Agent API migration:** the current report service is not an A2A agent. Registration
  before protocol support would advertise a capability that does not exist.

## Canary matrix

Use synthetic records first, then consenting real accounts:

| Principal | Expected result |
| --- | --- |
| Student, own `get_student` | one owned record, pseudonymized result |
| Student, peer ID | denied by source RBAC; no peer fields in trace |
| Student, cohort tool | absent from virtual `tools/list` and denied by Cedar/source if forced |
| Noida dean, Noida query | allowed |
| Noida dean, Lucknow query | uniform denial/empty result from source RBAC |
| Cross-campus approved user | all four campuses, no access-admin tools |
| Unlisted Jaipuria user | no data session |
| Staff `create_report` | approval pending before execution; single call after approval |
| Burst over 90 calls/minute | gateway tool error; request body not logged |
| PII/secret canary result | redacted or blocked before reaching the model |

Do not cut clients over until the gateway audit and the MCP audit ledger agree on actor, tool,
decision and scope for every row in this matrix.
