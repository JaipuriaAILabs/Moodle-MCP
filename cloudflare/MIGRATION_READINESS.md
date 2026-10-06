# AIA-1391 — Render→Cloudflare migration: readiness + blockers (for review)

From a live recon of the **AIlabs Cloudflare account** (`c945945018f732e5607a331ef73c7d75`) on 2026-10-06.
Pairs with the Containers scaffold in this folder (`wrangler.jsonc`, `src/index.ts`, `README.md`).

## ✅✅ STAGING DEPLOY — VERIFIED GREEN (2026-10-06)
The Containers path is **proven end-to-end**. The *existing audited Python app* (`../Dockerfile`, zero
rewrite) now runs on Cloudflare Containers at
**`https://jaipuria-os-moodle-mcp.ailabs-c94.workers.dev`** (app id `a03d2750-b8d3-4370-80c7-77e485d1bc51`,
image `sha256:5ca25d2…`, `instance_type: standard-1`, 1 instance). All 11 secrets pushed via
`wrangler secret put` (see `README.md` checklist). Nothing on Render was touched — this is `workers.dev`
only; the live service stays on Render until the domain decision + a tested cutover.

Smoke tests (all pass):

| Check | Expect | Result |
|---|---|---|
| `GET /health` | 200 | **200** `{"status":"ok"}` |
| `POST /mcp` `tools/list` (unauth) | 401 | **401** — tool layer is auth-gated |
| `/.well-known/oauth-authorization-server` | issuer = staging host | **✓** issuer/authorize/token/register all = staging host |
| `/.well-known/oauth-protected-resource` | resource = staging host | **✓** + Google scopes |
| Host-leak check on both discovery docs | no prod host | **✓ clean** (no `tryrehearsal.ai` / Render) |
| `POST /register` (DCR) | 201 + client_id | **201**, `client_id` issued → **container writes to Supabase OAuth storage** |
| `GET /authorize` (registered client) | 302 → consent/Google | **302 → `/consent?txn_id=…`** → full stateful OAuth flow live |

Only step not machine-verifiable here is a Google-signed-in tool invocation (needs interactive consent);
the tool layer behind the auth above is byte-identical to the Render image.

Deploy gotchas captured so a re-run is clean: deploy **from the repo root** so the Dockerfile `COPY . .`
context is correct (`npx wrangler deploy -c cloudflare/wrangler.staging.jsonc`); prepend
`/Applications/Docker.app/Contents/Resources/bin` to `PATH` or the image build fails on
`docker-credential-desktop not found`; deps must be `@cloudflare/containers ^0.3.7` +
`@cloudflare/workers-types ^5` (the scaffold's `^0.0.44`/`^4` do not resolve).

## ✅ Ready (verified in the dashboard)
- **Account:** AIlabs (`c945945018…`), subdomain **`ailabs-c94.workers.dev`** — matches `wrangler.jsonc`.
- **Cloudflare Containers** is available on the account.
- **Workers Paid is already active** — **110 dynamic Workers** are running (dynamic Worker Loading requires
  the Paid plan), so the `error 10195` blocker noted on the ticket is **cleared**.
- **jaipuria-os is live here** (router at `rehearsal-os.app` + workshop / custom-gatekeeper / google / mcp /
  scheduler Workers). **No `moodle-mcp` Worker exists yet** → the Moodle MCP is still on Render, as expected.

## ⛔ Blocker 1 — the custom domain `moodle-mcp.tryrehearsal.ai` has no zone here
The account has **exactly one zone: `rehearsal-os.app`**. There is **no `tryrehearsal.ai` zone**. A Cloudflare
Worker can only bind a **custom domain whose zone is in the same account**, so the hostname cutover is not
possible as-is. Three options — **needs a decision + the `tryrehearsal.ai` DNS owner**:

1. **Add `tryrehearsal.ai` to this CF account.** Requires moving the **whole domain's nameservers** to
   Cloudflare — which also carries **`reports.tryrehearsal.ai` (the live reports backend / core infra)**.
   High blast radius; do **not** do without explicit sign-off + core-infra coordination.
2. **Cloudflare for SaaS (custom hostname)** — *recommended.* Keep `tryrehearsal.ai` DNS where it is; CF
   serves `moodle-mcp.tryrehearsal.ai` via a custom hostname + a verification CNAME. No nameserver move,
   doesn't disturb `reports.tryrehearsal.ai`.
3. **Move the MCP to a `rehearsal-os.app` subdomain** (e.g. `moodle-mcp.rehearsal-os.app`). Trivial on CF,
   but **changes the MCP's public URL → breaks every consumer** (defeats the "keep the hostname" goal) and
   forces OAuth-redirect / connector reconfig everywhere. Not recommended.

## ✅ Blocker 2 — RESOLVED: wrangler auth + staging deploy done
`wrangler login` as the ailabs owner is done; the `workers.dev` staging deploy succeeded and is verified
green (see top section). No touch to the live Render service.

## ✅ Blocker 3 — RESOLVED: Containers path proven
The **Containers** path (reuse the audited Python app, zero rewrite) is no longer a hypothesis — the image
builds and the container boots, serves, auth-gates, and completes OAuth/DCR against Supabase on CF. A TS
port is not needed for parity. Confirm this is the direction to carry into prod.

## Asks for @rajika.patel
1. **Domain path:** option 1, 2 (recommended), or 3 — and who controls `tryrehearsal.ai` DNS to action it.
2. **Confirm the Containers path** (vs TS port).
3. Green-light a **`workers.dev` staging deploy** as the first step (non-destructive; nothing live changes).

## Rollback / safety note
Nothing has been changed on Cloudflare — this was read-only recon. The live Moodle MCP stays on Render
(`srv-da61ppjncjis73aer1hg`, paid/always-on) until a tested staging deploy + an agreed cutover.
