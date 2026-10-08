# AIA-1012 — TrueFoundry-first MCP architecture

## Decision

Jaipuria OS remains the user-facing MCP harness. We do not deploy the proposed standalone
`rehearsal-mcp-gateway`, and we do not maintain a second model gateway. TrueFoundry is the managed
control plane for model traffic and MCP governance. Moodle MCP remains the source of truth for
identity-to-row authorization, and Supabase RLS remains the final database boundary.

The local AIA-1012 decision record pre-dates the current RBAC requirements. Its earlier uniform
institution-wide access assumption is superseded by this enforced matrix:

| Identity | Effective data scope |
| --- | --- |
| Signed-in student | only enrolment IDs mapped to that Google identity |
| Eight named full-access staff | all campuses |
| Four named deans | their own campus only |
| Any other identity | denied |

Gateway policy can narrow these grants but can never widen them.

## Request path and boundaries

```text
Google user -> Jaipuria OS -> TrueFoundry Virtual MCP -> Moodle MCP OAuth/RBAC -> Supabase RLS
                                      |                         |
                                      |                         +-- row scope + audit ledger
                                      +-- tool policy, Cedar, approvals, limits, post-tool guards

Moodle report job -> TrueFoundry AI Gateway -> reviewed virtual model -> provider
                         |
                         +-- bilateral PII, prompt-injection/secrets, routing, budgets, traces
```

Each human uses authorization-code OAuth with DCR and PKCE. A shared Virtual Account Token is used
only by the server-side report agent, never by interactive users.

## Feature disposition

| TrueFoundry capability | Production decision |
| --- | --- |
| Remote MCP registry | Manage the single production Moodle source |
| Virtual MCP servers | Separate curated student and authorized-staff tool surfaces |
| Tool discovery policy | New tools disabled by default; access-admin tools excluded |
| Cedar pre-tool guardrail | Default-deny reviewed tool matrix; source still owns row scope |
| PII / Prompt Injection / Secrets | Pre/post defence; in-server pseudonymization remains primary |
| Human approval | One-execution approval for staff `create_report` after encoded name exists |
| MCP rate limits | Per-user minute/hour caps plus a report-specific cap |
| Model limits and budgets | Dedicated report-agent VAT, enforced throughput, staged spend budget |
| Virtual model routing | Reviewed primary and fallback providers, bounded retries, no bypass |
| Trace feedback | Store only the feedback target ID; approved QA can attach a 1–5 rating |
| Data access rules | Own traces only; teammates see aggregate metrics, not each other's traces |
| OpenTelemetry export | Export body-free traces and metrics to the approved observability backend |
| Data routing | Route metadata-only telemetry to the approved region/retention destination |
| Ask TFY | Weekly coverage/drift review, with every proposed change code-reviewed |
| Exact/semantic cache | Disabled for student data; application cache is already identity-scoped |
| Shared human VAT | Prohibited because it collapses identity and defeats student isolation |
| Best Effort virtual MCP | Disabled so a missing protected source fails closed |
| OpenAPI-to-MCP duplicate | Not created; it would bypass the audited MCP authorization path |

## Activation gates

Repository manifests cover the remote server, both virtual servers, rate limits and trace access.
Tenant resources that need secrets, provider accounts or generated tool names cannot be safely
fabricated in Git. A tenant administrator must complete these after a dry run:

1. Attach the Cedar pre-tool and PII/Prompt Injection/Secrets post-tool policies.
2. Copy the exact encoded staff `create_report` tool name into the one-execution approval rule.
3. Configure the report virtual model with a reviewed secondary provider and SLA cutoff.
4. Connect separate body-free OpenTelemetry trace and metric exporters.
5. Configure data routing only to the approved region and retention destination.
6. Run the full student/dean/full-access/unlisted canary matrix before publishing proxy URLs.

No client cutover is complete until TrueFoundry decisions and the Moodle audit ledger agree on actor,
tool, decision and scope for every canary.
