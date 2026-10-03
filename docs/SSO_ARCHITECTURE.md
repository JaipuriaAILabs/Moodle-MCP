# Moodle MCP — SSO architecture & hardening review

Reviewed against the HireOS SSO implementation (`Hari21-Tech/hireos`) as the reference. Conclusion
up front: **our SSO is already a proper, hardened implementation** — and for several items it is the
*right* design for an MCP, where HireOS's browser-BFF patterns would not apply. The genuine deltas
worth considering are in §4.

## 1. The crucial shape difference
| | HireOS | Moodle MCP |
| --- | --- | --- |
| Role in OAuth | **Relying Party** (BFF) — a web server that logs a human browser in | **Authorization Server + proxy** — machine clients (Claude.ai, JChat, Jaipuria OS) are the RPs; we proxy to Google |
| Who initiates | a browser hits our `/login` | an MCP client runs **Dynamic Client Registration + PKCE** against our `/register`,`/authorize`,`/token` |
| Transport | httpOnly session cookies in a browser | bearer tokens between machines (no browser, no cookies) |
| Library | hand-rolled RP on `jose` (their own design doc said *don't* hand-roll) | **FastMCP `GoogleProvider`/OAuthProxy** — a vetted library does the token validation |

So "copy HireOS" is not the goal — most of its code (sealed state cookie, `returnTo`, BFF login page,
refresh cookie) is browser-web-app machinery an MCP has no use for. What matters is whether our MCP
honors the same security *principles*. It does.

## 2. What we already have (files)
- **OAuth2 Authorization Code + PKCE (S256) + DCR**, Google Workspace IdP (`server.py` wires
  `oauth_compat.TolerantGoogleProvider`; `hd` domain hint).
- **DCR redirect-URI hardening** (`oauth_compat.RegistrationGuard`): HTTPS or loopback only, no
  `javascript:`/active-content schemes, length/space/credential checks — the MCP equivalent of
  HireOS's open-redirect defense.
- **PKCE enforced**; the only cross-client tolerance (Claude.ai's multi-node DCR race) is **flag-gated
  AND redirect-host-allowlisted**, and still enforces the verifier/redirect/expiry/single-use
  (`oauth_compat.TolerantGoogleProvider.load_authorization_code`).
- **Encrypted, persistent OAuth state** (`oauth_storage.py`): Supabase KV, Fernet with
  **rotation-tolerant MultiFernet**, a dedicated least-privilege `mcp_oauth_writer` DB role. Deploys
  don't log users out; a data-key compromise can't touch OAuth state.
- **`email_verified` enforced as a real boolean** (`security.principal_from_claims` — a string
  `"true"` is treated as unverified), **domain allow-list** (`OAUTH_ALLOWED_DOMAINS`) — identical
  posture to HireOS's `requireVerifiedEmail` (default true, boolean-only).
- **Authn decoupled from authz/tenancy** — exactly HireOS's best pattern: SSO only proves the Google
  identity; campus/role/student-boundary come from the `mcp_faculty` registry + student roster
  (the RBAC work). Access revocation = deactivate the registry row → denied within the ~60s grace.
- **Signed issued tokens** (`OAUTH_JWT_SIGNING_KEY`, pinned so restarts don't invalidate sessions).
- **Abuse controls**: per-IP pre-auth rate limit + per-principal limiter + body caps (`security.py`).

## 3. Principle-by-principle vs HireOS
| HireOS principle | Us | Verdict |
| --- | --- | --- |
| Auth Code + PKCE S256 | ✅ enforced | match |
| Strict token validation | **verified (§4.3)**: OAuthProxy obtains the Google token server-side with our creds (tokeninfo-validated), clients present our own signed JWTs; audience bound by construction. + our `email_verified`/domain checks | match — and we correctly **use a vetted library** instead of hand-rolling (HireOS's own doc advised this) |
| email_verified boolean-only | ✅ | match |
| Decouple authn from authz/tenancy | ✅ registry/roles/campus/student-boundary | match |
| Open-redirect / redirect-URI hardening | ✅ `RegistrationGuard` (HTTPS/loopback only) | match (MCP form) |
| Secrets env-only + rotation | ✅ + MultiFernet rotation, pinned signing key | match / stronger |
| Account-linking takeover defense | N/A — single IdP (Google), no email-linking | not applicable |
| Browser CSRF/state cookie, returnTo | N/A — no browser session (machine clients) | not applicable |
| **Refresh rotation + reuse detection + jti blacklist** | handled inside FastMCP's token lifecycle; **we add no reuse detection** | **minor gap — see §4.1** |
| **Explicit logout / RP-initiated end-session + session revocation** | no token-revocation endpoint; revocation is via registry deactivation / key rotation | **gap by design — see §4.2** |
| Lockout on failed logins | no password login to lock out; rate limiting covers abuse | not applicable |

## 4. Genuine deltas worth considering (none are launch-blockers)
**4.1 Refresh-token reuse detection.** HireOS rotates refresh tokens per use and revokes the whole
family on reuse (stolen-token containment). Ours relies on FastMCP's refresh handling, which does not
expose family/reuse detection. *Impact:* a stolen refresh token could be replayed within its lifetime.
*Options:* (a) accept (FastMCP-managed, machine clients, short-lived access tokens), (b) shorten
issued-token TTL, (c) add reuse detection around the OAuth-storage layer (non-trivial). **Recommend
(a)+(b)** for now; revisit if we see token abuse in the audit ledger.

**4.2 Immediate per-user revocation.** HireOS has a jti blacklist + logout. Ours revokes *access* at
the authz layer: deactivate the `mcp_faculty` row (or flip a student) → `principal_from_claims` denies
on the next request (≤ ~60s cache). This is clean and MCP-appropriate — **but only in `enforce` mode**;
in `off/shadow` a faculty can't be cut off except by rotating `OAUTH_JWT_SIGNING_KEY` (nuclear). *Action:*
note this; it resolves naturally at the enforce cutover. If instant revocation is needed pre-enforce,
shorten the registry cache TTL or rotate the signing key.

**4.3 Token validation — VERIFIED SOUND (was my main open question).** I read the pinned FastMCP
`2.14.7` source. `GoogleTokenVerifier.verify_token` (`fastmcp/server/auth/providers/google.py:89`)
validates the Google access token via Google's `tokeninfo` API (authoritative for signature/expiry) and
checks `required_scopes`, but it does **not** assert the token's `audience == our client_id` — in
isolation that would be a confused-deputy risk (a token minted for another Google app, same scopes,
could be replayed). **In our configuration it is not reachable:** `GoogleProvider` runs as an
**`OAuthProxy` (`google.py:186`, `320`)** — the Google token is fetched **server-side by OAuthProxy**
using our `upstream_client_id`/secret during the code exchange, so its audience is our client by
construction; MCP clients only ever present **our own signed FastMCP JWTs** (verified by OAuthProxy with
`OAUTH_JWT_SIGNING_KEY`), never a raw Google token. So there is **no audience-substitution gap** here.
*Optional belt-and-suspenders:* override `verify_token` in `TolerantGoogleProvider` to reject any token
whose `audience != GOOGLE_OAUTH_CLIENT_ID` — harmless (our tokens always match) and future-proofs us if
the verifier is ever used on a client-facing path. Not required today.

## 5. Verdict
Our SSO is **proper and well-hardened for an MCP**: standards OAuth2 + PKCE + DCR, a vetted provider
library, encrypted rotation-tolerant persistent state, domain + verified-email gating, hardened DCR
redirects, and — the pattern HireOS gets most right — **authn fully decoupled from authz** (our
registry/RBAC). The only real conceptual gaps (refresh-reuse detection, instant pre-enforce revocation)
are secondary and have reasonable mitigations. No rebuild is warranted; §4.3/§4.4 are small, optional
hardening steps.
