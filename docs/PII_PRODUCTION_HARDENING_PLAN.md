# ADR: Production PII boundary for Moodle MCP and report generation

**Status:** Accepted; application changes implemented, gateway policy rollout requires tenant admin
**Date:** 2026-10-08
**Owners:** Moodle MCP, moodle-agent, Jaipuria AI/platform administrators

## Objective

No model or generic MCP client may receive a real student name, enrolment number, email, phone,
PAN, or Aadhaar number from Moodle tools. Authorised humans may still see identity in the
server-rendered report. Academic facts remain usable, but are associated only with an opaque
`S_*` reference or the report generator's `[[FIRST]]` placeholder while in model context.

PII redaction is independent from RBAC: RBAC decides **which records may be read**; redaction decides
**which identity fields may enter an AI context**. Both controls must pass.

## Decision

Use two mandatory enforcement layers and a third managed layer:

1. **Moodle MCP local boundary (mandatory, fail closed).** `GuardMiddleware` redacts the real
   FastMCP `ToolResult`, including `structured_content`, serialized `TextContent`, embedded text
   resources and metadata, before returning it or capturing the result in the audit ledger.
2. **moodle-agent LLM boundary (mandatory, fail closed).** The report prompt uses `[[FIRST]]`; the
   real name is stitched back only after all LLM retries. Production LLM calls must use a dedicated
   TrueFoundry Virtual Account and must never downgrade to a direct provider.
3. **TrueFoundry managed boundary (defence in depth).** Apply PII mutation on LLM input/output and
   MCP post-tool results, prompt-injection validation on LLM input, secrets detection on LLM output
   and MCP post-tool results, and Cedar/OPA policy checks before MCP tool invocation.

Application-side Google OAuth, Supabase RBAC/RLS and student self-only filters remain authoritative.
TrueFoundry policy is an additional boundary, not a replacement for record-level authorization.

## End-to-end control map

| Boundary | Control | Failure behaviour |
|---|---|---|
| User → MCP | Google OAuth, authoritative whitelist, role/tool gate | deny |
| MCP → Supabase | campus/student filters + RLS | deny |
| Tool handler → MCP client | local `ToolResult` redactor | deny in `enforce` |
| MCP error channel | PII-safe error contracts | never enumerate candidate identities |
| moodle-agent → model | `[[FIRST]]`, no name/id/email/phone | deny if TrueFoundry unavailable |
| TrueFoundry → model | input PII mutation + prompt-injection validation | phased to strict enforce |
| Model → moodle-agent | output PII mutation + secrets detection; `stream=false` | phased to strict enforce |
| MCP Gateway post-tool | PII + secrets mutation before model context | strict enforce after audit |
| Logs/traces | counts, verdicts, trace IDs and safe metadata only | bodies disabled/redacted |

## TrueFoundry resources to provision and use

### Required now

1. **Virtual Account Token**
   - Create `moodle-agent-prod`; grant only the approved virtual model and guardrails.
   - Store its token as `TRUEFOUNDRY_API_KEY`; do not use a human PAT.
   - Rotate with a grace period and keep provider keys only in TrueFoundry Secret Manager.

2. **Virtual model**
   - Publish one stable model FQN for reports.
   - Put primary/fallback providers behind this FQN; all fallback traffic therefore retains the
     same guardrails, budgets and traces.
   - Do not implement a direct OpenRouter fallback in application code.

3. **Guardrails**
   - Existing: `moodle-pii/pii-redaction` in **Mutate** mode on LLM input and output.
   - Provision Prompt Injection and attach it to LLM input in **Validate** mode.
   - Provision Secrets Detection and attach it to LLM output and MCP post-tool.
   - Register Moodle MCP and attach PII mutation to MCP post-tool results.
   - Add the exact selectors to `TRUEFOUNDRY_PROMPT_INJECTION_GUARDRAIL` and
     `TRUEFOUNDRY_SECRETS_GUARDRAIL`.

4. **Gateway policies rather than header-only policy**
   - Target the production Virtual Account, the report virtual model, and the registered Moodle MCP.
   - Keep the request-level `X-TFY-GUARDRAILS` header for the report path as defence in depth.
   - Use a generic custom error message; never expose detector evidence or matched PII.

5. **Logging and data access**
   - Keep request/response body logging disabled for `data_class=pseudonymized-academic`.
   - Retain model, token, latency, cost, policy verdict and trace identifiers.
   - Restrict request-trace access to the privacy/platform team and set an approved retention period.
   - Never put email, enrolment number, student reference, campus/batch combination or report URL in
     `X-TFY-METADATA`.

6. **Rate limits and budgets**
   - Rate-limit by production Virtual Account and `metadata.feature=student-report`.
   - Configure daily token and monthly cost ceilings plus alerts at 70/90/100%.
   - Keep the MCP server's per-principal limiter; the gateway quota is not an authorization control.

7. **Observability**
   - Alert on PII guardrail blocks/errors, direct-provider attempts, missing applied configuration,
     fallback rate, p95 guardrail latency and budget throttles.
   - Export operational spans through OpenTelemetry. Export verdicts/counts, not matched values.
   - Reconcile `x-tfy-trace-id` with the MCP request ID using a non-PII correlation field.

### Recommended next

- **MCP Gateway + Virtual MCP Server:** expose only the reviewed read/self-service tool set. Preserve
  end-user OAuth identity to Moodle MCP; never replace it with one shared static identity.
- **Cedar or OPA pre-tool policy:** mirror role/tool entitlements (`student` self-only, campus dean
  own campus, cross-campus whitelist all campuses). App-side RBAC remains the final decision.
- **Data residency:** use a customer VPC/hybrid gateway if the approved privacy posture requires
  academic payloads to remain in Jaipuria-controlled infrastructure.
- **Playground/evaluations:** maintain a synthetic PII and indirect-prompt-injection corpus and run
  it against every guardrail or model-policy change before promotion.

### Deliberately not enabled

- **Semantic caching for student reports.** Reports are identity- and snapshot-specific and the
  application already has a scoped Supabase narrative cache. A semantic cache introduces avoidable
  cross-student correctness/privacy risk. If gateway caching is ever used, use exact match only,
  isolate by environment + prompt version + opaque student reference, and use a short TTL.
- **Streaming model output.** TrueFoundry output guardrails require a complete response, so report
  generation sets `stream=false`.

## Rollout gates

### Phase 0 — application boundary (implemented)

- Local MCP mode is `enforce` in production manifests and requires a stable 32+ character key.
- Real `ToolResult` wrapper/content/resource/meta tests pass.
- Ambiguous lookup errors no longer enumerate roster identities.
- Production report generation requires TrueFoundry and uses PII guardrails on input/output.

### Phase 1 — TrueFoundry Audit (staging)

- Register the MCP and production-like Virtual Account.
- Run PII, prompt-injection and secrets controls in Audit for synthetic traffic.
- Acceptance: zero raw canaries in model/provider payloads and stored gateway bodies; false-positive
  rate documented; p95 added latency within the agreed budget.

### Phase 2 — Enforce But Ignore On Error

- Block confirmed violations while measuring provider availability.
- Acceptance: seven days with no unexpected blocks, missing policies or direct-provider traffic.

### Phase 3 — Enforce

- Fail closed on both violation and guardrail-provider failure for the report and MCP PII routes.
- Maintain a documented break-glass rollback to the previous application release, never to direct
  unguarded provider traffic.

## Test strategy and release evidence

| Test | Layer | Required evidence |
|---|---|---|
| Structured identity tokenisation | unit | names/ids/emails replaced; marks unchanged |
| Real FastMCP `ToolResult` | integration | structured, text, embedded resources and metadata clean |
| Error-channel enumeration | unit | no candidate identity in `ToolError` |
| PAN/Aadhaar/phone/email/enrolment canaries | adversarial | no raw value survives |
| Name placeholder + retry | integration | name absent from every request and corrective turn |
| TrueFoundry request contract | contract | HTTPS gateway, VAT, input/output rails, `stream=false` |
| TrueFoundry live canary | staging E2E | applied-rule/trace evidence and provider payload clean |
| RBAC × PII matrix | E2E | access scope correct and returned identity redacted |
| Load/latency | performance | p95/p99 with guardrails and fallback under target |

No production promotion is complete until the live canary and RBAC × PII matrix pass with the exact
gateway policies and Virtual Account used in production.

## Primary product references

- [TrueFoundry guardrail hooks, mutation and enforcement](https://www.truefoundry.com/docs/ai-gateway/guardrails-overview)
- [Guardrail policy targeting for models, MCP servers and tools](https://www.truefoundry.com/docs/ai-gateway/guardrails-configuration)
- [PII/PHI detection across LLM and MCP hooks](https://www.truefoundry.com/docs/ai-gateway/tfy-pii)
- [Secrets detection](https://www.truefoundry.com/docs/ai-gateway/secrets-detection)
- [Prompt-injection guardrail](https://www.truefoundry.com/docs/ai-gateway/tfy-prompt-injection)
- [Virtual models and managed fallback](https://www.truefoundry.com/docs/ai-gateway/virtual-model)
- [MCP Gateway](https://www.truefoundry.com/docs/ai-gateway/mcp/mcp-overview)
