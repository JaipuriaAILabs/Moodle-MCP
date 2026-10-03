# New Relic alerts — `jaipuria-moodle-mcp` (go-live)

Alert policy for the Moodle MCP at the 500→5000 ramp. Backend: **New Relic EU, account 8495484**
(`one.eu.newrelic.com`). All signals carry `service.name = jaipuria-moodle-mcp`.

> **How to create:** New Relic → **Alerts → Alert conditions → + New condition → NRQL**. Paste the
> query, set the threshold below, attach a **workflow → destination** (email / Slack) so it actually
> pages someone. Validate each NRQL in the **Query builder** first (it autocompletes the metric names).
> Or create them programmatically via NerdGraph (`alertsNrqlConditionStaticCreate`) with a User API
> key — I can generate the mutations if you'd rather script it.

Telemetry contract (from `telemetry.py`):
- Metrics: `mcp.tool.calls`, `mcp.tool.duration` (ms histogram), `mcp.auth.calls`; dims
  `mcp.tool`, `mcp.outcome` (`success`|`failure`), `mcp.error_code`, `mcp.campus_scope`,
  `mcp.role`, `mcp.rbac_shadow_outcome`, `mcp.auth.mode`. Continuous system/process metrics also
  flow (via SystemMetricsInstrumentor). Scope is bounded to `all|scoped|none`; shadow outcome to
  `allow|deny|pending|error|not_applicable`.
- Spans: `mcp.tool.<name>`, `mcp.auth`.

---

## 1. MCP down / not exporting (CRITICAL) — loss of signal
Process/system metrics export every interval regardless of traffic, so a full stop = the app is down
or can't reach New Relic.
```sql
FROM Metric SELECT count(*) WHERE `service.name` = 'jaipuria-moodle-mcp'
```
- **Type:** enable **Loss of signal** → open a **critical** incident after **signal is lost for 5 min**.
- Optional static threshold off; this condition is purely loss-of-signal.

## 2. Health endpoint down (CRITICAL) — Synthetics
Truer uptime check than telemetry (catches Cloudflare/Render edge failures too).
- **Synthetics → Create monitor → Ping (or Simple Browser)** → URL `https://moodle-mcp.tryrehearsal.ai/health`,
  every **1 min**, from 2–3 locations. Alert if **≥ 2 locations fail** (avoids single-POP flakiness).

## 3. Auth failure surge (HIGH) — connector breakage / key mishap
Catches a bad deploy, a botched key rotation, or Cloudflare injecting failures. Uses a ratio so normal
token churn doesn't page.
```sql
FROM Metric SELECT percentage(sum(`mcp.auth.calls`), WHERE `mcp.outcome` = 'failure')
WHERE `service.name` = 'jaipuria-moodle-mcp'
```
- **Threshold:** **> 60% for 10 min** (critical). Sustained majority-failure = something is broken,
  not just clients re-registering. Revisit the number after a week of baseline.

## 4. Tool error rate (HIGH) — reliability
```sql
FROM Metric SELECT percentage(sum(`mcp.tool.calls`), WHERE `mcp.outcome` = 'failure')
WHERE `service.name` = 'jaipuria-moodle-mcp'
```
- **Threshold:** **> 20% for 5 min** (critical); optional warning at **> 10% for 10 min**.
- Useful companion (not an alert, for triage): `FACET mcp.tool, mcp.error_code`.

## 5. Tool latency p95 (WARNING) — performance
`create_report` is intentionally slower (calls the agent backend), so exclude it from the read-path SLO.
```sql
FROM Metric SELECT percentile(`mcp.tool.duration`, 95)
WHERE `service.name` = 'jaipuria-moodle-mcp' AND `mcp.tool` != 'create_report'
```
- **Threshold:** **p95 > 5000 ms for 10 min** (warning). Add a separate, looser one for
  `create_report` (`mcp.tool = 'create_report'`, p95 > 30000 ms) if you want to watch report latency.

## 6. RBAC shadow would-deny (HIGH before enforce)

During the shadow soak, this catches verified users who would lose access at cutover:
```sql
FROM Metric SELECT sum(`mcp.tool.calls`)
WHERE `service.name` = 'jaipuria-moodle-mcp'
  AND `mcp.rbac_shadow_outcome` = 'deny'
```
- **Threshold:** `> 0 for 5 min` (warning during roster seeding; promote to critical before cutover).
- Triage with `FACET mcp.tool`; use the locked `mcp_audit` ledger to identify the pseudonymous actor.

---

## Notes
- **Notification workflow is what makes these real** — a condition with no destination pages no one.
  Point them at a channel the on-call actually watches.
- These thresholds are launch-day starting points. After ~1 week of traffic, tune #3/#4/#5 against the
  observed baseline (NR's condition editor shows the historical line under your threshold).
- Recording-delivery failures (`audit delivery failed`) are logged, not metered. If you want an alert
  on those, enable OTel **logs** export (`MCP_OTEL_LOGS=true`) and add a NRQL condition on
  `FROM Log SELECT count(*) WHERE message LIKE '%audit delivery failed%'`.
