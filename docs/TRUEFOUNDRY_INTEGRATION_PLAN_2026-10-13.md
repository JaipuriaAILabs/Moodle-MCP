# TrueFoundry End-to-End Integration Plan for Moodle

**Date:** 2026-10-13
**Scope:** `moodle-mcp`, `moodle-agent`, Jaipuria OS, TrueFoundry Service Deployment, TrueFoundry MCP Gateway, TrueFoundry AI Gateway, Supabase, Redis, and the current Render/Cloudflare path.
**Status:** Implementation-ready working trees with verified faculty data and passing
offline release gates. Browser access to `gradelessai.truefoundry.cloud` is confirmed;
CLI deployment authorization is still pending. Supabase migrations `0016` and `0017`
are prepared but not yet applied.

## Execution Checkpoint (2026-10-13)

This section is the deployment receipt. Items marked **ready** exist and have been
validated locally; they are not described as live until the external control plane
has returned a successful read-back.

| Workstream | Executed result | State |
| --- | --- | --- |
| Faculty source capture | Queried all 48 campus/batch/term scopes; 24 populated and 24 valid empty scopes | **Complete** |
| Private faculty archive | Archive `91e35db3-dfe1-45ab-9125-3487bfdd8bf3` stored in private `moodle-faculty` storage | **Complete** |
| Faculty relational data | 1,228 current course assignments, 2,456 feedback rows, 213 campus/faculty identities | **Complete** |
| Faculty database schema | Migration `0015` applied and current views return HTTP 200 | **Live** |
| Faculty MCP tools | `list_faculty`, `faculty_courses`, and leadership-only `faculty_feedback`; source and gateway deny-by-default controls | **Ready; awaits `0017` and release** |
| Scheduled Workflow | Static DAG, pinned snapshots, scope map, retries, aggregate quality gate, private persistence, UTC cron, failure handler | **Ready; not deployed** |
| Workflow audit schema | Aggregate-only migration `0016_truefoundry_workflow_runs.sql` | **Ready; table currently returns HTTP 404** |
| MCP Service deployment | Code-based TrueFoundry Service spec using Docker, health probes, secret FQNs, fixed resources, one replica, immutable Git SHA | **Ready; not deployed** |
| MCP Gateway | Source, student/staff virtual servers, rate limits, trace access, Cedar, PII, secrets and injection guardrails | **Ready; validator passes** |
| TrueFoundry tenant inventory | Browser login confirmed; the live MCP Registry currently has no Moodle source or Virtual MCP. No deployment workspace or Workflow/Flyte surface has yet been proven for this tenant | **Gateway available; platform capability unverified** |
| Dashboard privacy | Safe metadata and aggregate workflow outputs enabled; model request bodies explicitly disabled | **Ready; tenant logging rule still to verify** |
| Faculty-name canary | The current gateway PII group includes `Person`; staging must prove whether authorized faculty names are masked and obtain a privacy decision before any tool-specific exception | **Required before staff rollout** |
| Release verification | Agent: 197 passed/11 skipped; MCP: all 25 script suites; Workflow/Gateway validators: pass; `git diff --check`: pass | **Complete** |

The remaining live sequence is: approve CLI device login, inspect workspace/Flyte and
secrets, apply `0016`, obtain action-time approval for `0017` because it grants the
MCP database role faculty-view access, run TrueFoundry dry-runs, deploy to staging,
execute a canary, and only then enable production scheduling or gateway enforcement.

## Executive Decision

Use TrueFoundry in four distinct places:

1. **MCP runtime:** deploy the `moodle-mcp` Docker image from GitHub as a TrueFoundry Service.
2. **MCP policy gateway:** register that service as the single source MCP, then expose only curated student and staff Virtual MCP endpoints.
3. **Model gateway:** route every production `moodle-agent` LLM call through the TrueFoundry AI Gateway and a reviewed model/virtual-model route.
4. **Operations and governance:** use TrueFoundry secrets, deployment versions, probes, metrics, policies, guardrails, rate limits, approvals, and controlled promotion.

TrueFoundry will be the managed ingress and policy layer. It will **not** replace Moodle MCP's user-to-row authorization, Supabase RLS, source audit ledger, or server-side PII tokenization.

The recommended end state is:

```text
Interactive Moodle access

Google/Jaipuria user
  -> Jaipuria OS or MCP client
  -> TrueFoundry MCP Gateway authentication
  -> student or staff Virtual MCP
  -> TrueFoundry-hosted Moodle MCP service
  -> Moodle OAuth + application RBAC
  -> Supabase RLS/read-only reporting role

Report narrative generation

Moodle MCP create_report
  -> report queue / moodle-agent worker
  -> TrueFoundry AI Gateway using an application VAT
  -> reviewed model or virtual model
  -> server-side schema validation
  -> identity rehydration after model processing
```

This follows TrueFoundry's model: MCP code is deployed as a service, registered with the MCP Gateway, and optionally grouped into Virtual MCP servers; LLM traffic uses the separate OpenAI-compatible AI Gateway. See [Deploy MCP Server From Code](https://www.truefoundry.com/docs/mcp-server-deployment/deploy-mcp-server-from-code), [MCP Server Getting Started](https://www.truefoundry.com/docs/ai-gateway/mcp/mcp-server-getting-started), and [Introduction to AI Gateway](https://www.truefoundry.com/docs/ai-gateway/intro-to-llm-gateway).

## What Is Already Integrated

| Integration point | Current repository evidence | Actual status |
| --- | --- | --- |
| Agent calls TrueFoundry's OpenAI-compatible endpoint | `moodle-agent/app/config.py`, `moodle-agent/app/onepager/build.py` | Implemented in the working tree |
| Production fails closed without TrueFoundry | `validate_llm_gateway_config()` and `TRUEFOUNDRY_REQUIRED=1` | Implemented and tested |
| LLM input/output PII, prompt-injection, and secret guardrails | `X-TFY-GUARDRAILS` in the report request | Implemented and tested |
| Privacy-safe request settings | `X-TFY-LOGGING-CONFIG: {"enabled": false}`, metadata only, web search disabled | Implemented and tested |
| Remote MCP registry entry | `moodle-mcp/origin/main:truefoundry/manifests/00-moodle-remote.yaml` | Designed in Git; tenant apply not verified |
| Student and staff Virtual MCP servers | `10-moodle-student-virtual.yaml`, `20-moodle-staff-virtual.yaml` | Designed in Git; tenant apply not verified |
| MCP rate limits, trace access, Cedar, PII, secret and prompt-injection policies | `30` through `50` manifests and `moodle-access.cedar` | Designed in Git; tenant apply not verified |
| TrueFoundry hosting of the Moodle MCP code | `moodle-mcp/truefoundry/deploy_mcp.py` plus deployment runbook; production gateway still points to `moodle-mcp.rehearsal-os.app` until canary | Implemented; tenant deploy not verified |
| Production TrueFoundry tenant resources and secrets | Cannot be proven from source | **Not verified** |

Important branch fact: the checked-out `moodle-mcp` branch is older than `origin/main`.
The canonical `truefoundry/` desired state has been materialized into the working tree
and extended for faculty tools, but the release must still reconcile onto current
`origin/main` and produce a reviewed immutable SHA before deployment.

## Architecture Decision Record

### Context

The Moodle MCP is an HTTP/streamable-HTTP FastMCP server with custom Google authorization-code OAuth, dynamic client registration, PKCE, source-side role/campus/student controls, and a Supabase-backed OAuth store. The report agent is a separate application that calls an OpenAI-compatible LLM endpoint.

TrueFoundry recommends direct deployment for HTTP MCP servers, a service bound to `0.0.0.0`, a configured HTTP port, secrets referenced from its secret store, and endpoint verification after deployment. The gateway prefers streamable HTTP. See [deployment from code](https://www.truefoundry.com/docs/mcp-server-deployment/deploy-mcp-server-from-code) and [MCP protocol support](https://www.truefoundry.com/docs/ai-gateway/mcp/mcp-protocol-support).

### Options Considered

| Option | Advantages | Disadvantages | Decision |
| --- | --- | --- | --- |
| Keep MCP on Render/Cloudflare; use TrueFoundry only as gateway | Lowest migration effort; matches existing manifests | Two runtime control planes, public-origin complexity, split observability and rollback | Use only as temporary rollback path |
| Deploy MCP on TrueFoundry and keep policy gateway there | One deployment/policy plane, versioned rollout, secrets, probes and metrics close to gateway | Requires OAuth callback/domain migration and session-scaling validation | **Selected** |
| Replace source OAuth/RBAC with gateway-only authorization | Fewer auth hops | High security regression risk; gateway tool permissions cannot enforce student/campus row scope | Rejected |
| Use a shared Virtual Account Token for all people | Simple client integration | Collapses individual identity and defeats attribution/student isolation | Rejected |

### Consequences

- The TrueFoundry Service becomes the production runtime for `moodle-mcp` after canary and cutover.
- The source MCP remains responsible for who the person is allowed to query.
- Interactive people retain per-user OAuth. A TrueFoundry Virtual Account Token is reserved for `moodle-agent` and other machine callers.
- End users receive only the student or staff Virtual MCP URL, never the source-service URL.
- The existing Render/Cloudflare MCP remains available only for a time-boxed rollback window, then is retired.

## Exact Integration Points

### 1. TrueFoundry Service Deployment: `moodle-mcp`

Create separate TrueFoundry workspaces for staging and production. A workspace determines the target cluster/environment, so environments must not share mutable secrets or endpoints. TrueFoundry's service guide supports GitHub or local source; use GitHub for reproducible production builds. See [Deploy Your First Service](https://www.truefoundry.com/docs/deploy-first-service) and [GitHub integration setup](https://www.truefoundry.com/docs/github-integration-set-up).

Initial service settings:

| Setting | Staging | Production |
| --- | --- | --- |
| Service name | `jaipuria-moodle-mcp-staging` | `jaipuria-moodle-mcp-prod` |
| Source | GitHub release branch/SHA | Same promoted, immutable SHA |
| Build context | repository root `./` | repository root `./` |
| Build method | existing `Dockerfile` | same image promoted from staging |
| Runtime command | Dockerfile command: `uvicorn server:app --host 0.0.0.0 --port 8000` | same |
| Port | `8000`, TCP, app protocol `http` | same |
| MCP path | `/mcp` | `/mcp` |
| Liveness | existing `/health` initially | `/livez` after probe split |
| Readiness | add `/readyz` | required before production cutover |
| Replica count | 1 until session tests pass | minimum 2 only after stateless/session gate passes |

The current Dockerfile already binds to `0.0.0.0`, runs as a non-root user, and defaults to port 8000. Set `PORT=8000` explicitly in the TrueFoundry service so the port and probe definitions cannot drift.

#### Endpoint exposure decision

The generic MCP deployment example recommends `expose: false`. That cannot be copied blindly here because the current Moodle MCP also hosts browser-reachable OAuth endpoints (`/register`, `/authorize`, `/token`, and `/auth/callback`). Google and the user's browser must reach those paths.

Use this staged design:

1. Expose one TLS domain for the service during migration.
2. Keep the OAuth/discovery paths public and protected by the application's OAuth/DCR/PKCE controls.
3. Protect `/mcp` with the existing `GATEWAY_ENFORCED` and `x-mcp-gateway-secret` boundary.
4. Configure the matching secret in TrueFoundry Secret Manager and inject it as an additional upstream header from the MCP Gateway.
5. Do not add blanket TrueFoundry port authentication that would intercept the OAuth discovery/callback flow.
6. After cutover, evaluate split ingress: a public OAuth route plus an internal/private `/mcp` route. Make this a later hardening change, not a pre-launch refactor.

TrueFoundry documents that an unexposed service is cluster-only and an exposed service receives a routed HTTPS endpoint. See [ports and domains](https://www.truefoundry.com/docs/define-ports-and-domains). The application-level path gate is necessary because this service has mixed public OAuth and private MCP paths.

#### Health and scaling gate

Add separate probes:

- `/livez`: process event loop is responsive; no downstream calls.
- `/readyz`: configuration loaded, OAuth store initialized, and a short cached connectivity check to required dependencies succeeds.

TrueFoundry uses liveness to restart failed replicas and readiness to stop routing to an unready replica. See [Liveness/Readiness Probe](https://www.truefoundry.com/docs/liveness-readiness-probe).

Do not autoscale immediately. The current FastMCP HTTP app can maintain session state in process. Before enabling two or more replicas, choose and prove one of these:

1. Preferred: run FastMCP with `stateless_http=True` and execute the full OAuth, `initialize`, `tools/list`, `tools/call`, progress, and report-job regression suite.
2. Temporary: verify end-to-end sticky routing using the MCP session header, while accepting that TrueFoundry documents sticky routing as best effort.

Only after that test should production use minimum two replicas and RPS/CPU autoscaling. Derive thresholds from a one-replica load test instead of guessing. See [Autoscaling](https://www.truefoundry.com/docs/autoscaling-overview) and [Sticky Routing](https://www.truefoundry.com/docs/sticky-routing).

### 2. TrueFoundry Secret Manager: Runtime Configuration

Put sensitive values in the secret manager and reference their `tfy-secret://...` FQNs in the service spec. TrueFoundry injects the values at runtime, keeping them out of Git and visible deployment configuration. See [Environment Variables and Secrets](https://www.truefoundry.com/docs/environment-variables-and-secrets).

Secret groups should be separated by purpose:

| Group | Values |
| --- | --- |
| `moodle-data` | `SUPABASE_DATA_KEY`, `SUPABASE_ANON_KEY` where required |
| `moodle-oauth` | `GOOGLE_OAUTH_CLIENT_SECRET`, `OAUTH_JWT_SIGNING_KEY`, `OAUTH_STORAGE_ENCRYPTION_KEY`, `SUPABASE_OAUTH_STORAGE_KEY` |
| `moodle-audit` | `SUPABASE_AUDIT_KEY`, `MCP_AUDIT_HMAC_KEY`, `MCP_PII_HMAC_KEY` |
| `moodle-gateway-origin` | `GATEWAY_SHARED_SECRET` |
| `moodle-cache` | `MCP_REDIS_URL` |
| `moodle-agent-link` | `AGENT_SHARED_SECRET`, access-request webhook secret if enabled |
| `report-agent-tfy` | TrueFoundry Virtual Account Token used by `moodle-agent` |

Non-secret settings such as environment name, campus list, allowed domain, RBAC mode, PII mode, rate limits, and public base URLs belong in versioned service configuration.

Rotation rules:

- OAuth signing and storage-encryption keys are stable keysets; rotate with overlap, not replacement.
- Gateway-origin, audit HMAC, PII HMAC, report-link, and agent HMAC credentials remain independent.
- Provider credentials stay in TrueFoundry model accounts, not in `moodle-agent` or `moodle-mcp`.
- Production and staging use different secrets.

### 3. TrueFoundry MCP Registry and Gateway

After the staging service is healthy, register it as a custom remote MCP server. TrueFoundry supports custom remote URLs plus API key, OAuth2, AWS SigV4, or token-passthrough outbound authentication. For this application, keep OAuth2 Authorization Code because the source server must retain the human identity. See [MCP Server Getting Started](https://www.truefoundry.com/docs/ai-gateway/mcp/mcp-server-getting-started) and [MCP Gateway Authentication and Security](https://www.truefoundry.com/docs/ai-gateway/mcp/mcp-gateway-auth-security).

Update `00-moodle-remote.yaml` as follows:

- Replace `https://moodle-mcp.rehearsal-os.app/mcp` with the TrueFoundry service `/mcp` URL.
- Replace authorization, token, and registration URLs with the TrueFoundry service domain.
- Keep TLS certificate verification enabled.
- Keep `enable_tools_by_default: false`.
- Keep the remote source with no end-user collaborators.
- Add the gateway-to-origin secret header using a TrueFoundry secret reference; never commit the value.
- Update `MCP_SERVER_BASE_URL`, `MCP_ALLOWED_HOSTS`, Google OAuth callback URI, and any DCR redirect allowlist to the new service domain.

Inbound identity choice:

- For interactive users, use TrueFoundry OAuth for TrueFoundry members or map the Jaipuria Google IdP token to TrueFoundry users/teams.
- For the gateway-to-Moodle hop, keep the custom per-user OAuth flow already defined by the remote manifest.
- Do not use a shared VAT for people. A VAT is appropriate for a service-to-service caller where all calls intentionally have one application identity.

TrueFoundry explicitly separates inbound gateway authentication, access control, and outbound MCP authentication. That is the correct mental model for this deployment.

### 4. Curated Virtual MCP Surfaces

Apply the existing Virtual MCP manifests from canonical `origin/main`:

- `jaipuria-moodle-student`: only reviewed self-service tools.
- `jaipuria-moodle-staff`: reviewed analytics/report tools for named authorized staff.
- Best Effort remains off so a source failure fails the request rather than silently returning a partial capability set.
- Publish only these two gateway URLs to Jaipuria OS and other clients.

Virtual MCP servers combine selected tools without another service deployment. Their tool names receive stable suffixes, so approval policies must use the final encoded name visible after creation. See [Virtual MCP Server](https://www.truefoundry.com/docs/ai-gateway/mcp/virtual-mcp-server).

### 5. MCP Authorization, Guardrails, Limits, and Approval

Apply the checked-in desired state in this order:

1. Remote MCP source.
2. Student and staff Virtual MCP servers.
3. Per-user and report-specific rate limits.
4. Gateway data-access rules.
5. Cedar authorization group.
6. PII, secret-detection, and prompt-injection groups.
7. Guardrail binding policy.
8. Human approval for the encoded staff `create_report` tool.

Policy responsibilities:

| Layer | Responsibility |
| --- | --- |
| Virtual MCP allowlist | Limits discoverable tools by persona |
| Cedar pre-tool guardrail | Default-deny user/server/tool entitlement |
| Source MCP RBAC | Student ownership, dean campus, full-access roster, uniform denials |
| Supabase RLS/read-only role | Final database boundary |
| Post-tool PII mutation | Redacts identity/contact/India-specific identifiers |
| Post-tool secret mutation | Redacts likely credentials |
| Post-tool prompt-injection validation | Blocks malicious instructions embedded in tool data |
| Rate-limit policies | Per-user abuse/cost controls |
| Human approval | Single-execution approval for staff report generation |

TrueFoundry guardrail policies can target MCP servers and tools and run before or after a tool call. Registering a guardrail alone does not apply it; the policy binding is required. See [Configure Guardrail Policies](https://www.truefoundry.com/docs/ai-gateway/guardrails-configuration). Tool approval can hold a call until an approver acts, and `count: 1` grants exactly one execution. See [MCP Tool Approvals](https://www.truefoundry.com/docs/ai-gateway/mcp/mcp-tool-approval).

Keep **Enable new tools by default** off. Newly added source tools must remain hidden until reviewed, tested, added to the correct virtual server, and covered by Cedar. See [MCP Tool Management](https://www.truefoundry.com/docs/ai-gateway/mcp/mcp-tool-management).

### 6. TrueFoundry AI Gateway: `moodle-agent`

The agent integration is already present in the working tree:

- `moodle-agent/app/config.py` selects the TrueFoundry base URL, key, and model in production and rejects partial/insecure configuration.
- `moodle-agent/app/onepager/build.py` posts to `/chat/completions`, attaches bilateral guardrails, supplies identity-free metadata, disables request logging and web search, and bounds retry behavior.
- `moodle-agent/render.yaml` requires the gateway and deliberately omits a direct provider credential.

Tenant work still required:

1. Create provider/model accounts in TrueFoundry; provider keys stay there.
2. Create a reviewed model or virtual model for the report workload with an approved primary and fallback.
3. Create a dedicated Virtual Account owned by the report platform team.
4. Grant it access only to the report model and required guardrails.
5. Store its VAT as `TRUEFOUNDRY_API_KEY` in the report service's secret store.
6. Set the exact gateway base URL and model ID from the Playground code snippet.
7. Add model-level rate, concurrency, and spend limits.
8. Verify direct OpenRouter/provider keys are absent from every production service.

TrueFoundry recommends PATs for development and Virtual Account Tokens for applications, and exposes an OpenAI-compatible base URL. See [AI Gateway Quick Start](https://www.truefoundry.com/docs/ai-gateway/quick-start) and [AI Gateway Access Control](https://www.truefoundry.com/docs/ai-gateway/gateway-access-control).

### 7. Privacy and Observability

For LLM report requests, preserve the current `X-TFY-LOGGING-CONFIG: {"enabled": false}` behavior unless the privacy owner approves a redacted logging rule. The header is the documented request-level control. See [Request Logging](https://www.truefoundry.com/docs/ai-gateway/request-logging).

For operational monitoring:

- Keep application audit records for actor, tool, scope, decision, latency, and result class.
- Do not log prompt bodies, tool-result bodies, detector evidence, raw student IDs, names, tokens, or report content.
- Allow people to inspect only their own traces.
- Allow teams to inspect aggregate application metrics, not teammates' personal traces.
- Reserve all-data trace access for tenant administrators/privacy investigators.
- Preserve the existing `gateway-data-access-config` manifest because TrueFoundry access rules are additive; broad defaults must be narrowed, not counteracted with another rule.

TrueFoundry recommends separating Metrics from Traces because traces can contain request/response content. See [Configure Data Access](https://www.truefoundry.com/docs/ai-gateway/data-access).

Create alerts for:

- pod crash loop or unhealthy pod;
- readiness failure;
- 5xx rate and MCP tool error rate;
- p95/p99 latency;
- OAuth/authentication failures;
- Cedar denials and guardrail blocks;
- PII/secret detector events without bodies;
- rate-limit utilization;
- report queue age/depth;
- TrueFoundry model error, latency, and spend budget.

TrueFoundry exposes deployment/pod logs, resource metrics, Kubernetes events, and alerting integrations. See [service monitoring](https://www.truefoundry.com/docs/monitor-your-service) and [alerts](https://www.truefoundry.com/docs/setup-alerts).

### 8. TrueFoundry Workflow: Optional Batch Orchestration

**Decision:** do not introduce a TrueFoundry Workflow on the interactive MCP path or
as a first-launch dependency. The optional scheduled campus/trimester Workflow is now
implemented and compiled, but remains undeployed until Flyte support, database migration,
staging retry/idempotency, and cutover gates pass. It does not replace individual MCP calls.

Why it can help there:

- the current shell runner executes scopes sequentially and deliberately isolates a failed scope with `|| echo`, which can make the overall cron appear successful;
- a Workflow provides a visible DAG, per-task logs/metrics/events, task retries, failure handlers, alerts, cron scheduling, and recovery from the failed node without rerunning completed work;
- a map task can run independent campus/batch/trimester scopes in parallel while keeping resource limits explicit;
- the existing Python 3.12 container is within the documented Python 3.10-3.12 SDK range.

Recommended initial DAG:

1. `preflight`: validate configuration, storage, TrueFoundry AI Gateway access, and required database migrations.
2. `select_snapshots`: resolve immutable promoted run IDs for the requested scopes.
3. `generate_scopes`: map over `{campus, batch, trimester, run_id}` and invoke the existing containerized `app.jobs.cli`/coordinator code.
4. `verify_outputs`: aggregate persisted `report_run_metrics`, enforce failure/manual-review thresholds, and verify object-store artifacts.
5. `publish_summary`: persist a run summary and emit completion/failure notification without including student data.

Keep these paths outside the Workflow:

- ordinary read-only Moodle MCP tools, because they must remain low-latency request/response calls;
- one-student `create_report`, which already uses an idempotent Supabase request table plus a continuously running private worker and polling endpoint;
- email delivery, which remains a separately authorized action rather than an automatic consequence of generation;
- row-level authorization, report-link authorization, and source audit, which remain application/database responsibilities.

Do not create a workflow task per student initially. Student IDs would become workflow inputs and appear in orchestration metadata/log surfaces, while thousands of task pods would add scheduling overhead. Keep per-student concurrency, caching, validation, and persistence inside the existing scope coordinator; map only over the small set of non-PII scope identifiers. Pass large artifacts through private object storage and return only object references and aggregate metrics.

Adoption gate:

- TrueFoundry's Flyte data-plane components are installed in the target cluster;
- a staging proof shows retry, failure alert, recovery, cancellation, idempotency, and no duplicate LLM spend;
- no names, emails, raw student IDs, marks, attendance, prompts, or report bodies appear in Workflow inputs, outputs, logs, or alerts;
- the Workflow reuses the same immutable image and core coordinator functions instead of creating a second report implementation;
- operations accepts the additional Flyte/SDK dependency and has a documented rollback to the existing cron job.

If these gates are not met, retain the existing cron for batch generation and improve its exit-code/alert handling. For the on-demand report queue, a TrueFoundry Async Service is a closer managed analogue than a Workflow because it is designed for queue-backed, long-running request processing and traffic bursts. Migrating that already durable Supabase queue is optional and should not happen during the first TrueFoundry cutover.

References: [Introduction to Workflow](https://www.truefoundry.com/docs/introduction-to-workflow), [Workflow Concepts](https://www.truefoundry.com/docs/workflow-concepts), [Interacting With Workflow](https://www.truefoundry.com/docs/interacting-with-workflow), [Recovering a Failed Workflow Execution](https://www.truefoundry.com/docs/recovering-workflow-execution), and [Introduction to Async Service](https://www.truefoundry.com/docs/introduction-to-async-service).

## Delivery Plan

### Phase 0: Reconcile and Freeze Source

**Owner:** engineering/release manager

- [ ] Start the MCP release branch from current `origin/main`, not the checked-out TrueFoundry PII feature branch.
- [ ] Port only reviewed local hardening that is not already on main.
- [ ] Land the current `moodle-agent` TrueFoundry and production-hardening changes through review.
- [ ] Record immutable release SHAs for both repositories.
- [ ] Re-run agent tests, MCP tests, dependency audit, static security scan, and `scripts/validate_truefoundry_gateway.py`.

**Exit gate:** clean reviewed SHAs; no `.env`, credentials, student artifacts, local databases, or generated reports in Git/build contexts.

### Phase 1: Prepare TrueFoundry Tenant

**Owner:** TrueFoundry tenant administrator/platform team

- [ ] Create isolated staging and production workspaces.
- [ ] Configure GitHub App access with repository-content read permission.
- [ ] Create least-privilege teams/roles for platform managers, MCP users, MCP approvers, privacy investigators, and report applications.
- [ ] Create secret groups and populate staging values.
- [ ] Create provider/model accounts and the report application Virtual Account.
- [ ] Confirm region, retention, data-routing, and subprocessors with the data/privacy owner.

**Exit gate:** no production secret is in source, CI logs, tickets, or shell history; staging account can build from the recorded SHA.

### Phase 2: Deploy Moodle MCP to Staging

**Owner:** platform + MCP engineering

- [ ] Create the TrueFoundry Service from GitHub with the existing Dockerfile.
- [ ] Commit the generated TrueFoundry service YAML to Git; treat Git as authoritative.
- [ ] Configure port 8000, HTTPS endpoint, `PORT=8000`, probes, resources, and staging secrets.
- [ ] Set staging `MCP_SERVER_BASE_URL` and allowed host to the TrueFoundry domain.
- [ ] Add the staging Google OAuth redirect URI.
- [ ] Verify `/health`, OAuth discovery, DCR, Google login, `initialize`, `tools/list`, and one safe `whoami` call.
- [ ] Confirm deployment logs contain no tokens, authorization codes, student identifiers, or request bodies.

**Exit gate:** `DEPLOY_SUCCESS`, green probes, reproducible build from the recorded SHA, and complete OAuth/MCP handshake.

### Phase 3: Apply Gateway Desired State in Staging

**Owner:** gateway/platform team

- [ ] Update the remote manifest URLs to the staging TrueFoundry service.
- [ ] Run `tfy apply --dir truefoundry/manifests --dry-run --show-diff` and archive the reviewed diff.
- [ ] Apply the source, virtual servers, limits, data-access rules, guardrail groups, and binding.
- [ ] Configure the gateway-origin secret header from Secret Manager.
- [ ] Verify the gateway can initialize the source before setting `GATEWAY_ENFORCED=true`.
- [ ] Confirm direct `/mcp` without the secret returns `403 gateway_required` while OAuth discovery still works.
- [ ] Add MCP Server Approvers and create a one-execution rule for the final encoded staff `create_report` tool name.

**Exit gate:** only virtual URLs are shared; source has no end-user collaborators; new tools default off; Best Effort off.

### Phase 4: Complete Staging Canary

**Owner:** QA + security + data/privacy

Use synthetic records first, then named consenting accounts.

| Test | Expected result |
| --- | --- |
| Tokenless gateway request | 401/authorization challenge |
| Direct source `/mcp` without gateway secret | 403 after enforcement |
| Student `get_student` for own record | One owned, pseudonymized result |
| Student requests peer record | Uniform denial; no peer data in logs/traces |
| Student requests cohort tool | Absent from `tools/list`; denied if forced |
| Campus dean queries own campus | Allowed |
| Campus dean queries another campus | Denied/empty at source boundary |
| Approved cross-campus staff | Allowed for all four campuses |
| Unlisted Jaipuria user | No data session |
| Student or pending user lists faculty tools | Tools absent or denied |
| Authorized staff lists faculty/course data | Campus scope enforced; no raw payload/source fields |
| Leadership reads faculty feedback | Allowed only within source-side campus grant |
| Authorized faculty name through PII guardrail | Matches the privacy-owner decision; no accidental redaction or disclosure |
| Staff `create_report` | Pending approval, then exactly one execution |
| More than 90 calls/minute | Rate-limited without body logging |
| PII/secret canary tool output | Redacted before client/model |
| Prompt-injection canary in Moodle content | Blocked before model consumption |
| Agent report generation | TrueFoundry route used; real name absent from provider payload |
| Audit reconciliation | TrueFoundry and Moodle audit agree on actor/tool/decision/scope |

**Exit gate:** all rows pass twice, including after one service restart and one deployment rollback rehearsal.

### Phase 5: Make the Runtime Production-Grade

**Owner:** MCP engineering/platform

- [ ] Add `/livez` and `/readyz`.
- [ ] Enable/test FastMCP stateless HTTP, or document a proven session-affinity design.
- [ ] Configure Redis and prove rate limits remain shared across two replicas.
- [ ] Load-test one replica; record sustainable RPS, concurrency, p95/p99, CPU, memory, and downstream Supabase limits.
- [ ] Set production requests/limits and autoscaling from evidence.
- [ ] Use at least two replicas only after the session test passes.
- [ ] Configure rolling update with readiness gates; use canary for security/auth changes.
- [ ] Configure production alerts and named notification/on-call targets.

TrueFoundry supports rolling, canary, and blue-green strategies. Use rolling updates for low-risk changes and a manual-pause canary for OAuth, authorization, guardrail, or tool-surface changes. See [Rollout Strategy](https://www.truefoundry.com/docs/rollout-strategy).

**Exit gate:** replica failure does not terminate a valid session or widen access; capacity and alert thresholds are evidenced.

### Phase 6: Production Promotion and Cutover

**Owner:** release manager with security/privacy approval

1. Promote the exact staging image/spec to the production workspace.
2. Supply production-only secrets and domain configuration.
3. Add the production OAuth callback URI and test with designated accounts.
4. Dry-run and apply the production gateway manifests.
5. Run the full canary matrix against production synthetic records.
6. Point Jaipuria OS/test clients to the student/staff Virtual MCP URLs.
7. Pilot with a small named cohort for one operating day.
8. Expand only after error, denial, latency, audit, guardrail, and queue metrics are reviewed.
9. Keep Render/Cloudflare unchanged but inaccessible to end users during the rollback window.
10. Retire the old runtime and DNS only after the rollback window closes.

TrueFoundry creates new service versions on update, retains version history for rollback, and supports promotion between workspaces. See [Update, Rollback, Promote](https://www.truefoundry.com/docs/update-rollback-promote-your-service).

### Phase 7: CI/CD and Drift Control

**Owner:** platform engineering

Repository additions:

- generated TrueFoundry service spec for staging/production overlays;
- existing gateway manifests from `origin/main`;
- static gateway validator;
- staging smoke/canary workflow;
- production promotion workflow with manual approval;
- deployment evidence artifact containing SHA, image digest, dry-run diff, canary result, and approver.

Pipeline:

1. Pull request: compile, unit/integration tests, manifest validator, dependency/security scans, Docker build.
2. Merge to main: TrueFoundry builds/deploys staging from the immutable SHA.
3. Staging: automated health/OAuth/MCP smoke plus manual persona canary.
4. Production: manual promotion of the exact tested image; do not rebuild from moving `main`.
5. Post-deploy: reconcile live service/gateway state against Git and archive evidence.

Keep the TrueFoundry YAML spec in Git and avoid ad hoc UI edits, because the next Git-driven deployment will overwrite them. The CI system needs a scoped `TFY_API_KEY`; TrueFoundry performs the build. See [Set Up CI/CD](https://www.truefoundry.com/docs/setting-up-cicd-for-your-service).

## Rollback Plan

Rollback triggers:

- any cross-student or cross-campus disclosure;
- source/gateway audit disagreement;
- OAuth loops, token mix-ups, or loss of individual attribution;
- guardrail bypass or raw PII/secret exposure;
- readiness failure or sustained 5xx/latency breach;
- report job executed without required approval;
- session loss after scale/deploy;
- provider bypass outside TrueFoundry.

Rollback sequence:

1. Remove end-user collaborators from the affected Virtual MCP servers or disable their publication.
2. Roll back the TrueFoundry service to the previous known-good version.
3. If the TrueFoundry runtime itself is the cause, temporarily repoint the remote registry entry to the recorded old Cloudflare origin and re-run the canary before restoring clients.
4. Never publish the source origin directly as a fallback.
5. Disable `create_report`, keep mail/send switches off, and stop the report worker if report isolation is uncertain.
6. Preserve audit/traces and rotate only credentials suspected of compromise.
7. Re-open access only after the same failed canary passes and the gateway/source audit records reconcile.

## Definition of Done

TrueFoundry integration is complete only when all of the following are true:

- [ ] Moodle MCP is built and running from an immutable Git SHA on TrueFoundry.
- [ ] Production clients use only the two curated Virtual MCP endpoints.
- [ ] Interactive calls retain a unique human identity end to end.
- [ ] Source RBAC and Supabase RLS remain authoritative for row access.
- [ ] Direct `/mcp` access cannot bypass the gateway.
- [ ] New tools are disabled by default; Best Effort is off.
- [ ] Cedar, PII, secret, prompt-injection, rate-limit, and approval controls are enforced and tested.
- [ ] `moodle-agent` has only a scoped TrueFoundry VAT and no direct provider fallback.
- [ ] Request/result bodies and student PII are absent from TrueFoundry logs and general observability exports.
- [ ] Two-replica/session behavior, shared limits, capacity, probes, alerts, canary, and rollback are proven.
- [ ] Live TrueFoundry state matches the reviewed Git manifests.
- [ ] Engineering, platform, security/privacy, data, programme, and rollback owners have signed the production record.

## Immediate Next Actions

1. Reconcile both release branches and freeze SHAs.
2. Ask the TrueFoundry tenant administrator for staging workspace identifiers, control-plane URL, GitHub App access, and a scoped deployment credential.
3. Generate and commit the staging service YAML from the TrueFoundry UI using the existing Dockerfile.
4. Add `/readyz` and validate stateless FastMCP behavior before enabling multiple replicas.
5. Update a staging copy of `00-moodle-remote.yaml` to the TrueFoundry service domain.
6. Create staging secrets/model account/VAT, dry-run the gateway manifests, and execute the canary matrix.

The detailed production gate and broader Moodle cutover checklist remain in `outputs/MOODLE_PRODUCTION_READINESS_PLAN_2026-10-13.md`; this document is the TrueFoundry-specific implementation path.

---

## Live Execution Log — 2026-10-13 (session receipt)

This log records what was *actually executed* against the live tenant and repos in this
session, separate from the plan above. "Live" means a control-plane read-back confirmed it.

### Phase 0 — Reconcile and Freeze Source — COMPLETE ✅
- Immutable SHAs frozen: `origin/main` **0f79760**, PR #21 (faculty tools) **273d742**, PR #22 (retire hosted-APM backend) **6dab77d**.
- `git diff --check`: clean on both branches.
- `scripts/validate_truefoundry_gateway.py`: **valid** on both branches.
- No `.env`/secret files tracked in either branch.
- MCP test suites: **30 passed, 0 failed**.
- `moodle-agent` offline suite: **197 passed, 11 skipped**.
- Open Phase 0 item: PRs #21 and #22 still need review+merge by rajikapatel01 (cannot be done from here).

### TrueFoundry tenant — executed live in the browser (as `ailabs2`, Pro)
- **Model Gateway → Providers:** OpenRouter provider account added; 2 models enabled
  (`openrouter/google-gemini-flash-latest`, `openrouter/deepseek-v4-flash-latest`); Test Credentials **2 passed**.
  Gateway base URL: `https://gateway.truefoundry.ai`.
- **Playground:** one real chat-completion ran through the gateway with the `moodle-pii` input guardrail attached.
  The guardrail **mutated** a test email + phone to asterisks before the model saw them.
- **Metrics dashboard now non-empty:** Model Metrics = 1 call / $0.002 / 0 errors; Guardrail Metrics = `moodle-pii/pii-redaction` 1 (100% mutated).
- **MCP Gateway → Registry:** source server **`jaipuria-moodle-prod`** registered (Remote, OAuth2 Authorization Code).
  TrueFoundry auto-discovered our `/authorize`, `/token`, `/register` via DCR (no client secret); PKCE S256.

### Known issues / blockers surfaced this session
- **Connect → "Authorization session mismatch":** transient OAuth state/session mismatch. First remediation: retry Connect
  in the same tab while already signed into Google. If it persists, enable cross-client PKCE in prod
  (`OAUTH_ALLOW_CROSS_CLIENT_PKCE=true` **and** `OAUTH_REDIRECT_HOSTS=gradelessai.truefoundry.cloud` — the code requires both)
  then redeploy. Prod currently has `OAUTH_ALLOW_CROSS_CLIENT_PKCE=false`.
- **Classifier-gated for the assistant** (user must click): all tenant *submit* actions (Add Server, Connect/OAuth consent,
  Create Virtual MCP, Save), all secret entry (OpenRouter key, gateway shared secret), and anything requiring the `tfy` CLI.
- **Credential hygiene:** the OpenRouter key `sk-or-v1-4a42…` was confirmed live via Test Credentials and appeared in chat — **rotate it**.

### Not yet executed (needs `tfy` CLI / tenant-admin / deploy auth — out of browser scope)
- `tfy apply` of the Virtual MCPs (student/staff), Cedar/PII/secrets/prompt-injection guardrail bindings, and rate limits.
- Deploying `moodle-mcp` as a TrueFoundry **Service** (workspace + GitHub App + deploy credential).
- Setting `GATEWAY_ENFORCED=true` + installing `x-mcp-gateway-secret` / Cloudflare `GATEWAY_SHARED_SECRET`.
- `moodle-agent` Virtual Account Token wiring (`TRUEFOUNDRY_API_KEY`).
- Supabase migrations `0016` (workflow audit) and `0017` (grant MCP role faculty-view access — action-time approval required).
