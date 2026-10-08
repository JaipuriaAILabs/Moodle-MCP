# Test & Deploy — Jaipuria Moodle MCP

## A. Test locally (5 min)

```bash
cd "moodle-mcp"
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# grab the service key from the agent project (or paste your own)
export SUPABASE_URL="https://sadbfvfcmmxgtatfjfmc.supabase.co"
export SUPABASE_DATA_KEY="<reporting_readonly JWT>"
export SUPABASE_ANON_KEY="<publishable/anon gateway key>"
export MCP_ADMIN_TOKEN="test-token-that-is-at-least-24-characters"

# 1) start the server
uvicorn server:app --port 8899
```

In a second terminal:
```bash
# 2) health check
curl localhost:8899/health          # -> {"status":"ok",...}

# 3) full MCP round-trip (lists tools + calls a few with real data)
cd "moodle-mcp" && source .venv/bin/activate
MCP_URL="http://localhost:8899/mcp" \
  MCP_TOKEN="test-token-that-is-at-least-24-characters" python test_client.py
```
Expected from the source server: 30 tools listed; `whoami` shows the exact principal and scope;
student sessions do not expose cohort tools. TrueFoundry virtual servers intentionally expose a
smaller persona-specific subset.

Before a production merge, also run the repeatable release checks:
```bash
python scripts/validate_codex_packaging.py
for test_file in tests/test_*.py; do FASTMCP_HOME=/tmp/moodle-fastmcp python "$test_file"; done
python scripts/check_live_endpoint.py https://moodle-mcp.rehearsal-os.app
```

For a built local container with dummy Supabase settings, set `MCP_SMOKE_ONLY=1` on
`test_client.py` to verify the authenticated handshake, all tool descriptors, and `whoami`
without querying student data.

## B. Deploy to Render (blueprint, ~3 min)

1. **Render → New → Blueprint** → connect **`mansigambhir-1313/Moodle-MCP`**. Render reads
   `render.yaml` and creates the `jaipuria-moodle-mcp` web service.
2. Apply the migrations and configure every `sync:false` value in `render.yaml` following
   [`docs/SECURITY_SCALABILITY_RELEASE.md`](docs/SECURITY_SCALABILITY_RELEASE.md). Do not deploy
   the new MCP before the custom data/OAuth/audit role JWTs, Redis, report-queue HMAC secret, and
   OAuth storage encryption key are present.
3. **Create** → build (`pip install -r requirements.txt`) → start (`uvicorn server:app`) →
   Render health-checks `/health`.
4. (Optional) set `MCP_SERVER_BASE_URL` to the assigned URL and add a custom domain
   (`moodle-mcp.rehearsal-os.app`).

**Verify the deploy:**
```bash
curl https://<render-url>/health
MCP_URL="https://<render-url>/mcp" MCP_TOKEN="<your admin token>" python test_client.py
```

## C. Connect an MCP host

- **URL:** `https://<render-url>/mcp`
- **Header:** `Authorization: Bearer <MCP_ADMIN_TOKEN>`

Claude CLI:
```bash
claude mcp add moodle --transport http https://<render-url>/mcp \
  --header "Authorization: Bearer <MCP_ADMIN_TOKEN>"
```
Then ask: *"which reports are flagged in jaipur T5?"*, *"who's at risk in 2024-26?"*,
*"campus overview for jaipur"*, *"open JJ24PG099's report"*.

## D. Per-campus faculty tokens (instead of one admin token)

Set `MCP_TOKENS` (JSON) so each faculty sees only their campus:
```
MCP_TOKENS={"tok_indore":{"name":"Indore TNP","campuses":["indore"]},"tok_office":{"name":"Programme Office","campuses":null}}
```
A campus outside a token's grant returns `found:false` — verified.

## E. Google sign-in for Jaipuria accounts (recommended — no manual tokens)

With OAuth configured, hosts like Claude.ai onboard users through the standard MCP OAuth flow: the
user adds the connector URL, clicks "Connect", and signs in with their **@jaipuria.ac.in** Google
account. OAuth proves identity; it does not grant data. Students receive self-only access, the
approved eight staff accounts receive cross-campus data access, each approved dean receives only
their campus, and every other account is denied. No bearer token is handed to the user.
(Without this, Claude.ai shows *"Couldn't register with Moodle's sign-in service"*
and falls back to asking each user for a token.)

### 1. Create the Google OAuth client (one-time, ~5 min)

In [Google Cloud Console](https://console.cloud.google.com/) under the **Jaipuria
Workspace** account:

1. Create/select a project → **APIs & Services → OAuth consent screen**.
   - User type: **Internal** ← this alone restricts sign-in to jaipuria.ac.in accounts
     at Google's side (the server also enforces the domain independently).
2. **Credentials → Create credentials → OAuth client ID → Web application**.
   - Authorized redirect URI: `https://<render-url>/auth/callback`
3. Copy the **Client ID** (`….apps.googleusercontent.com`) and **Client secret** (`GOCSPX-…`).

### 2. Set env vars on Render

| Var | Value |
|---|---|
| `GOOGLE_OAUTH_CLIENT_ID` | the client ID |
| `GOOGLE_OAUTH_CLIENT_SECRET` | the client secret |
| `MCP_REQUIRE_GOOGLE_OAUTH` | `true` — fail boot if Google credentials are missing; prevents an auth-mode downgrade |
| `MCP_SERVER_BASE_URL` | `https://<render-url>` (must be the public https URL) |
| `OAUTH_JWT_SIGNING_KEY` | `python3 -c "import secrets;print(secrets.token_urlsafe(48))"` — keeps logins valid across redeploys |
| `OAUTH_ALLOWED_DOMAINS` | `jaipuria.ac.in` (default) |
| `MCP_RBAC_MODE` | `enforce` — makes `public.mcp_faculty` authoritative |
| `OAUTH_DEFAULT_CAMPUSES` | `none` — unlisted accounts receive no data access |
| `MCP_SELF_SERVICE_ACCESS` | `false` — whitelist-only; no pending self-service sessions |
| `MCP_FACULTY` | legacy override; ignored in `enforce` mode |

### 3. Connect

In Claude.ai / Claude Desktop: **Settings → Connectors → Add custom connector** →
URL `https://<render-url>/mcp` → **Connect** → Google sign-in. Done.

Notes:
- The Jaipuria consent page's **Continue with Google** button approves the requesting
  MCP client and immediately hands the browser to Google's real OAuth flow. The local
  consent/CSRF step remains enabled to prevent confused-deputy attacks; it is not a
  password form and never receives Google credentials.
- When OAuth is enabled, static `MCP_TOKENS`/`MCP_ADMIN_TOKEN` are **not** accepted on
  `/mcp` (FastMCP validates its own issued tokens); remove them or keep them only for
  a separate non-OAuth deployment.
- Sign-ins from outside `OAUTH_ALLOWED_DOMAINS` (or unverified emails) are rejected
  per-call with "Access denied", even if Google issued a token.
- `whoami` now returns the signed-in email — use it to verify scoping.

### Robustness for all users (built-in)

- **Either URL works**: `https://<render-url>/mcp` and the bare `https://<render-url>`
  both reach the MCP endpoint (`oauth_compat.PathAliases`), so a connector added
  without the `/mcp` path no longer fails with "no MCP server was found".
- **Claude.ai's DCR race is tolerated**: Claude's backend may register several OAuth
  clients concurrently and redeem the authorization code as a different client than
  it authorized with. `oauth_compat.TolerantGoogleProvider` accepts that exchange
  when the code is PKCE-bound (verifier, redirect_uri, expiry, single-use all still
  enforced by the framework); codes without PKCE keep the strict client check.
- **Keep-warm**: `.github/workflows/keep-warm.yml` pings `/health` every ~10 min so
  the free Render instance rarely spins down (cold starts + in-memory OAuth state
  loss become rare). If the server does restart, issued tokens stay valid
  (`OAUTH_JWT_SIGNING_KEY`), and a user whose refresh fails is simply sent through
  Google sign-in again.

## F. Roll out the Codex plugin to the team

After the release pull request is merged and Render is healthy, follow
[`docs/TEAM_ROLLOUT.md`](docs/TEAM_ROLLOUT.md). The workspace admin imports the GitHub repository's
`.agents/plugins/marketplace.json`, assigns a pilot role, and completes one fresh Codex desktop
OAuth/tool-call test before expanding access.
