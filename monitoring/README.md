# Monitoring & alerting — Moodle MCP

Observability now comes from three complementary planes (see `docs/OBSERVABILITY.md`):
**Cloudflare Workers Logs** (runtime + request/OAuth errors), an optional **generic OTLP**
export from the MCP (`telemetry.py`, gated on `MCP_OTEL_ENDPOINT` + `MCP_OTEL_HEADERS`),
and the **Supabase `mcp_audit` ledger** (durable per-call accountability). There is no
longer any hosted-APM account or vendor-specific alerting setup to maintain here.

## Uptime: external `/health` check
Keep an external uptime check on `https://moodle-mcp.tryrehearsal.ai/health` every ~5 min
(validate the body contains `ok`, treat redirects as failure) and alert on failure. This
catches the failure modes that have actually bitten us — the service being down / spun
down and boot failures — without instrumenting the app. A ping cron is tracked in
`sql/2026-10-04_cron_schedules.sql`; Cloudflare Workers also surface request/error rates
for the `/health` route directly.

## Per-tool APM (once OTLP export is configured)
When `MCP_OTEL_ENDPOINT` + `MCP_OTEL_HEADERS` are set, the MCP ships an OTel exporter
(`telemetry.py`) that emits one SERVER span per tool call — `mcp.tool.<name>`,
`service.name='jaipuria-moodle-mcp'`, attributes `mcp.tool` / `mcp.outcome` /
`mcp.error_code` / `mcp.campus_scope`, ERROR status on failure. Express these alert
conditions against whatever OTLP backend you point the exporter at:

| Condition | Signal | Threshold |
|-----------|--------|-----------|
| error-rate spike | % of `mcp.tool.*` spans with ERROR status | > 5% for 5m |
| p95 tool latency | p95 duration of `mcp.tool.*` spans | > 3000 ms for 5m |
| throughput floor / silence | rate of `mcp.tool.*` spans | no data 10m |
| auth-denial surge | count of `mcp.auth` spans with `mcp.outcome='failure'` | > N for 5m |
| auth p95 latency | p95 duration of `mcp.auth` spans | > 1500 ms for 5m (Google tokeninfo per request) |

## Host / process metrics + app counters (once OTLP export is configured)
Beyond spans, the MCP exports **metrics** (on by default; `MCP_OTEL_METRICS=false` to
disable) so you can see saturation that spans don't show — essential for judging 5k
capacity:
- **Host + process gauges** via system-metrics instrumentation — `process.runtime.cpu.*`,
  `process.runtime.memory` / RSS, open file descriptors, etc. (needs
  `opentelemetry-instrumentation-system-metrics`; absent → app counters still export).
- **App instruments** — `mcp.tool.calls` (counter, dims `mcp.tool`/`mcp.outcome`/
  `mcp.error_code`/`mcp.campus_scope`), `mcp.tool.duration` (histogram, seconds),
  `mcp.auth.calls` (counter, `mcp.auth.mode`/`mcp.outcome`). Aggregated, so unaffected by
  trace sampling — the reliable RED signal alongside spans.

Suggested metric conditions:

| Condition | Signal | Threshold |
|-----------|--------|-----------|
| memory pressure | latest `process.runtime.memory` | > 80% of instance RAM for 10m |
| tool error-rate (metric) | % of `mcp.tool.calls` with `mcp.outcome='failure'` | > 5% for 5m |
| per-instance isolation | facet any of the above by `service.instance.id` | spot one bad node |

## Logs (opt-in)
OTLP log forwarding is **OFF by default** (`MCP_OTEL_LOGS=true` to enable) so operational
logs aren't shipped until confirmed PII-free (httpx access-token URLs are already quieted
at boot). Day-to-day runtime logs are in **Cloudflare Workers Logs** regardless.

## Trace propagation, sampling & instance id
Incoming **W3C `traceparent`** is honoured, so once the client harness is OTel-instrumented a
harness→MCP call is a single distributed trace (today each MCP call is a clean root span).
**Sampling is upstream-resistant**: the `ParentBased` sampler keeps our SERVER spans at
`MCP_OTEL_SAMPLE_RATIO` (default 1.0 = all) even when a client/proxy propagates an
*unsampled* `traceparent` (`-00`) — otherwise OTel's default would inherit that "don't
sample" and silently drop our traces while metrics kept flowing. A *sampled* upstream is
always honoured so cross-service traces stay whole. Dial the ratio down only if span
volume bites at 5k; error/throughput **rates** survive in the `mcp.tool.*` metrics
regardless (metrics ignore trace sampling).
Every span/metric/log carries **`service.instance.id`** (`MCP_SERVICE_INSTANCE_ID`, else
hostname) — set it to the instance id to tell nodes apart under horizontal scale.

## Flush on deploy
Providers force-flush on graceful shutdown (ASGI lifespan hook + atexit), so a
redeploy/SIGTERM doesn't drop the last batch and under-count the signal around deploys.

## Usage analytics (from the audit ledger)
Per-user activity, most-queried students, tool-usage mix, adoption/DAU come from the
Supabase audit ledger — query `mcp_audit.v_activity` (see `docs/DATA_CAPTURE_PLAN.md`).
Keep raw student PII in that locked schema; do **not** mirror it into any telemetry
backend.
