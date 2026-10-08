"""Security-hardening verification (plain asserts; this repo has no pytest infra).

Run:  ../moodle-agent/.venv/bin/python tests/test_hardening.py
from the moodle-mcp directory (so local modules import).
"""
import os
import sys

# Import local modules (run from repo root).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Minimal config so `import config` succeeds without a real .env.
os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-service-key")
# Historical mode-specific assertions below opt into OFF explicitly; production defaults enforce.
os.environ.setdefault("MCP_RBAC_MODE", "off")

PASS, FAIL = 0, 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}")


# --------------------------------------------------------------------------
# Fake supabase client that records whether the DB was actually queried.
# --------------------------------------------------------------------------
class _Chain:
    def __init__(self, hits, rows):
        self._hits = hits
        self._rows = rows
        self.not_ = self

    def select(self, *a, **k): return self
    def eq(self, *a, **k): return self
    def is_(self, *a, **k): return self
    def in_(self, *a, **k): return self
    def order(self, *a, **k): return self
    def limit(self, *a, **k): return self

    def execute(self):
        self._hits.append(1)  # DB was touched

        class _R:
            data = self._rows
        return _R()


class FakeClient:
    def __init__(self, rows=None):
        self.db_hits = []
        self._rows = rows or []

    def table(self, name):
        return _Chain(self.db_hits, self._rows)


def phase1_latest_run_scope():
    print("PHASE 1 — latest_run is scope-aware")
    from supabase_client import MoodleService

    # jaipur-scoped caller
    fc = FakeClient(rows=[{"run_id": "RID", "finished_at": "t"}])
    svc = MoodleService(fc, {"name": "Jaipur", "campuses": ["jaipur"]})

    # out-of-scope campus -> None, and NO db query issued
    r = svc.latest_run("noida", "2024-26")
    check("out-of-scope campus returns None", r is None)
    check("out-of-scope short-circuits before any DB query", len(fc.db_hits) == 0)

    # in-scope campus -> proceeds to query and returns the run id
    r2 = svc.latest_run("jaipur", "2024-26")
    check("in-scope campus resolves the run id", r2 == "RID")
    check("in-scope campus did hit the DB", len(fc.db_hits) >= 1)

    # admin (campuses=None) -> any campus allowed
    fc2 = FakeClient(rows=[{"run_id": "RID2", "finished_at": "t"}])
    admin = MoodleService(fc2, {"name": "admin", "campuses": None})
    check("admin resolves any campus", admin.latest_run("noida", "2024-26") == "RID2")


def _make_jwt(role):
    import base64
    import json as _json

    def b64(d):
        return base64.urlsafe_b64encode(_json.dumps(d).encode()).rstrip(b"=").decode()

    return f"{b64({'alg': 'HS256', 'typ': 'JWT'})}.{b64({'role': role, 'iss': 'supabase'})}.sig"


def phase2_key_role_detection():
    print("\nPHASE 2 — service_role (god-key) detection")
    from config import key_role

    check("detects service_role key", key_role(_make_jwt("service_role")) == "service_role")
    check("detects reporting_readonly key",
          key_role(_make_jwt("reporting_readonly")) == "reporting_readonly")
    check("opaque/non-JWT key -> None", key_role("not-a-jwt") is None)
    check("empty key -> None", key_role("") is None)


def phase3_transport_guard():
    print("\nPHASE 3 — transport guard (body cap / IP limit / auth / health)")
    import asyncio

    from security import TransportGuard

    app_calls = []

    async def app(scope, receive, send):
        app_calls.append(1)
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    async def drive(guard, scope, chunks=None):
        sent = []
        chunks = list(chunks or [b""])
        cursor = 0

        async def receive():
            nonlocal cursor
            if cursor >= len(chunks):
                return {"type": "http.disconnect", "from_upstream": True}
            body = chunks[cursor]
            cursor += 1
            return {"type": "http.request", "body": body,
                    "more_body": cursor < len(chunks)}

        async def send(m):
            sent.append(m)

        await guard(scope, receive, send)
        return next((m["status"] for m in sent if m["type"] == "http.response.start"), None)

    def mcp_scope(headers=None, ip="1.2.3.4"):
        return {"type": "http", "path": "/mcp", "method": "POST",
                "headers": headers or [], "client": (ip, 1234)}

    # 1. body cap -> 413, app never reached
    app_calls.clear()
    g = TransportGuard(app, max_body=1024, ip_rate_limit=240)
    st = asyncio.run(drive(g, mcp_scope(headers=[(b"content-length", b"999999")])))
    check("oversized body -> 413", st == 413)
    check("oversized body never reaches the app", not app_calls)

    # A chunked request has no Content-Length. The received-byte cap must still
    # reject it before OAuth/MCP body parsers can buffer an unbounded payload.
    app_calls.clear()
    g_chunked = TransportGuard(app, max_body=1024, ip_rate_limit=240,
                               check_bearer=False)
    st = asyncio.run(drive(g_chunked, mcp_scope(), chunks=[b"a" * 600, b"b" * 600]))
    check("oversized chunked body -> 413", st == 413)
    check("oversized chunked body never reaches the app", not app_calls)

    # 2. per-IP limit -> first request passes IP (401 no-token), second 429
    g2 = TransportGuard(app, max_body=262144, ip_rate_limit=1)
    st1 = asyncio.run(drive(g2, mcp_scope(ip="9.9.9.9")))
    st2 = asyncio.run(drive(g2, mcp_scope(ip="9.9.9.9")))
    check("first request from IP passes the limiter (401, no token)", st1 == 401)
    check("second request from same IP -> 429", st2 == 429)

    # 3. /health stays open (no auth, passthrough)
    app_calls.clear()
    g3 = TransportGuard(app)
    sth = asyncio.run(drive(g3, {"type": "http", "path": "/health", "method": "GET",
                                 "headers": [], "client": ("1.2.3.4", 1)}))
    check("/health passes through to the app (200)", sth == 200 and app_calls)

    # 4. tokenless /mcp -> 401
    g4 = TransportGuard(app, ip_rate_limit=240)
    st401 = asyncio.run(drive(g4, mcp_scope(ip="2.2.2.2")))
    check("tokenless /mcp -> 401", st401 == 401)

    # Streamable HTTP reads receive() again while sending its response. After the
    # buffered body is replayed, that call must reach the real socket receive.
    observed = {}

    async def streaming_app(scope, receive, send):
        observed["request"] = await receive()
        observed["next"] = await receive()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    streaming_guard = TransportGuard(streaming_app, check_bearer=False,
                                     ip_rate_limit=0)
    streaming_status = asyncio.run(drive(
        streaming_guard, mcp_scope(ip="3.3.3.3"), chunks=[b"{}"]
    ))
    check("body replay delegates later receive calls to the real connection",
          streaming_status == 200
          and observed.get("request", {}).get("type") == "http.request"
          and observed.get("next", {}).get("from_upstream") is True)


def phase4_credential_hygiene():
    print("\nPHASE 4 — credential hygiene (expiry / strength / expires-format)")
    import importlib

    import config as cfgmod
    import security
    from config import settings as cfg
    from security import resolve_principal, token_expired

    check("past date -> expired", token_expired("2000-01-01") is True)
    check("future date -> not expired", token_expired("2999-12-31") is False)
    check("no expiry -> not expired", token_expired(None) is False)
    check("malformed expiry -> not expired (fail-open)", token_expired("nonsense") is False)

    # resolve_principal honours expiry (inject a known token map at the cache)
    security._token_cache["sig"] = (cfg.mcp_tokens_raw, cfg.mcp_admin_token)
    security._token_cache["map"] = {
        "tok-valid-000000000000000000": {"name": "A", "campuses": ["jaipur"]},
        "tok-expired-0000000000000000": {"name": "B", "campuses": ["jaipur"], "expires": "2000-01-01"},
        "tok-future-000000000000000000": {"name": "C", "campuses": ["jaipur"], "expires": "2999-12-31"},
    }
    check("valid token resolves", resolve_principal("tok-valid-000000000000000000") is not None)
    check("expired token rejected (revocable by date)",
          resolve_principal("tok-expired-0000000000000000") is None)
    check("future-dated token resolves", resolve_principal("tok-future-000000000000000000") is not None)

    def reload_with(**env):
        for k, v in env.items():
            os.environ[k] = v
        importlib.reload(cfgmod)

    def raises(fn):
        try:
            fn()
            return False
        except RuntimeError:
            return True

    reload_with(SUPABASE_URL="https://x.supabase.co", SUPABASE_SERVICE_ROLE_KEY="k",
                MCP_ADMIN_TOKEN="short", ALLOW_WEAK_TOKENS="false")
    check("weak admin token rejected at boot", raises(cfgmod.validate_config))

    reload_with(ALLOW_WEAK_TOKENS="true")
    check("ALLOW_WEAK_TOKENS lets a weak token boot", not raises(cfgmod.validate_config))

    os.environ.pop("MCP_ADMIN_TOKEN", None)
    os.environ.pop("ALLOW_WEAK_TOKENS", None)
    reload_with(MCP_TOKENS='{"averylongtoken-abcdefghijklmnop":'
                           '{"campuses":["jaipur"],"expires":"not-a-date"}}')
    check("bad expires format rejected at boot", raises(cfgmod.validate_config))
    os.environ.pop("MCP_TOKENS", None)
    importlib.reload(cfgmod)


def phase5_oauth_signin():
    print("\nPHASE 5 — Google OAuth sign-in (domain gate / grants / boot validation)")
    import importlib

    import config as cfgmod

    def reload_with(**env):
        for k, v in env.items():
            os.environ[k] = v
        importlib.reload(cfgmod)

    def raises(fn):
        try:
            fn()
            return False
        except RuntimeError:
            return True

    reload_with(SUPABASE_URL="https://x.supabase.co", SUPABASE_SERVICE_ROLE_KEY="k",
                GOOGLE_OAUTH_CLIENT_ID="id.apps.googleusercontent.com",
                GOOGLE_OAUTH_CLIENT_SECRET="GOCSPX-x",
                MCP_SERVER_BASE_URL="https://mcp.example.com",
                OAUTH_DEFAULT_CAMPUSES="none",
                OAUTH_JWT_SIGNING_KEY="k" * 32)
    import security
    importlib.reload(security)
    from security import principal_from_claims

    # The DB faculty registry now sits between the env override and the default
    # grant; stub it as "reachable, empty, nobody is a student" so these checks
    # keep testing the identity gates + default-grant logic in isolation
    # (tests/test_faculty_registry.py covers the registry itself).
    import faculty as _reg
    _reg._fetch_faculty_row = lambda email: None
    _reg._fetch_student_hit = lambda email: False
    _reg._grants = _reg.TTLCache(maxsize=8, ttl=0.0)
    _reg._students = _reg.TTLCache(maxsize=8, ttl=0.0)

    p = principal_from_claims({"email": "Prof@Jaipuria.ac.in", "email_verified": True,
                               "name": "Prof"})
    check("unlisted Jaipuria ID allowed with default none",
          p is not None and p["campuses"] is None)

    reload_with(OAUTH_DEFAULT_CAMPUSES="all")
    p = principal_from_claims({"email": "Prof@Jaipuria.ac.in", "email_verified": True,
                               "name": "Prof"})
    check("jaipuria.ac.in email accepted (case-insensitive)",
          p is not None and p["email"] == "prof@jaipuria.ac.in")
    check("Jaipuria ID has all-campus access", p is not None and p["campuses"] is None)
    check("outside domain rejected",
          principal_from_claims({"email": "x@gmail.com", "email_verified": True}) is None)
    check("missing email rejected",
          principal_from_claims({"name": "X", "email_verified": True}) is None)
    check("malformed email rejected",
          principal_from_claims({"email": "x@evil@jaipuria.ac.in",
                                 "email_verified": True}) is None)
    check("missing verification claim rejected",
          principal_from_claims({"email": "p@jaipuria.ac.in"}) is None)
    check("unverified email rejected (userinfo v2 spelling)",
          principal_from_claims({"email": "p@jaipuria.ac.in",
                                 "google_user_data": {"verified_email": False}}) is None)

    reload_with(MCP_FACULTY='{"dean@jaipuria.ac.in": {"name": "Dean", "campuses": ["jaipur"]}}',
                OAUTH_DEFAULT_CAMPUSES="none")
    p = principal_from_claims({"email": "dean@jaipuria.ac.in", "email_verified": True})
    check("old MCP_FACULTY scope cannot narrow Jaipuria access",
          p is not None and p["campuses"] is None)
    check("unlisted Jaipuria ID allowed when OAUTH_DEFAULT_CAMPUSES=none",
          principal_from_claims({"email": "other@jaipuria.ac.in",
                                 "email_verified": True})["campuses"] is None)

    reload_with(OAUTH_DEFAULT_CAMPUSES='["noida"]', MCP_FACULTY="")
    p = principal_from_claims({"email": "other@jaipuria.ac.in", "email_verified": True})
    check("JSON-list default cannot narrow Jaipuria access",
          p is not None and p["campuses"] is None)

    reload_with(MCP_FACULTY='{"dean@jaipuria.ac.in": {"name": "Dean"}}')
    check("MCP_FACULTY entry without campuses rejected at boot",
          raises(cfgmod.validate_config))
    reload_with(MCP_FACULTY='{"dean@jaipuria.ac.in": {"campuses": [""]}}')
    check("MCP_FACULTY entry with blank campus rejected at boot",
          raises(cfgmod.validate_config))
    reload_with(MCP_FACULTY="", OAUTH_DEFAULT_CAMPUSES='["noida", ""]')
    check("blank campus in OAuth default rejected at boot",
          raises(cfgmod.validate_config))

    # Boot validation: half-configured OAuth and a non-https base URL must fail closed.
    reload_with(OAUTH_DEFAULT_CAMPUSES="all", GOOGLE_OAUTH_CLIENT_SECRET="")
    check("half-configured OAuth rejected at boot", raises(cfgmod.validate_config))
    reload_with(GOOGLE_OAUTH_CLIENT_SECRET="GOCSPX-x", MCP_SERVER_BASE_URL="")
    check("OAuth without https base_url rejected at boot", raises(cfgmod.validate_config))
    reload_with(MCP_SERVER_BASE_URL="https://mcp.example.com", OAUTH_ALLOWED_DOMAINS=" ")
    check("empty allowed-domains rejected at boot", raises(cfgmod.validate_config))

    for k in ("GOOGLE_OAUTH_CLIENT_ID", "GOOGLE_OAUTH_CLIENT_SECRET", "MCP_SERVER_BASE_URL",
              "OAUTH_ALLOWED_DOMAINS", "OAUTH_DEFAULT_CAMPUSES", "MCP_FACULTY",
              "OAUTH_JWT_SIGNING_KEY"):
        os.environ.pop(k, None)
    importlib.reload(cfgmod)
    importlib.reload(security)


def phase6_oauth_compat():
    print("\nPHASE 6 — OAuth compatibility (aliases / DCR guard / race tolerance)")
    import asyncio
    import time as _time

    from oauth_compat import (PATH_ALIASES, PathAliases, RegistrationGuard,
                              TolerantGoogleProvider, _safe_redirect_uri)

    # --- PathAliases: exact rewrites only, everything else untouched ----------
    seen = {}

    async def inner(scope, receive, send):
        seen["path"] = scope.get("path")

    aliased = PathAliases(inner)

    def run(path, typ="http"):
        seen.clear()
        asyncio.run(aliased({"type": typ, "path": path}, None, None))
        return seen.get("path")

    check("bare / rewritten to /mcp", run("/") == "/mcp")
    check("root protected-resource doc rewritten",
          run("/.well-known/oauth-protected-resource")
          == "/.well-known/oauth-protected-resource/mcp")
    check("path-inserted auth-server doc rewritten",
          run("/.well-known/oauth-authorization-server/mcp")
          == "/.well-known/oauth-authorization-server")
    check("/mcp untouched", run("/mcp") == "/mcp")
    check("/health untouched", run("/health") == "/health")
    check("non-http scope untouched", run("/", typ="lifespan") == "/")

    # --- RegistrationGuard: public DCR cannot persist an unsafe callback ------
    check("HTTPS OAuth callback accepted",
          _safe_redirect_uri("https://chatgpt.com/connector_platform_oauth_redirect"))
    check("localhost HTTP callback accepted",
          _safe_redirect_uri("http://localhost:49152/callback"))
    check("IPv4 loopback HTTP callback accepted",
          _safe_redirect_uri("http://127.0.0.1:49152/callback"))
    check("IPv6 loopback HTTP callback accepted",
          _safe_redirect_uri("http://[::1]:49152/callback"))
    check("javascript callback rejected",
          not _safe_redirect_uri("javascript:alert(1)"))
    check("remote cleartext callback rejected",
          not _safe_redirect_uri("http://example.com/callback"))
    check("callback with credentials rejected",
          not _safe_redirect_uri("https://user:pass@example.com/callback"))
    check("callback with fragment rejected",
          not _safe_redirect_uri("https://example.com/callback#token"))

    async def registration_case(uri):
        observed, sent = {}, []

        async def registered(scope, receive, send):
            observed["called"] = True
            observed["body"] = (await receive()).get("body")

        guard = RegistrationGuard(registered)
        request_body = ("{\"redirect_uris\":[\"" + uri + "\"]}").encode()
        delivered = False

        async def receive():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": request_body,
                        "more_body": False}
            return {"type": "http.disconnect"}

        async def send(message):
            sent.append(message)

        await guard({"type": "http", "method": "POST", "path": "/register"},
                    receive, send)
        return observed, sent, request_body

    accepted, accepted_sent, original = asyncio.run(registration_case(
        "https://chatgpt.com/connector_platform_oauth_redirect"))
    check("registration guard replays accepted body unchanged",
          accepted.get("called") and accepted.get("body") == original
          and not accepted_sent)
    rejected, rejected_sent, _ = asyncio.run(registration_case("javascript:alert(1)"))
    check("registration guard returns OAuth 400 for unsafe callback",
          not rejected.get("called") and rejected_sent
          and rejected_sent[0].get("status") == 400)

    # --- Cross-client redemption is deny-by-default and tightly allowlisted ---
    provider = TolerantGoogleProvider(
        client_id="x.apps.googleusercontent.com", client_secret="GOCSPX-x",
        base_url="https://mcp.example.com",
        required_scopes=["openid"],
    )
    check("OAuth proxy consent screen is required (confused-deputy protection)",
          provider._require_authorization_consent is True)

    class FakeStore:
        def __init__(self, model):
            self.model, self.deleted = model, False

        async def get(self, key):
            return self.model

        async def delete(self, key):
            self.deleted = True

    class CodeModel:
        client_id = "client-A"
        redirect_uri = "https://claude.ai/api/mcp/auth_callback"
        scopes = ["openid"]
        expires_at = _time.time() + 300
        code_challenge = "challenge123"

    class ClientB:
        client_id = "client-B"

    class ClientA:
        client_id = "client-A"

    provider._code_store = FakeStore(CodeModel())
    same = asyncio.run(provider.load_authorization_code(ClientA(), "code1"))
    check("same-client exchange loads code",
          same is not None and same.code_challenge == "challenge123")
    cross = asyncio.run(provider.load_authorization_code(ClientB(), "code1"))
    check("cross-client exchange rejected by default even with PKCE", cross is None)

    import config as _config
    _config.settings.oauth_allow_cross_client_pkce = True
    _config.settings.oauth_redirect_hosts_raw = "claude.ai"
    cross = asyncio.run(provider.load_authorization_code(ClientB(), "code1"))
    check("explicit exception accepts PKCE only on approved redirect host",
          cross is not None and cross.client_id == "client-B"
          and cross.code_challenge == "challenge123")

    class NoPkce(CodeModel):
        code_challenge = ""

    provider._code_store = FakeStore(NoPkce())
    check("cross-client exchange REJECTED without PKCE",
          asyncio.run(provider.load_authorization_code(ClientB(), "code1")) is None)

    class Expired(CodeModel):
        expires_at = _time.time() - 1

    store = FakeStore(Expired())
    provider._code_store = store
    check("expired code rejected and deleted",
          asyncio.run(provider.load_authorization_code(ClientA(), "code1")) is None
          and store.deleted)

    class EmptyStore(FakeStore):
        async def get(self, key):
            return None

    provider._code_store = EmptyStore(None)
    check("unknown code rejected",
          asyncio.run(provider.load_authorization_code(ClientA(), "nope")) is None)
    _config.settings.oauth_allow_cross_client_pkce = False
    _config.settings.oauth_redirect_hosts_raw = ""

    # --- validation errors surface as clean parameter messages ---------------
    from pydantic import BaseModel, Field

    from fastmcp import Client, FastMCP
    from security import build_middleware

    m = FastMCP(name="t-val")
    m.add_middleware(build_middleware(90, 60))

    class VP(BaseModel):
        campus: str = Field(max_length=8)

    @m.tool
    def demo(params: VP) -> dict:
        return {"ok": True}

    async def val_case(args):
        async with Client(m) as c:
            r = await c.call_tool("demo", args, raise_on_error=False)
            return r.content[0].text if r.content else ""

    missing = asyncio.run(val_case({}))
    bad = asyncio.run(val_case({"params": {"campus": "way-too-long-value"}}))
    good = asyncio.run(val_case({"params": {"campus": "noida"}}))
    check("missing params -> named validation error",
          missing.startswith("Invalid parameters"))
    check("bad value -> names the offending field",
          bad.startswith("Invalid parameters") and "campus" in bad)
    check("valid params still succeed", "ok" in good)


def phase7_create_report():
    print("\nPHASE 7 — create_report (scope gate / agent proxy / config validation)")
    import asyncio
    import importlib

    import config as cfgmod
    from fastmcp.exceptions import ToolError

    import tools.actions as actions
    from tools.actions import CreateReportParams, _create_impl
    import tools.common as _common
    _orig_fs = _common.find_student
    _common.find_student = lambda svc, ref: {
        "student_id": "JN25PG067", "campus": "noida", "batch": "2025-27"
    } if ref == "JN25PG067" else None

    class Svc:
        def __init__(self, allowed, run="RID"):
            self.allowed = allowed
            self._run = run

        def campus_scope(self, requested):
            if self.allowed is None:
                return [requested] if requested else None
            return [requested] if requested in self.allowed else []

        def latest_run(self, campus, batch):
            return self._run if self.campus_scope(campus) != [] else None

    def run(svc, monkeypatch_client=None, **kw):
        if monkeypatch_client is not None:
            actions.httpx.AsyncClient = monkeypatch_client
        p = CreateReportParams(**{"campus": "noida", "batch": "2025-27",
                                  "student_id": "JN25PG067", **kw})
        return asyncio.run(_create_impl(svc, p))

    def raises(fn, exc):
        try:
            fn()
            return False
        except exc:
            return True

    # 1. campus outside grant -> PermissionError (masked to 'Access denied' upstream)
    check("campus outside grant denied",
          raises(lambda: run(Svc(["jaipur"])), PermissionError))

    # 2. unconfigured backend -> clean ToolError, no HTTP attempted
    for k in ("AGENT_API_BASE", "AGENT_ADMIN_USER", "AGENT_ADMIN_PASS"):
        os.environ.pop(k, None)
    importlib.reload(cfgmod)
    actions.settings = cfgmod.settings
    check("unconfigured backend -> clean ToolError",
          raises(lambda: run(Svc(None)), ToolError))

    # 3. configured: agent responses are proxied, internals filtered
    os.environ.update({"AGENT_API_BASE": "https://agent.example.com",
                       "AGENT_ADMIN_USER": "u", "AGENT_ADMIN_PASS": "p",
                       "AGENT_REPORT_QUEUE": "false"})
    importlib.reload(cfgmod)
    actions.settings = cfgmod.settings

    class FakeResp:
        def __init__(self, code, body):
            self.status_code, self._body = code, body

        def json(self):
            return self._body

    def client_for(resp, seen=None):
        class FakeClient:
            def __init__(self, **kw):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def post(self, url, **kw):
                if seen is not None:
                    seen.update({"url": url, **kw})
                return resp
        return FakeClient

    seen = {}
    body = {"ok": True, "student_id": "JN25PG067", "name": "Test Student",
            "attendance_pct": 81.2, "ce_pct": 64.0, "subjects": 7,
            "insight_headline": "Solid trimester", "from_cache": True,
            "path": "reports_out/internal/leak.html",
            "report_url": "https://reports.tryrehearsal.ai/rv/tok123"}
    out = run(Svc(None), client_for(FakeResp(200, body), seen))
    check("success proxies the agent result",
          out.get("generated") and out.get("report_url", "").endswith("/rv/tok123"))
    check("internal file path NOT leaked", "path" not in out)
    check("basic auth + refresh param sent",
          seen.get("auth") == ("u", "p")
          and seen.get("params", {}).get("refresh") == "false"
          and "/generate/noida/2025-27/JN25PG067" in seen.get("url", ""))

    out = run(Svc(None), client_for(FakeResp(404, {"detail": "student JN25PG067 not in run for noida/2025-27"})))
    check("agent 404 -> found:false with the agent's real reason",
          out.get("found") is False and "not in run" in out.get("note", ""))
    check("agent 5xx -> clean ToolError",
          raises(lambda: run(Svc(None), client_for(FakeResp(503, {}))), ToolError))

    # NEW: roster-first, data-second — a batch with no completed run never reaches the agent
    _orig_rm = _common.roster_member
    _common.roster_member = lambda svc, sid, c, b: {"student_id": sid, "student_name": "Divya U",
                                                     "campus": c, "batch": b}
    out = run(Svc(None, run=None))  # latest_run None -> pre-check fires, no HTTP
    check("enrolled but ungraded -> found:True + has_graded_data:False (not 'no student')",
          out.get("found") is True and out.get("has_graded_data") is False
          and "no graded data" in out.get("note", ""))
    _common.roster_member = lambda svc, sid, c, b: None
    out = run(Svc(None, run=None))
    check("genuinely not enrolled here -> found:false with 'not enrolled'",
          out.get("found") is False and "not enrolled" in out.get("note", "").lower())
    _common.roster_member = _orig_rm

    # NEW: optional params — omit student_id (or campus/batch) and a real graded target is chosen
    _orig_gs = _common.graded_scopes
    _orig_rgs = _common.random_gradeable_students
    _orig_crs = _common.cached_report_students
    _common.graded_scopes = lambda svc, campus=None, batch=None: [("noida", "2025-27", "RID")]
    _common.cached_report_students = lambda svc, rid, limit=5: []
    _common.random_gradeable_students = lambda svc, rid, n=4: ["JN25PG099"]
    body2 = {"ok": True, "student_id": "JN25PG099", "name": "Picked One",
             "report_url": "https://reports.tryrehearsal.ai/s/abc"}
    out = run(Svc(None), client_for(FakeResp(200, body2)), student_id=None)
    check("omit student_id -> auto-picks a graded student and generates",
          out.get("generated") and out.get("auto_selected") is True
          and out.get("student_id") == "JN25PG099")
    # first candidate 404s (unscored trimester) -> retries and lands on the next
    seq = [FakeResp(404, {"detail": "no scored subjects for X in trimester 4"}),
           FakeResp(200, body2)]

    def client_seq():
        class FakeClient:
            def __init__(self, **kw): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def post(self, url, **kw): return seq.pop(0)
        return FakeClient
    _common.random_gradeable_students = lambda svc, rid, n=4: ["BAD01", "JN25PG099"]
    out = run(Svc(None), client_seq(), student_id=None)
    check("auto-select retries past a 404 and returns the first that builds",
          out.get("generated") and out.get("student_id") == "JN25PG099")
    _common.random_gradeable_students = lambda svc, rid, n=4: ["JN25PG099"]
    _common.graded_scopes = lambda svc, campus=None, batch=None: []
    out = run(Svc(None), student_id=None)
    check("no graded data anywhere -> found:false 'no graded data'",
          out.get("found") is False and "graded data" in out.get("note", "").lower())
    # an explicit out-of-grant campus still denies, even with student omitted
    check("omit student_id but out-of-grant campus -> PermissionError",
          raises(lambda: run(Svc(["jaipur"]), student_id=None), PermissionError))
    _common.graded_scopes = _orig_gs
    _common.random_gradeable_students = _orig_rgs
    _common.cached_report_students = _orig_crs
    _common.find_student = _orig_fs

    # 4. boot validation: half config / non-https rejected
    def vraises():
        try:
            cfgmod.validate_config()
            return False
        except RuntimeError:
            return True

    os.environ.pop("AGENT_ADMIN_PASS", None)
    importlib.reload(cfgmod)
    check("half-configured agent backend rejected at boot", vraises())
    os.environ.update({"AGENT_ADMIN_PASS": "p",
                       "AGENT_API_BASE": "http://agent.example.com"})
    importlib.reload(cfgmod)
    check("non-https AGENT_API_BASE rejected at boot", vraises())

    for k in ("AGENT_API_BASE", "AGENT_ADMIN_USER", "AGENT_ADMIN_PASS"):
        os.environ.pop(k, None)
    importlib.reload(cfgmod)
    actions.settings = cfgmod.settings


def phase8_quiet_noisy_loggers():
    print("\nPHASE 8 — secret-bearing third-party loggers are capped at WARNING")
    import logging

    from security import quiet_noisy_loggers

    logging.getLogger().setLevel(logging.INFO)          # what server.py's basicConfig sets up
    logging.getLogger("httpx").setLevel(logging.INFO)   # simulate httpx's default chatter
    quiet_noisy_loggers()
    check("httpx capped at WARNING (tokeninfo URLs carry live access tokens)",
          not logging.getLogger("httpx").isEnabledFor(logging.INFO))
    check("httpcore capped at WARNING",
          not logging.getLogger("httpcore").isEnabledFor(logging.INFO))
    check("own moodle-mcp loggers still emit INFO",
          logging.getLogger("moodle-mcp.security").isEnabledFor(logging.INFO))


if __name__ == "__main__":
    phase1_latest_run_scope()
    phase2_key_role_detection()
    phase3_transport_guard()
    phase4_credential_hygiene()
    phase5_oauth_signin()
    phase6_oauth_compat()
    phase7_create_report()
    phase8_quiet_noisy_loggers()
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)
