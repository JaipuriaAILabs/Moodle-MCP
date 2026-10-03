# Monitoring & alerting — Moodle MCP

New Relic **EU**, account **8495484** (the MCP's own observability account). NerdGraph
endpoint: `https://api.eu.newrelic.com/graphql`. (jaipuria-os has its own, separate
observability — Cloudflare Workers Observability — so there is no cross-account NR join to
maintain here.)

## Available now (no MCP change): external uptime
`newrelic_uptime_alert.sh` provisions, via NerdGraph:
- a **Synthetics ping monitor** on `https://moodle-mcp.tryrehearsal.ai/health` every 5 min
  (validates the body contains `ok`, treats redirects as failure),
- an **alert policy** "Moodle MCP",
- a **condition** that fires when the check FAILS,
- an **email workflow** to `ALERT_EMAIL`.

```bash
NEW_RELIC_USER_API_KEY='NRAK-...' ALERT_EMAIL='ops@jaipuria.ac.in' \
  bash monitoring/newrelic_uptime_alert.sh
```
This catches the failure modes that have actually bitten us — the service being down /
spun down (free-plan cold starts) and boot failures — without instrumenting the app.

## Per-tool APM (once NEW_RELIC_LICENSE_KEY is set)
The MCP now ships an OTel exporter (`telemetry.py`) that emits one SERVER span per tool
call — `mcp.tool.<name>`, `service.name='jaipuria-moodle-mcp'`, attributes `mcp.tool` /
`mcp.outcome` / `mcp.error_code` / `mcp.campus_scope`, ERROR status on failure. Set
`NEW_RELIC_LICENSE_KEY` in Render to turn it on, then add these NRQL conditions to the
"Moodle MCP" policy (inert until spans arrive):

| Condition | NRQL | Threshold |
|-----------|------|-----------|
| error-rate spike | `SELECT percentage(count(*), WHERE otel.status_code='ERROR') FROM Span WHERE service.name = 'jaipuria-moodle-mcp' AND name LIKE 'mcp.tool.%'` | > 5% for 5m |
| p95 tool latency | `SELECT percentile(duration.ms, 95) FROM Span WHERE service.name = 'jaipuria-moodle-mcp' AND name LIKE 'mcp.tool.%'` | > 3000 ms for 5m |
| throughput floor / silence | `SELECT rate(count(*), 1 minute) FROM Span WHERE service.name = 'jaipuria-moodle-mcp' AND name LIKE 'mcp.tool.%'` | no data 10m |
| auth-denial surge | `SELECT count(*) FROM Span WHERE service.name = 'jaipuria-moodle-mcp' AND name = 'mcp.auth' AND mcp.outcome = 'failure'` | > N for 5m |
| auth p95 latency | `SELECT percentile(duration.ms, 95) FROM Span WHERE service.name = 'jaipuria-moodle-mcp' AND name = 'mcp.auth'` | > 1500 ms for 5m (Google tokeninfo per request) |

## Host / process metrics + app counters (once NEW_RELIC_LICENSE_KEY is set)
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

| Condition | NRQL | Threshold |
|-----------|------|-----------|
| memory pressure | `SELECT latest(process.runtime.memory) FROM Metric WHERE service.name = 'jaipuria-moodle-mcp'` | > 80% of instance RAM for 10m |
| tool error-rate (metric) | `SELECT percentage(sum(mcp.tool.calls), WHERE mcp.outcome='failure') FROM Metric WHERE service.name = 'jaipuria-moodle-mcp'` | > 5% for 5m |
| per-instance isolation | facet any of the above `FACET service.instance.id` | spot one bad node |

## Logs (opt-in)
Log forwarding is **OFF by default** (`MCP_OTEL_LOGS=true` to enable) so operational
logs aren't shipped to NR until confirmed PII-free (httpx access-token URLs are already
quieted at boot). When on, root-logger records flow to NR Logs, correlated to traces.

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
hostname) — set it to the Render instance id to tell nodes apart under horizontal scale.

## Flush on deploy
Providers force-flush on graceful shutdown (ASGI lifespan hook + atexit), so a
redeploy/SIGTERM doesn't drop the last batch and under-count the signal around deploys.

## Usage analytics (from the audit ledger, not NR)
Per-user activity, most-queried students, tool-usage mix, adoption/DAU come from the
Supabase audit ledger — query `mcp_audit.v_activity` (see `docs/DATA_CAPTURE_PLAN.md`).
Keep raw student PII in that locked schema; do **not** mirror it into New Relic.

> NerdGraph mutation shapes drift between NR releases; if the script errors, the printed
> response names the offending field. Re-running creates duplicates — it's a one-time
> bootstrap; manage changes in the NR UI afterward.
