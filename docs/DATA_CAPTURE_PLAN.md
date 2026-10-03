# MCP activity capture — recording what users do with the Moodle MCP

Owner: Mansi · Epic: AIA-1012 · Status: Phase 1–2 implemented (flag-gated, off by default)

Goal: capture everything users do through the Moodle MCP once it is exposed to
~5,000 Jaipuria students + professors via the client harness, for adoption analytics, support,
and security review. Access stays open to every verified Jaipuria ID (data-owner
decision, 2026-09-14) — so capture, not access control, is the lever here.

## Two capture planes

| Plane | What it holds | Where it lives |
|-------|---------------|----------------|
| **A. MCP tool activity** | which tool, arguments (which student), result, timing, identity, client/IP | our `mcp_audit` schema — **ours to enrich (this doc)** |
| **B. Conversation context** | the user's actual prompts, model replies, tokens, files, cost | **the harness logs it** (on whichever client is in use) |

The harness runs the agent loop, so its agent-run records already hold our tool calls +
results keyed to the real user — a second, identity-linked copy of Plane A. The
value is correlating the two on a shared `x-request-id` / email.

## What existed before

Every tool call funnels through one chokepoint — `GuardMiddleware.on_call_tool`
(`security.py`) → `audit_store.record_tool_call` → RPC `record_mcp_tool_call` →
partitioned `mcp_audit.tool_calls` (+ `users`/`sessions`/`clients`, 180-day purge).
It stored, per call: tool, outcome, error_code, duration_ms, campus_scope,
request_id, and **HMAC-pseudonymised** user/session/client. It deliberately dropped
identity, arguments, results and IP. The MCP emits **no OTel/New Relic** — Supabase
+ stderr is the only sink.

## What was added (this change — flag-gated, all default OFF)

`build_metadata()` in `audit_store.py` enriches the existing `metadata` jsonb
(already stored by the RPC — no schema change needed to write it). Gated by:

| Flag (env) | Adds to `metadata` |
|------------|--------------------|
| `MCP_CAPTURE_IDENTITY` | `identity.{email,name,campuses}` (real, from the Google OAuth principal) |
| `MCP_CAPTURE_ARGUMENTS` | `arguments` — the full tool call (which student/name/campus/offset), size-capped |
| `MCP_CAPTURE_RESULTS` | `result` — summary of the returned payload (report_url, from_cache, rows…), size-capped |
| `MCP_CAPTURE_CLIENT_IP` | `source_ip` — first hop of X-Forwarded-For |
| `MCP_CAPTURE_ARGS_MAX_BYTES` / `MCP_CAPTURE_RESULT_MAX_BYTES` | truncation caps (default 4096 / 8192) |

With every flag off the metadata is byte-identical to before (`{server_version}`),
so **enabling audit alone changes nothing** — the widening is an explicit, reversible
switch. Capture is defensive: it never raises and never changes a tool outcome.
Verified by `tests/test_activity_capture.py` (17 checks: off-preserves-privacy,
on-captures, selective, size-caps, robustness).

Query ergonomics: `sql/2026-09-14_mcp_activity_capture.sql` (drafted, **not applied**)
adds a GIN index on `metadata` and a flattened `mcp_audit.v_activity` view. Writing
the data needs no migration — only this convenience layer does.

## Roadmap

- **Phase 1 — turn on & widen (config):** confirm `SUPABASE_AUDIT_KEY` + `MCP_AUDIT_HMAC_KEY`
  are set (with `MCP_REQUIRE_AUDIT=true` the server won't boot otherwise); flip the
  `MCP_CAPTURE_*` flags per the governance decision below. *(Render env edits are a
  paid/dashboard action — ops.)*
- **Phase 2 — rich capture (code):** done — this change. Apply the migration when the
  query layer is wanted.
- **Phase 3 — identity spine + harness correlation:** thread one `x-request-id` from a
  harness turn → MCP call so Plane A/B join; confirm the harness's own message + agent-run
  retention and `MONGO_SYNC`.
- **Phase 4 — live telemetry:** add OpenTelemetry to the MCP → New Relic EU (8495484):
  per-tool latency/throughput/error-rate, active users, tool-usage mix, alerts;
  `trace_id` join to the gateway/harness.
- **Phase 5 — analytics surface:** views/dashboard — per-user activity timeline,
  most-queried students, tool-usage funnel, adoption/DAU, error hotspots.

## Governance checkpoint (decide before flipping identity/arguments/results ON)

These flags record real identity + which student records each person viewed + the
marks/attendance returned — a reversal of the privacy-first default (HMAC, no args,
no payloads, `consents` table, 180-day purge). The data owner has authorised broad
collection; to keep it defensible:

1. **Privacy notice** — state that activity + queried-record identifiers are logged.
2. **Keep raw identity/PII in the locked `mcp_audit` schema only — never mirror it to
   New Relic in clear** (NR stays pseudonymised/aggregate).
3. **Retention** — keep the 180-day purge, or scrub payloads sooner while keeping the
   event (example in the migration).
