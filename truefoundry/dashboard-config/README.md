# TrueFoundry dashboard-applied config (not `tfy apply`-able)

These manifests are **valid per the TrueFoundry MCP rate-limiting docs** but the `tfy` CLI
(v0.17.5) rejects their type:

```
Failed to apply manifest ... of type tenant-rate-limit-config/mcp.
Error: Invalid type tenant-rate-limit-config in the manifest.
```

So they are **applied in the dashboard** (AI Gateway → Config / Rate Limiting), or via a newer CLI
when available. Keeping them out of `truefoundry/manifests/` lets `tfy apply -d truefoundry/manifests`
run clean (the whole gateway config applies in one shot).

| File | Intent |
| --- | --- |
| `30-moodle-user-rate-limit.yaml` | Per-user cap on `jaipuria-moodle-prod`: 90 tool calls/min, 2000/hr. |
| `31-moodle-report-rate-limit.yaml` | Per-user cap on `jaipuria-moodle-prod:create_report`: 60/hr. |

These are **defense-in-depth** — the MCP already rate-limits in-app (`MCP_IP_RATE_LIMIT=1200`,
`TransportGuard`). Schema reference: TrueFoundry docs — MCP rate limits use `type:
tenant-rate-limit-config/mcp`, `when.mcp_servers.in: [server]` or `[server:tool]`, `limits.tool_calls_per_*`,
`applies_to.type: per-user`, `mode: enforce`.
