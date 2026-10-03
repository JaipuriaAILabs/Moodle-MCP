# AIA-1391 — Moodle MCP → Cloudflare (Containers) execution runbook

**Decision (recorded on AIA-1391):** Python Workers/Pyodide is infeasible (fastmcp/uvicorn/ASGI +
cryptography/otel/supabase-py + module-level state). **Recommended: Cloudflare Containers** — run
the existing Python app as-is in a container fronted by a small Worker, in the same CF account as
Jaipuria OS. ~Zero app rewrite (the `Dockerfile` already exists). TS port stays a later option only
if the container cold-start can't meet the <2s `initialize` criterion.

> **Blocked until:** Rajika confirms Containers-vs-TS, grants the Cloudflare account
> (`ailabs@jaipuria.ac.in`, id `c945945018f732e5607a331ef73c7d75`) + Render dashboard access, and
> confirms the **Workers Paid plan** is active (Containers + Dynamic Workers require it — error 10195).
> This runbook is execute-only once those land. Verify each snippet against the current Cloudflare
> Containers docs at deploy time (the API is young and moves).

## 0. Prereqs
- `npm i -g wrangler` (latest), `wrangler login` into the ailabs account.
- Confirm Workers Paid plan is on.
- The existing `Dockerfile` (`python:3.12-slim`, `uvicorn server:app --port ${PORT:-8000}`) is reused; the container will listen on **8080** (set below) to match the Worker's `defaultPort`.

## 1. Files to add (in a `cloudflare/` subdir or a sibling deploy repo)

**`Dockerfile` change** — make the port explicit for Containers:
```dockerfile
# ...existing python:3.12-slim build...
EXPOSE 8080
CMD ["sh", "-c", "exec uvicorn server:app --host 0.0.0.0 --port ${PORT:-8080}"]
```

**`wrangler.jsonc`:**
```jsonc
{
  "name": "jaipuria-os-moodle-mcp",
  "main": "src/index.ts",
  "compatibility_date": "2026-09-01",
  "containers": [
    { "class_name": "MoodleMcp", "image": "../Dockerfile", "instance_type": "standard", "max_instances": 3 }
  ],
  "durable_objects": { "bindings": [ { "name": "MOODLE_MCP", "class_name": "MoodleMcp" } ] },
  "migrations": [ { "tag": "v1", "new_sqlite_classes": ["MoodleMcp"] } ]
}
```

**`src/index.ts`** — the minimal Worker + Container DO that proxies every request to the Python app:
```ts
import { Container, getContainer } from "@cloudflare/containers";

export class MoodleMcp extends Container {
  defaultPort = 8080;      // matches EXPOSE/uvicorn above
  sleepAfter = "20m";      // keep warm-ish to protect the <2s cold-start criterion
}

export default {
  async fetch(req: Request, env: Env): Promise<Response> {
    // single logical instance keeps streamable-HTTP sessions + in-process rate limiter coherent
    return getContainer(env.MOODLE_MCP, "singleton").fetch(req);
  },
} satisfies ExportedHandler<Env>;
```
`npm i @cloudflare/containers`. (`Env` has `MOODLE_MCP: DurableObjectNamespace`.)

## 2. Secrets (Render env → `wrangler secret put`, never in wrangler.jsonc)
Move every runtime var. Secrets via `wrangler secret put NAME`; non-secret flags can go in `vars`.
- Data/OAuth/audit: `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_DATA_KEY` (reporting_readonly), `SUPABASE_OAUTH_STORAGE_KEY`, `SUPABASE_AUDIT_KEY`, `MCP_AUDIT_HMAC_KEY`
- OAuth: `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET`, `OAUTH_STORAGE_ENCRYPTION_KEY`, `OAUTH_JWT_SIGNING_KEY` (do **NOT** set `OAUTH_ALLOW_CROSS_CLIENT_PKCE` unless also setting `OAUTH_REDIRECT_HOSTS` — it crash-loops otherwise)
- Identity/observability: `MCP_SERVER_BASE_URL=https://moodle-mcp.tryrehearsal.ai` (no trailing slash), `NEW_RELIC_LICENSE_KEY`
- Capture flags (`vars`): `MCP_CAPTURE_IDENTITY/ARGUMENTS/CLIENT_IP=true`, `MCP_CAPTURE_RESULTS=false`, `MCP_TRUST_PROXY_HEADERS=true`
- Report backend: `AGENT_API_BASE`, `AGENT_SHARED_SECRET`, `AGENT_REPORT_QUEUE`, `REPORT_PUBLIC_BASE_URL`, `MOODLE_BASE_URL` + Moodle WS token

## 3. Staging deploy + test (before DNS)
1. `wrangler deploy` → get `jaipuria-os-moodle-mcp.ailabs-c94.workers.dev`.
2. Point `MCP_SERVER_BASE_URL` at the **final** hostname (`https://moodle-mcp.tryrehearsal.ai`) even while testing on workers.dev, so issued OAuth metadata is correct.
3. Test on the workers.dev URL: Claude.ai connector (list tools + 1 read + 1 write), Jaipuria OS (Gatekeepers → MCP Server → + ) `create_report` end-to-end.
4. **Client-IP fix note:** behind CF, read `CF-Connecting-IP` for the real client IP in `security._source_ip` (currently records the CF edge IP). Do this as part of the move.

## 4. Cutover
1. Add `moodle-mcp.tryrehearsal.ai` as a **custom domain** on the Worker (Workers → Domains). If the `tryrehearsal.ai` zone isn't on Cloudflare, CNAME to the workers.dev route as interim + note it.
2. Verify: `curl -sI https://moodle-mcp.tryrehearsal.ai/health` shows `cf-ray` and **no** `x-render-origin-server`.
3. Keep Render running (paused-ready) **7 days** as rollback, then delete; note the date on AIA-1391.

## 5. Acceptance (from the ticket)
- Served by Worker `jaipuria-os-moodle-mcp` (verify `cf-ray`); cold `initialize` < 2s.
- `curl .../.well-known/oauth-authorization-server | jq .issuer` == `"https://moodle-mcp.tryrehearsal.ai"` (the trailing-slash fix + no-slash base URL land here cleanly).
- All 3 consumers connect + 1 read + 1 write, no client config change; per-user/campus scoping identical (test 2 faculty, different campuses); recording still writes to `mcp_audit`.
- No secrets in git; Render deleted after the rollback window.

## 6. Bonus this resolves
Making the MCP a first-party CF Worker/Container **dissolves the Bot-Fight-Mode / managed-challenge
risk** on `/mcp` (no Render origin behind a CF proxy) — closes that RENDER_GO_LIVE §0 concern.

## State-safety note
Streamable-HTTP sessions + the in-process rate limiter are kept coherent by routing to a **single**
container instance (`"singleton"`). If you later scale to `max_instances > 1`, set `MCP_REDIS_URL`
so the limiter + create_report budget are shared (same as the Render note).
