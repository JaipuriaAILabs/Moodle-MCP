# Making the Moodle MCP a permanent / built-in connector in Rehearsal OS (jaipuria-os)

Analysis of how to stop users pasting `https://moodle-mcp.tryrehearsal.ai/mcp` into the Rehearsal OS
MCP connect form each time, and instead have it as a first-class, always-available connector.

## The hard constraint (read first)

There is **no mechanism in jaipuria-os that removes per-user OAuth sign-in for an OAuth MCP server**,
and that is correct for us: the per-user Google sign-in is exactly what lets the Moodle MCP apply RBAC
(faculty → their campus; **student → their own record only**). "Zero-click, appears for everyone with no
sign-in" only exists for gatekeepers that mint accounts with **no OAuth flow** (`autoProvisionsAccount`,
e.g. Context Library, Scheduler) — the MCP gatekeepers deliberately are **not** among them
(`workshop-shared/src/gatekeeper.ts` `autoProvisionsAccount`; only `gatekeeper-context` / `gatekeeper-scheduler`
set it; `provisioning-policy.ts`).

So **"permanent" = remove the URL-paste step, keep the sign-in.**

## Options

### A. Built-in one-click pick — RECOMMENDED ✅ (shipped as JaipuriaAILabs/jaipuria-os PR #42)
Add one entry to `KNOWN_MCP_SERVERS` in `packages/gatekeeper-mcp/src/known-servers.ts` (mirrors the
existing "Jaipuria Lead Gen" entry):
```ts
{ name: "Jaipuria Moodle",
  description: "Student marks, attendance and performance reports",
  endpoint: "https://moodle-mcp.tryrehearsal.ai/mcp" },
```
- The endpoint becomes a labelled **button** on the connect form for everyone offered the MCP connector.
- A pick posts `preset`, which runs the **identical** `validateCustomEndpoint` + OAuth discovery chain as
  a pasted URL (`gatekeeper-mcp/src/mcp.ts`, `connect-form.ts`). So **per-user OAuth is preserved** → RBAC
  / student-isolation intact. (Form copy: "Each of these signs you in to your own account with that vendor.")
- Effort: one data line. Blast radius: one extra button. Ships in the `gatekeeper-mcp` worker bundle via
  the normal release pipeline — no deploy input or secret needed.

**Optional hardening — `MCP_PICKS_ONLY=true`** (a var on the `gatekeeper-mcp` worker,
`gatekeeper-mcp/src/env.d.ts`): removes the free-text URL field and makes the curated picks the
**allowlist** (`mcp.ts` enforces `isKnownMcpEndpoint`). This also closes the "any USER can connect an
arbitrary MCP server" data-exfiltration surface (old GAP_ANALYSIS G13). Ops toggle, separate from the PR.

### B. MCP Portal (fully pre-wired, no URL at all) — NOT viable as-is ❌
`gatekeeper-mcp-portal` + `MCP_PORTAL_URL` pre-wires one upstream for the whole deployment with **no
connect form**. But the portal connector only accepts an aggregator that speaks the **portal contract**
(a `portal_list_servers` tool + `{server_id}_{tool}` naming); a bare single endpoint like ours is
explicitly **"not grantable through this connector"** (`gatekeeper-mcp-portal/README.md`,
`src/config.ts`). Making this work means standing up a real MCP *portal* in front of Moodle and
installing/binding a second Worker (`GATEKEEPER_MCP_PORTAL`) + a deployment overlay to set
`MCP_PORTAL_URL` (the repo deliberately refuses to commit a portal URL). Still per-user OAuth under the
default `MCP_PORTAL_AUTH: oauth` (and `token`/`none` would collapse student identity, so not an option).
Much higher effort and operational surface for one server — not worth it.

### C. Admin "feature / pin a connector" — does not exist ❌
`workshop-backend/src/admin-config.ts` is **opt-out only** (`disabledGatekeepers`, `disabledResources`).
There is no field to pin/feature an MCP connector; `formats` curation only promotes blueprints/output
formats, not MCP servers. The real "featuring" lever is the source-level known-server entry (Option A).

## Deploy path

- **Option A** reaches prod by being compiled into the `gatekeeper-mcp` worker bundle and shipped in the
  next release (`scripts/release/build-release.ts` → `upload-release.ts` → `promote-release.ts`). The MCP
  gatekeeper is already installed on the deployment (that's how Moodle connects today); `gatekeeper-mcp`
  is not auto-preinstalled on *fresh* deploys (`manifest-lib.ts` `PREINSTALL` = context + scheduler only),
  so a new deployment's operator installs it via the deploy wizard. Neither MCP gatekeeper collects deploy
  inputs (`NO_DEFAULT_CRED_INPUTS`; MCP OAuth uses dynamic client registration, no static app creds).
- **`MCP_PICKS_ONLY`** / **`MCP_PORTAL_URL`** are **vars**, not wizard inputs — set via the deployment's
  own config/overlay (not the committed `wrangler.jsonc`).

## Recommendation

Ship **Option A** (PR #42) — Moodle becomes a built-in one-click pick org-wide while keeping per-user
sign-in and RBAC. Consider enabling **`MCP_PICKS_ONLY=true`** as an ops follow-up to also lock the
deployment to curated connectors only. Skip the portal unless/until there's a real aggregator in front of
Moodle.

Code references are to `JaipuriaAILabs/jaipuria-os`; this doc is the Moodle-MCP-side record of the decision.
