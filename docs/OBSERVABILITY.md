# Moodle MCP — Observability

**The hosted third-party APM backend has been retired.** MCP observability now comes from three
complementary planes, none of which requires the server to ship telemetry to a third-party APM:

## 1. TrueFoundry MCP Gateway — tool traffic analytics
When clients reach the MCP through the TrueFoundry MCP Gateway (the student/staff Virtual MCP URLs),
TrueFoundry *observes the proxied traffic* and populates its **Metrics Dashboard** automatically — no
app-side exporter is involved. Visible there:
- **MCP Metrics → Servers / Tools**: requests/sec, P50/P75/P90/P99 latency, failure rate by error type,
  `tools/list` vs `tools/call` breakdown, Top Tools, Top MCP Servers, per-user / per-virtual-account usage.
- **Guardrail Metrics**: Cedar / PII / secrets / prompt-injection evaluations, blocked vs mutated counts.
- **Routing / Rate-limit Metrics**: per-user and per-report (`create_report`) limit checks and exceeded rates.

This data appears **only once traffic flows through the gateway** — i.e. after `tfy apply` of the
`truefoundry/manifests`, `GATEWAY_ENFORCED=true` (+ the `x-mcp-gateway-secret` / `GATEWAY_SHARED_SECRET`
pair), and clients are pointed at the Virtual MCP URLs. Direct-to-origin `/mcp` is blocked once enforced,
so all tool traffic is captured.

LLM/report metrics (tokens, cost, latency) appear under **Model Metrics** once `moodle-agent`'s generation
calls go through the TrueFoundry AI Gateway (its Virtual Account Token + a reviewed model route).

## 2. Cloudflare Workers analytics — runtime
The MCP runs on Cloudflare (`moodle-mcp.rehearsal-os.app`). Cloudflare's own dashboard provides request
volume, status-code/error rates, CPU time, and latency for the Worker/Container at the edge — the runtime
health signal that does not depend on the gateway.

## 3. Supabase `mcp_audit` ledger — the governance record
The authoritative who-did-what: actor (HMAC-pseudonymous subject), tool, campus scope, decision, latency,
and result class, with 180-day retention. This is the DPDP/attribution record and is independent of both
planes above. Result/argument bodies and raw student PII are **not** logged unless the `MCP_CAPTURE_*`
flags are explicitly enabled.

## Generic OTLP hook (optional, off by default)
`telemetry.py` retains a backend-agnostic OTLP/HTTP exporter for traces/metrics/logs. It is **disabled
unless BOTH `MCP_OTEL_ENDPOINT` and `MCP_OTEL_HEADERS` are set** — so a future OpenTelemetry collector can
be wired with env only, no code change. There is no default hosted-APM endpoint or license-key path anymore.

## Removing the retired backend's footprint (ops)
The leftover license-key Cloudflare Worker **secret** from the retired APM backend is now unused by the
code. Delete it for hygiene (run against the prod + staging envs):

```
wrangler secret list                 # find any leftover license-key secret from the retired backend
wrangler secret delete <secret-name>
```
