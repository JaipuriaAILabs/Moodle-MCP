# AIA-1391 — Render→Cloudflare migration: readiness + blockers (for review)

From a live recon of the **AIlabs Cloudflare account** (`c945945018f732e5607a331ef73c7d75`) on 2026-10-06.
Pairs with the Containers scaffold in this folder (`wrangler.jsonc`, `src/index.ts`, `README.md`).

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

## ⛔ Blocker 2 — the deploy is `wrangler` CLI, and needs auth
The container deploys via `wrangler deploy` (build image → Worker + container), not the dashboard. It needs
`wrangler login` as **ailabs@jaipuria.ac.in** (or a scoped API token). Credentials are not something I set —
this is an owner step. Safe first move once authed: deploy to the **`workers.dev` staging URL**
(`jaipuria-os-moodle-mcp.ailabs-c94.workers.dev`), test the 3 consumers, then decide the domain path — no
touch to the live Render service until cutover.

## ⛔ Blocker 3 — architecture confirmation still open
The scaffold assumes the **Containers** path (reuse the audited Python app, zero rewrite). The AIA-1391
runtime decision flagged **Containers vs a TS port** as a shared call. Please confirm Containers before we build.

## Asks for @rajika.patel
1. **Domain path:** option 1, 2 (recommended), or 3 — and who controls `tryrehearsal.ai` DNS to action it.
2. **Confirm the Containers path** (vs TS port).
3. Green-light a **`workers.dev` staging deploy** as the first step (non-destructive; nothing live changes).

## Rollback / safety note
Nothing has been changed on Cloudflare — this was read-only recon. The live Moodle MCP stays on Render
(`srv-da61ppjncjis73aer1hg`, paid/always-on) until a tested staging deploy + an agreed cutover.
