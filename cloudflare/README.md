# Moodle MCP on Cloudflare — migration scaffold (AIA-1391)

Moves the Moodle MCP off Render onto Cloudflare in the **same account as Jaipuria OS**, using the
**Cloudflare Containers** path: front the existing audited Python app (`../Dockerfile`) with a thin
Worker + a single container instance — **zero rewrite** of the OAuth / RBAC / audit / tools code.

> **Status (2026-10-05):** review-ready scaffold. **Not yet deployed.** Blocked on: (1) Rajika's
> confirmation of Containers vs a TS port, (2) Cloudflare `ailabs@jaipuria.ac.in` account access,
> (3) Render dashboard access (to export the live env), (4) Workers **Paid** plan active (error
> `10195` otherwise). Fields marked `VERIFY` are finalised at the first `wrangler deploy` with CF access.

## Files
| File | Purpose |
|---|---|
| `wrangler.jsonc` | Worker + container config, custom domain, non-secret `vars` (mirrors `../render.yaml`) |
| `src/index.ts` | Thin Worker: pins one container instance, forwards `CF-Connecting-IP` as `X-Forwarded-For` |
| `package.json` / `tsconfig.json` | `@cloudflare/containers` + wrangler toolchain |
| image | **`../Dockerfile`** (unchanged `python:3.12-slim`, `uvicorn server:app`) |

## Why these choices (parity with Render)
- **One pinned instance** (`getContainer(…, "singleton")`, `max_instances: 1`): FastMCP streamable-HTTP
  sessions + the in-process rate limiter live in memory. OAuth client/token state is already in
  Supabase, so it is instance-independent — but sessions/limiter are not. Single process = Render parity.
- **Real client IP**: the app records source IP and rate-limits off the **rightmost** `X-Forwarded-For`
  (`MCP_TRUST_PROXY_HEADERS=true`). The Worker sets `X-Forwarded-For` to the single `CF-Connecting-IP`
  so per-user IP capture + per-IP flood limits keep working.
- **Warm start** for the `<2s` cold-`initialize` criterion: long `sleepAfter` + a keepalive cron on
  `/health` (reuse the existing 10-min health cron pattern).
- **Issuer**: `MCP_SERVER_BASE_URL=https://moodle-mcp.tryrehearsal.ai` (no trailing slash) is a `var`;
  the app's `config.py` validator already strips a stray slash, so the `.issuer` acceptance check passes.

## Build context
`../Dockerfile` does `COPY . .`, so the image build context must be the **repo root**. `image:
"../Dockerfile"` resolves the context to `..` for current wrangler. If a deploy builds an empty/cwd
context instead, move `wrangler.jsonc` to the repo root (and set `image: "./Dockerfile"`, `main:
"cloudflare/src/index.ts"`). Add a root `.dockerignore` with `cloudflare/node_modules` to keep the
image lean.

## Secrets — `wrangler secret put <NAME>` (the `sync:false` entries in `../render.yaml`)
Run each from this directory after `wrangler login`. **Copy values from the Render dashboard yourself**
(I don't enter credentials into forms). Non-secret config is already in `wrangler.jsonc` → `vars`.

```sh
# Supabase (least-privilege keys)
wrangler secret put SUPABASE_URL
wrangler secret put SUPABASE_DATA_KEY           # SELECT-only reporting_readonly JWT
wrangler secret put SUPABASE_OAUTH_STORAGE_KEY  # CRUD on mcp_oauth_kv only
wrangler secret put SUPABASE_AUDIT_KEY          # execute record_mcp_tool_call only
wrangler secret put SUPABASE_ANON_KEY

# Google OAuth proxy + token/DCR store encryption  (STABLE — rotating orphans every client; keyset to rotate)
wrangler secret put GOOGLE_OAUTH_CLIENT_ID
wrangler secret put GOOGLE_OAUTH_CLIENT_SECRET
wrangler secret put OAUTH_JWT_SIGNING_KEY
wrangler secret put OAUTH_STORAGE_ENCRYPTION_KEY
wrangler secret put OAUTH_REDIRECT_HOSTS

# Audit + PII HMAC
wrangler secret put MCP_AUDIT_HMAC_KEY
wrangler secret put MCP_PII_HMAC_KEY            # required before MCP_PII_REDACTION_MODE=shadow/enforce

# Optional — set only if the feature is on
wrangler secret put MCP_REDIS_URL                   # only if scaling >1 instance
wrangler secret put NEW_RELIC_LICENSE_KEY           # turns on OTel → New Relic EU
wrangler secret put MCP_FACULTY                      # faculty registry JSON (for RBAC enforce)
wrangler secret put AGENT_API_BASE                  # enables create_report
wrangler secret put AGENT_SHARED_SECRET             # with AGENT_API_BASE
wrangler secret put MCP_ACCESS_REQUEST_WEBHOOK_URL  # with the secret below (set both or neither)
wrangler secret put MCP_ACCESS_REQUEST_WEBHOOK_SECRET
```

## Deploy (once access is granted)
```sh
cd cloudflare
npm install
wrangler login                      # must land on the ailabs@jaipuria.ac.in account
# 1) Staging on workers.dev first (comment out the custom-domain route, or use `--route`):
wrangler deploy                     # → jaipuria-os-moodle-mcp.<subdomain>.workers.dev
```
Test on the staging URL **before** touching DNS:
- **Claude.ai connector:** connect → list tools → one read tool + one `create_report`.
- **Jaipuria OS:** Gatekeepers → MCP Server → + → staging `/mcp` → sign in → one tool call.
- **Two-faculty cross-campus** parity: confirm campus scoping matches Render exactly.

Then cut over:
1. Re-enable the `routes` custom-domain in `wrangler.jsonc` (or add the custom domain in the dash) and
   `wrangler deploy`. If `tryrehearsal.ai` is **not** a zone on this CF account, instead CNAME
   `moodle-mcp` → the `workers.dev` hostname as an interim and note it on the ticket.
2. Keep the Render service **paused-ready for 7 days** as rollback, then delete it and note the date on AIA-1391.
3. Enable Workers Logs; confirm tool calls + OAuth errors are visible (replaces Render logs). New Relic
   OTel still exports from inside the container if `NEW_RELIC_LICENSE_KEY` is set.

## Acceptance checks (AIA-1391)
```sh
curl -sI https://moodle-mcp.tryrehearsal.ai/mcp | grep -i cf-ray            # served by the Worker
curl -s https://moodle-mcp.tryrehearsal.ai/.well-known/oauth-authorization-server \
  | jq .issuer                                                               # == "https://moodle-mcp.tryrehearsal.ai"
# cold initialize < 2s; all 3 consumers connect with NO config change on their side
```

## Still to confirm with Rajika
- Containers vs TS port (this scaffold assumes **Containers**).
- `instance_type` + whether `max_instances: 1` + keepalive meets `<2s` cold in practice (else revisit TS port).
- Is `tryrehearsal.ai` a zone on this CF account (custom domain) or CNAME interim?
- Workers **Paid** plan active before deploy.
