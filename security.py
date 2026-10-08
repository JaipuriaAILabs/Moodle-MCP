"""Security layer — central auth resolution, rate limiting, audit logging, and a single
error boundary for every tool. Kept deliberately dependency-free (stdlib only) and bounded
(no unbounded dicts) so it is safe to run in a long-lived process.

Wiring (see server.py):
  * TransportGuard (ASGI)      — every /mcp request needs a valid bearer -> real 401; /health open.
  * GuardMiddleware (FastMCP)  — per tool-call: rate limit + audit + catch-all error boundary.
  * resolve_principal(token)   — cached, constant-time token -> principal.
"""
import hashlib
import hmac
import json
import logging
import time
from collections import OrderedDict, deque
from datetime import datetime, timedelta, timezone

log = logging.getLogger("moodle-mcp.security")

# Generic, internal-detail-free messages returned to the caller.
MSG_DENIED = "Access denied for your token."
MSG_RATE = "Rate limit exceeded — please slow down and retry shortly."
MSG_ERROR = "This query could not be completed right now. Please retry."
MSG_AUDIT = "The audit service is unavailable, so this request was not executed. Please retry."


def quiet_noisy_loggers() -> None:
    """Cap third-party HTTP client loggers at WARNING. At INFO, httpx logs every
    request URL — including Google's tokeninfo endpoint, whose query string carries
    the caller's LIVE access token — straight into the platform logs. Anyone with
    log access could replay that token for its remaining lifetime. Our own
    "moodle-mcp*" loggers are unaffected and stay at INFO."""
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)


# --- token resolution: cached map + constant-time compare --------------------
_token_cache: dict = {"sig": None, "map": {}}


def _token_map() -> dict:
    """settings.tokens(), recomputed only when the raw config changes (avoids json.loads/request)."""
    from config import settings
    sig = (settings.mcp_tokens_raw, settings.mcp_admin_token)
    if _token_cache["sig"] != sig:
        _token_cache["map"] = settings.tokens()
        _token_cache["sig"] = sig
    return _token_cache["map"]


def token_expired(exp) -> bool:
    """True if an ISO date/datetime `expires` value is in the past. A date-only
    value ('2026-12-31') expires at the END of that UTC day. Malformed values are
    treated as NOT expired at runtime (validate_config rejects bad formats at boot,
    so a runtime parse error shouldn't lock a valid token out and cause an outage)."""
    if not exp:
        return False
    try:
        s = str(exp).strip()
        if len(s) == 10:  # date only
            dt = datetime.fromisoformat(s).replace(tzinfo=timezone.utc) + timedelta(days=1)
        else:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) >= dt
    except Exception:  # noqa: BLE001
        return False


def resolve_principal(token: str):
    """token -> principal dict, or None. Constant-time over the known tokens (no early-exit
    timing signal). Returns a copy so callers can't mutate the shared config. An expired
    token (optional per-token `expires`) resolves to None, so it can be revoked by date
    without a redeploy."""
    if not token:
        return None
    matched = None
    for known, principal in _token_map().items():
        if hmac.compare_digest(token, known):
            matched = principal
    if matched is None:
        return None
    if token_expired(matched.get("expires")):
        return None
    return dict(matched)


def bearer_of(headers: dict) -> str:
    """Extract the bearer token from a header mapping (case-insensitive)."""
    auth = headers.get("authorization") or headers.get("Authorization") or ""
    return auth[7:].strip() if auth.lower().startswith("bearer ") else ""


# --- OAuth (Google sign-in) principal resolution -----------------------------
def _role_for(campuses) -> str:
    """A sensible role when none is stored: an all-campus grant is cross-campus,
    a scoped grant is faculty. (mcp_faculty rows carry an explicit role; this only
    covers env/default grants that predate the role concept.)"""
    return "cross_campus" if campuses is None else "faculty"


def _student_principal(claims: dict, email: str):
    """A session hard-bounded to the student's OWN student_id — same tools, but every query is
    filtered to self, so one student can never read another's data. Returns None when student
    self-access is disabled or the identity can't be resolved (the caller then denies — never an
    unbounded student)."""
    from config import settings
    if not settings.student_self_access:
        return None
    import faculty as registry
    ident = registry.student_identity(email)
    # The identity must resolve to at least one complete (id, campus, batch) — student_identity
    # already denies any incomplete roster row. A partial principal could expose unowned scope
    # metadata (runs, course names, trimester availability), so fail closed.
    if not ident or not all(ident.get(k) for k in ("student_ids", "campuses", "batches")):
        return None
    return {"name": claims.get("name") or email, "email": email,
            # Scope spans ALL of this person's enrolment ids (and their campuses/batches) —
            # still strictly their own rows, never another student's.
            "campuses": list(ident["campuses"]),
            "role": "student",
            "student_ids": list(ident["student_ids"]),
            "student_id": ident["primary_id"],      # primary (latest): create_report target + cache key
            "batches": list(ident["batches"]),
            "batch": ident["primary_batch"],         # primary batch: default when none is named
            "can_generate": True}


def _pending_principal(claims: dict, email: str) -> dict:
    """A limited session for a verified-but-unprovisioned account: no campus scope and no
    report generation, so every DATA tool denies (campus_scope == []), but request_access
    stays callable so the user can self-request a campus+role for admin approval."""
    return {"name": claims.get("name") or email, "email": email,
            "campuses": [], "role": "pending", "can_generate": False}


def _registry_principal(claims: dict, email: str, domain: str, *, quiet: bool = False,
                        pending_ok: bool = False):
    """The authoritative RBAC resolution for ANY account (Jaipuria included): the env
    override, mcp_faculty grant, student classification, domain gate, and configured
    default — in that order. Returns a principal dict carrying role + can_generate, or
    None (deny). This is what ENFORCE mode returns, and what SHADOW mode logs. Kept
    separate so the historical all-access path (OFF/SHADOW for Jaipuria) is untouched.
    `quiet` suppresses the per-denial warnings (used by shadow, which logs its own line).
    `pending_ok` turns the final default-deny into a limited 'pending' session (self-service)."""
    from config import settings
    import faculty as registry

    # 1. Env override (admin break-glass) — an EXPLICITLY listed email is allowed
    #    regardless of domain (so a named guest can be granted without opening the gate).
    override = settings.faculty().get(email)
    if override is not None:
        camp = override.get("campuses")
        return {"name": override.get("name") or claims.get("name") or email, "email": email,
                "campuses": camp, "role": _role_for(camp), "can_generate": True}

    # 2. mcp_faculty DB row — the authoritative educator allowlist (role + campus scope).
    #    Checked BEFORE the student roster so a dual-role account (e.g. a PhD student who is
    #    also a TA) is treated as the educator they were granted. Writes are service-role only.
    grant = registry.faculty_grant(email)
    if grant is not None:
        return {"name": grant.get("name") or claims.get("name") or email, "email": email,
                "campuses": grant["campuses"], "role": grant.get("role") or "faculty",
                "can_generate": grant.get("can_generate", True)}

    # 3. Student roster -> a session hard-bounded to their OWN data (self only), or deny.
    if registry.is_student(email):
        return _student_principal(claims, email)

    # 4. Domain gate — only the DEFAULT-grant path is domain-restricted. Accept an
    #    allowed domain AND its subdomains; the leading dot blocks look-alikes.
    allowed = settings.oauth_allowed_domains()
    if not any(domain == a or domain.endswith("." + a) for a in allowed):
        if not quiet:
            subject = hashlib.sha256(email.encode()).hexdigest()[:12]
            log.warning("oauth sign-in rejected: subject=%s domain %r not allowed", subject, domain)
        return None

    # 5. Default grant for an allowed-domain account with no explicit row.
    default = settings.oauth_default_campuses()
    if default == "deny":
        if pending_ok:
            return _pending_principal(claims, email)   # self-service: limited pending session
        if not quiet:
            subject = hashlib.sha256(email.encode()).hexdigest()[:12]
            log.warning("oauth sign-in rejected: subject=%s has no explicit grant", subject)
        return None
    return {"name": claims.get("name") or email, "email": email, "campuses": default,
            "role": _role_for(default), "can_generate": True}


def _shadow_decision(principal) -> dict:
    """Bounded, non-PII representation of the decision ENFORCE would make."""
    if principal is None:
        return {"outcome": "deny", "role": "none", "campus_scope": "none",
                "can_generate": False}
    role = str(principal.get("role") or _role_for(principal.get("campuses")))
    campuses = principal.get("campuses")
    scope = "all" if campuses is None else sorted(
        {str(c).strip().lower() for c in campuses if str(c).strip()})
    outcome = "pending" if role == "pending" else "allow"
    return {"outcome": outcome, "role": role[:32], "campus_scope": scope,
            "can_generate": bool(principal.get("can_generate", outcome == "allow"))}


def _log_shadow(claims: dict, email: str, domain: str) -> dict:
    """SHADOW mode: compute what ENFORCE *would* do for this Jaipuria caller and log it,
    without changing the served (all-access) principal. Never raises — a shadow-logging
    failure must not affect a real sign-in. Returns a bounded decision so each subsequent
    tool audit record carries the shadow outcome without raw identity."""
    try:
        from config import settings
        subject = hashlib.sha256(email.encode()).hexdigest()[:12]
        would = _registry_principal(claims, email, domain, quiet=True,
                                    pending_ok=settings.self_service_access)
        decision = _shadow_decision(would)
        if decision["outcome"] == "deny":
            log.info("rbac.shadow subject=%s would=DENY (no grant; self-service off)", subject)
        elif decision["outcome"] == "pending":
            log.info("rbac.shadow subject=%s would=PENDING (no grant; may self-request access)", subject)
        else:
            log.info("rbac.shadow subject=%s would=ALLOW role=%s campuses=%s can_generate=%s",
                     subject, decision["role"],
                     decision["campus_scope"] if isinstance(decision["campus_scope"], str)
                     else ",".join(decision["campus_scope"]), decision["can_generate"])
        return decision
    except Exception:  # noqa: BLE001 — shadow observation must never break auth
        log.warning("rbac.shadow computation failed", exc_info=True)
        return {"outcome": "error", "role": "unknown", "campus_scope": "none",
                "can_generate": False}


def _claim_true(value) -> bool:
    """Whether a Google claim means 'yes'. FastMCP's GoogleProvider populates ``email_verified``
    from either a bool (OIDC claim / userinfo v2) OR the STRING ``"true"`` (Google's tokeninfo
    endpoint returns string-encoded values). Accept only unambiguous truthy encodings; everything
    else — ``False``, ``"false"``, ``None``, ``""``, ``0`` — is a 'no'."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int):   # bool is handled above, so this is a genuine int
        return value == 1
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes")
    return False


def principal_from_claims(claims: dict):
    """Resolve a verified Google identity to a principal (or None = deny), governed by
    MCP_RBAC_MODE. OFF (default): verified Jaipuria accounts get all-campus access, the
    historical policy. SHADOW: same served access, but the would-be enforced grant is
    logged. ENFORCE: the mcp_faculty registry is authoritative for educators (role + campus
    scope); students are self-scoped when enabled and denied otherwise; unlisted accounts
    become pending or are denied. Non-Jaipuria accounts always
    follow the registry ladder, in every mode (they were never all-access)."""
    from config import settings
    email = str(claims.get("email") or "").strip().lower()
    if email.count("@") != 1:
        return None
    # Email must be verified. FastMCP's GoogleProvider sets `email_verified` from Google's tokeninfo
    # endpoint, which returns the STRING "true" (not a bool); the userinfo v2 fallback
    # (`verified_email`) is a real bool. Accept any unambiguous truthy encoding from either source —
    # anything else stays unverified. (Pre-3.x FastMCP handed us a bool here, hence the old strict
    # `is True`; 3.4.5 changed the claim shape, which silently rejected every verified sign-in.)
    verified = claims.get("email_verified")
    if not _claim_true(verified):   # Google userinfo v2 spells it verified_email
        verified = (claims.get("google_user_data") or {}).get("verified_email")
    if not _claim_true(verified):
        subject = hashlib.sha256(email.encode()).hexdigest()[:12]
        log.warning("oauth sign-in rejected: email not verified (subject=%s)", subject)
        return None
    # Defense-in-depth (confused-deputy / token substitution): when the token's Google audience is
    # present it MUST be our OAuth client — a token minted for a different Google app (even with the
    # same scopes) is never honored. FastMCP 3.4.5 exposes it as the flat `aud` claim; older builds
    # nested it under `google_token_info` — check both. Absent audience can't be asserted and is
    # allowed: the OAuthProxy binds it by doing the code exchange with our own credentials.
    expected_aud = settings.google_oauth_client_id
    token_aud = claims.get("aud") or (claims.get("google_token_info") or {}).get("audience")
    if expected_aud and token_aud and token_aud != expected_aud:
        subject = hashlib.sha256(email.encode()).hexdigest()[:12]
        log.warning("oauth token audience mismatch (subject=%s) — rejecting", subject)
        return None
    domain = email.rsplit("@", 1)[1]
    is_jaipuria = domain == "jaipuria.ac.in" or domain.endswith(".jaipuria.ac.in")

    if is_jaipuria:
        mode = settings.rbac_mode()
        if mode == "enforce":
            return _registry_principal(claims, email, domain,
                                       pending_ok=settings.self_service_access)
        # OFF / SHADOW: historical all-campus grant — but a STUDENT roster email must NEVER
        # receive it: the cohort-wide grant would let one student read every other student's
        # data. An explicit educator grant (env or DB) still wins for a genuine dual-role TA,
        # matching ENFORCE's documented classification precedence. We only pay the extra registry
        # lookup after a roster hit, so the historical non-student OFF path remains DB-free.
        import faculty as registry
        if email not in settings.faculty() and registry.is_student(email):
            if registry.faculty_grant(email) is None:
                # A student NEVER gets the cohort-wide grant. With student self-access on they get
                # a session hard-bounded to their own student_id; otherwise deny.
                sp = _student_principal(claims, email)
                subject = hashlib.sha256(email.encode()).hexdigest()[:12]
                if sp is None:
                    log.warning("student roster email denied (subject=%s, mode=%s)", subject, mode)
                elif mode == "shadow":
                    sp["_rbac_shadow"] = _shadow_decision(sp)
                return sp
        # OFF / SHADOW: preserve the historical all-campus grant for non-student accounts.
        principal = {"name": claims.get("name") or email, "email": email,
                     "campuses": None, "role": "cross_campus", "can_generate": True}
        if mode == "shadow":
            principal["_rbac_shadow"] = _log_shadow(claims, email, domain)
        return principal

    # Non-Jaipuria accounts: always the registry ladder (unchanged in every mode — they
    # were never covered by the all-access policy).
    return _registry_principal(claims, email, domain)


def resolve_oauth_principal():
    """Principal behind the FastMCP-issued OAuth token on the current request, or
    None when there is no (valid) OAuth token in context. Never raises."""
    try:
        from fastmcp.server.dependencies import get_access_token
        token = get_access_token()
    except Exception:  # noqa: BLE001 - no auth context / no token
        return None
    if token is None:
        return None
    claims = getattr(token, "claims", None) or {}
    return principal_from_claims(claims)


def resolve_request_principal(headers: dict | None = None):
    """Resolve exactly one configured authentication mode.

    Google OAuth mode is deliberately exclusive: a static token must never become a
    fallback when the OAuth context is absent or invalid. Static tokens remain available
    only for deployments that have not configured Google OAuth at all.
    """
    from config import settings
    if settings.oauth_enabled():
        return resolve_oauth_principal()
    return resolve_principal(bearer_of(headers or {}))


# --- bounded sliding-window rate limiter -------------------------------------
class RateLimiter:
    """Per-key sliding window. Bounded in memory (LRU-evicts idle keys) — OOM-safe."""

    def __init__(self, limit: int, window: float, maxkeys: int = 16384):
        self.limit = max(1, int(limit))
        self.window = float(window)
        self.maxkeys = maxkeys
        self._hits: "OrderedDict[str, deque]" = OrderedDict()

    def allow(self, key: str) -> tuple[bool, float]:
        """(allowed, retry_after_seconds). Records the hit when allowed."""
        now = time.monotonic()
        dq = self._hits.get(key)
        if dq is None:
            dq = deque()
            self._hits[key] = dq
        self._hits.move_to_end(key)
        while dq and now - dq[0] > self.window:
            dq.popleft()
        if len(dq) >= self.limit:
            return False, max(0.0, self.window - (now - dq[0]))
        dq.append(now)
        while len(self._hits) > self.maxkeys:
            self._hits.popitem(last=False)
        return True, 0.0


class SharedRateLimiter:
    """Redis-backed limiter with a bounded local fallback for dependency outages."""

    _SCRIPT = """
local current = redis.call('INCR', KEYS[1])
if current == 1 then redis.call('PEXPIRE', KEYS[1], ARGV[1]) end
local ttl = redis.call('PTTL', KEYS[1])
return {current, ttl}
"""

    def __init__(self, limit: int, window: float, *, maxkeys: int = 16384,
                 redis_url: str = "", prefix: str = "moodle-mcp"):
        self.limit = max(1, int(limit))
        self.window = float(window)
        self.local = RateLimiter(limit, window, maxkeys=maxkeys)
        self.prefix = prefix
        self.redis = None
        if redis_url:
            try:
                import redis.asyncio as redis
                self.redis = redis.from_url(redis_url, decode_responses=False)
            except Exception:  # noqa: BLE001 — local limiter remains active
                log.exception("Redis rate limiter initialization failed; using local fallback")

    async def allow(self, key: str) -> tuple[bool, float]:
        if self.redis is None:
            return self.local.allow(key)
        digest = hashlib.sha256(key.encode()).hexdigest()
        redis_key = f"{self.prefix}:{digest}"
        try:
            count, ttl_ms = await self.redis.eval(
                self._SCRIPT, 1, redis_key, max(1, round(self.window * 1000)))
            return int(count) <= self.limit, max(0.0, int(ttl_ms) / 1000)
        except Exception:  # noqa: BLE001 — retain protection during Redis outages
            log.warning("Redis rate limiter unavailable; using local fallback")
            return self.local.allow(key)


# --- audit ------------------------------------------------------------------
def audit(tool: str, principal, *, ok: bool, scope=None, note: str = "") -> None:
    """Legacy synchronous fallback; stores only a pseudonymous subject."""
    from audit_store import principal_subject
    who = principal_subject(principal)
    log.info("audit tool=%s subject=%s ok=%s scope=%s%s",
             tool, who[:16], "1" if ok else "0", scope or "-",
             f" note={note}" if note else "")


# --- ASGI transport gate ----------------------------------------------------
class SecurityHeaders:
    """Apply baseline browser and cache protections to every HTTP response."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            return await self.app(scope, receive, send)

        async def protected_send(message):
            if message.get("type") == "http.response.start":
                headers = list(message.get("headers") or [])
                present = {k.lower() for k, _ in headers}
                additions = {
                    b"x-content-type-options": b"nosniff",
                    b"x-frame-options": b"DENY",
                    b"referrer-policy": b"no-referrer",
                    b"strict-transport-security": b"max-age=31536000; includeSubDomains",
                }
                if scope.get("path") in ("/mcp", "/token", "/authorize", "/consent",
                                         "/mcp/consent", "/auth/callback"):
                    additions[b"cache-control"] = b"no-store"
                for key, value in additions.items():
                    if key not in present:
                        headers.append((key, value))
                message = dict(message)
                message["headers"] = headers
            await send(message)

        return await self.app(scope, receive, protected_send)


class HostGuard:
    """Reject alternate public origins so edge controls cannot be bypassed."""

    def __init__(self, app, allowed_hosts: list[str] | None = None):
        self.app = app
        self.allowed_hosts = {host.lower() for host in (allowed_hosts or [])}

    async def __call__(self, scope, receive, send):
        if (scope.get("type") != "http" or not self.allowed_hosts
                or scope.get("path") == "/health"):
            return await self.app(scope, receive, send)
        headers = {k.decode().lower(): v.decode() for k, v in (scope.get("headers") or [])}
        host = headers.get("host", "").split(":", 1)[0].lower()
        if host not in self.allowed_hosts:
            return await _send_json(send, 421, {"error": "misdirected_request"})
        return await self.app(scope, receive, send)


async def _send_json(send, status: int, payload: dict, extra: dict | None = None) -> None:
    body = json.dumps(payload).encode()
    headers = [(b"content-type", b"application/json"),
               (b"content-length", str(len(body)).encode())]
    if extra:
        headers += list(extra.items())
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})


def client_ip(scope, headers: dict) -> str:
    """Best-effort client IP for pre-auth rate limiting. Uses the LAST (rightmost) hop
    of X-Forwarded-For — the one the trusted proxy (Render) appends. The leftmost hop is
    client-supplied and therefore spoofable; keying the per-IP limiter on it would let a
    flood/guessing client mint a fresh budget per forged IP. Falls back to the socket
    peer. (Assumes a single trusted proxy in front, as on Render.)"""
    xff = headers.get("x-forwarded-for")
    if xff:
        hops = [h.strip() for h in xff.split(",") if h.strip()]
        if hops:
            return hops[-1]
    client = scope.get("client")
    return client[0] if client else "unknown"


class TransportGuard:
    """First line of defense on every /mcp request, before JSON-RPC:
      * cap the declared and actually received request body -> 413, so chunked
        or dishonest Content-Length requests cannot pressure memory;
      * per-IP rate limit -> 429, to blunt unauthenticated floods / token guessing;
      * require a valid bearer -> real 401, so tool enumeration is impossible.
    /health stays open; OPTIONS (credential-free CORS preflight) passes through;
    non-http scopes (lifespan) pass untouched. All guards fail open on internal
    error so the transport itself never wedges a legitimate request."""

    def __init__(self, app, open_paths=("/health",), max_body: int = 262144,
                 ip_rate_limit: int = 240, ip_window: float = 60.0,
                 check_bearer: bool = True, maxkeys: int = 16384,
                 redis_url: str = "", trust_proxy_headers: bool = False):
        # check_bearer=False (OAuth mode): FastMCP's auth layer owns token
        # validation and the 401 + WWW-Authenticate resource-metadata handshake
        # the MCP OAuth discovery flow depends on, and the OAuth endpoints
        # (/.well-known/*, /register, /authorize, /token, /auth/callback) must be
        # reachable pre-auth. Body cap + per-IP limiting stay on either way.
        self.app = app
        self.open_paths = set(open_paths)
        self.max_body = max_body
        self.ip_limiter = SharedRateLimiter(
            ip_rate_limit, ip_window, maxkeys=maxkeys, redis_url=redis_url,
            prefix="moodle-mcp:ip") if ip_rate_limit else None
        self.check_bearer = check_bearer
        self.trust_proxy_headers = trust_proxy_headers

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            return await self.app(scope, receive, send)
        path = scope.get("path", "")
        if path in self.open_paths or scope.get("method") == "OPTIONS":
            return await self.app(scope, receive, send)
        headers = {k.decode().lower(): v.decode() for k, v in (scope.get("headers") or [])}

        # 1. Fast reject a declared oversized body. We also count the ASGI stream
        # below because Content-Length may be absent (chunked) or dishonest.
        cl = headers.get("content-length")
        if cl is not None:
            try:
                if int(cl) > self.max_body:
                    return await _send_json(send, 413, {"error": "request_too_large"})
            except ValueError:
                pass

        # 2. per-IP pre-auth rate limit (fail-open)
        if self.ip_limiter is not None:
            try:
                ip = client_ip(scope, headers) if self.trust_proxy_headers else (
                    scope.get("client", ("unknown",))[0] if scope.get("client") else "unknown")
                allowed, _retry = await self.ip_limiter.allow(ip)
                if not allowed:
                    return await _send_json(send, 429, {"error": "rate_limited"})
            except Exception:  # noqa: BLE001
                pass

        # 3. auth (static-token mode only; OAuth mode delegates to FastMCP auth)
        if self.check_bearer:
            token = bearer_of(headers)
            if resolve_principal(token) is None:
                return await _send_json(send, 401, {"error": "unauthorized"},
                                        extra={b"www-authenticate": b'Bearer realm="moodle-mcp"'})

        # 4. Count the actual stream before downstream parsers buffer it. MCP and
        # OAuth mutation endpoints use POST; limiting other methods too makes the
        # guard safe if a new endpoint is added later.
        if scope.get("method") in ("POST", "PUT", "PATCH"):
            upstream_receive = receive
            messages = []
            total = 0
            while True:
                message = await upstream_receive()
                messages.append(message)
                if message.get("type") != "http.request":
                    break
                total += len(message.get("body", b""))
                if total > self.max_body:
                    return await _send_json(send, 413, {"error": "request_too_large"})
                if not message.get("more_body"):
                    break
            index = 0

            async def replay_receive():
                nonlocal index
                if index < len(messages):
                    message = messages[index]
                    index += 1
                    return message
                # Streamable HTTP keeps listening for the real peer disconnect
                # while it sends the response. A synthetic disconnect here makes
                # FastMCP abort with "ASGI callable returned without completing
                # response" immediately after authenticated initialize.
                return await upstream_receive()

            receive = replay_receive

        return await self.app(scope, receive, send)


# --- FastMCP per-call middleware: rate limit + audit + error boundary --------
try:
    from opentelemetry import trace as _otel_trace
    from opentelemetry.trace import SpanKind as _SpanKind
    from opentelemetry.trace import StatusCode as _StatusCode
except Exception:  # noqa: BLE001 — OTel API optional; instrumentation no-ops without it
    _otel_trace = _SpanKind = _StatusCode = None


def _start_tool_span(name: str, headers=None):
    """A SERVER span for a tool call, or None when OTel isn't importable. With no
    provider installed (telemetry disabled) this is a cheap non-recording span. The
    tracer is fetched lazily so it always uses whatever provider setup_telemetry set.
    When ``headers`` carry a W3C traceparent, the span is parented to the caller's
    (client harness) span so a distributed trace stitches end-to-end."""
    if _otel_trace is None:
        return None
    span = None
    try:
        import telemetry
        ctx = telemetry.extract_context(headers) if headers else None
        span = _otel_trace.get_tracer("moodle-mcp").start_span(
            f"mcp.tool.{name}", kind=_SpanKind.SERVER, context=ctx)
        span.set_attribute("mcp.tool", name)
        return span
    except Exception:  # noqa: BLE001
        # If a started span failed mid-setup, end it so it can't leak/never-flush.
        if span is not None:
            try:
                span.end()
            except Exception:  # noqa: BLE001
                pass
        return None


def _authorization_attributes(principal) -> dict:
    """Low-cardinality, non-PII RBAC dimensions for traces and metrics."""
    if not isinstance(principal, dict):
        return {"mcp.role": "anonymous", "mcp.rbac_shadow_outcome": "not_applicable"}
    role = str(principal.get("role") or _role_for(principal.get("campuses"))).lower()
    allowed_roles = {"admin", "cross_campus", "campus_admin", "faculty", "viewer",
                     "student", "pending"}
    shadow = principal.get("_rbac_shadow")
    shadow_outcome = (str(shadow.get("outcome")).lower()
                      if isinstance(shadow, dict) and shadow.get("outcome") else "not_applicable")
    if shadow_outcome not in {"allow", "deny", "pending", "error", "not_applicable"}:
        shadow_outcome = "error"
    return {"mcp.role": role if role in allowed_roles else "other",
            "mcp.rbac_shadow_outcome": shadow_outcome}


def _end_tool_span(span, outcome: str, error_code, scope, duration_s=None, tool="?",
                   principal=None) -> None:
    # Emit the aggregated tool metric regardless of whether a span exists — metrics are
    # a separate signal (host/process gauges + counters) that must not be gated on the
    # tracer, and aren't subject to trace sampling.
    try:
        import telemetry
        telemetry.record_tool_metric(tool, outcome, error_code, scope, duration_s,
                                     _authorization_attributes(principal))
    except Exception:  # noqa: BLE001
        pass
    if span is None:
        return
    try:
        span.set_attribute("mcp.outcome", outcome)
        if error_code:
            span.set_attribute("mcp.error_code", error_code)
        if scope:
            span.set_attribute("mcp.campus_scope", scope)
        for key, value in _authorization_attributes(principal).items():
            span.set_attribute(key, value)
        if outcome == "failure":
            span.set_status(_StatusCode.ERROR, error_code or "error")
    except Exception:  # noqa: BLE001
        pass
    finally:
        try:
            span.end()
        except Exception:  # noqa: BLE001
            pass


def build_middleware(rate_limit: int, window: float):
    from fastmcp.exceptions import ToolError, ValidationError as FastMCPValidationError
    from fastmcp.server.dependencies import get_http_headers
    from fastmcp.server.middleware import Middleware
    from pydantic import ValidationError as PydanticValidationError

    from config import settings
    limiter = SharedRateLimiter(rate_limit, window, maxkeys=settings.rate_limit_max_keys,
                                redis_url=settings.redis_url,
                                prefix="moodle-mcp:principal")

    def _principal():
        try:
            return resolve_request_principal(get_http_headers() or {})
        except Exception:  # noqa: BLE001 - never let auth-introspection break a call
            return None

    def _rate_key():
        # Key the limiter by the TOKEN (hashed), not the principal name — two
        # tokens that happen to share a name must not share a rate budget. The
        # hash keeps raw tokens out of the in-memory limiter map.
        try:
            tok = bearer_of(get_http_headers() or {})
            return hashlib.sha256(tok.encode()).hexdigest()[:16] if tok else "anon"
        except Exception:  # noqa: BLE001
            return "anon"

    def _args(context):
        try:
            return getattr(context.message, "arguments", None)
        except Exception:  # noqa: BLE001
            return None

    def _scope(context, principal):
        """Resolved effective campus scope, not merely the caller's requested value."""
        try:
            args = _args(context) or {}
            p = args.get("params") if isinstance(args, dict) else None
            requested = (p or {}).get("campus") if isinstance(p, dict) else None
            requested = str(requested).strip().lower() if requested else None
            if not isinstance(principal, dict):
                return "none"
            allowed = principal.get("campuses")
            if allowed is None:
                return requested or "all"
            allowed = sorted({str(c).strip().lower() for c in allowed if str(c).strip()})
            if not allowed:
                return "none"
            if requested:
                return requested if requested in allowed else "none"
            return ",".join(allowed)
        except Exception:  # noqa: BLE001
            return "none"

    def _source_ip(headers):
        # LAST (rightmost) hop of X-Forwarded-For — the trusted-proxy-appended value;
        # the leftmost is client-supplied/spoofable so it must not be logged as the
        # source of record. Only stored when MCP_CAPTURE_CLIENT_IP is on.
        try:
            xff = headers.get("x-forwarded-for")
            if not xff:
                return None
            hops = [h.strip() for h in xff.split(",") if h.strip()]
            return hops[-1] if hops else None
        except Exception:  # noqa: BLE001
            return None

    class GuardMiddleware(Middleware):
        async def on_initialize(self, context, call_next):
            # Record session establishment so the audit ledger reflects CONNECTIONS, not
            # only tool invocations (AIA-1210 connection counts / user-email traceability).
            # Best-effort and post-hoc: a connect is never blocked or failed by auditing.
            principal = _principal()
            headers = get_http_headers() or {}
            started = time.monotonic()
            result = await call_next(context)
            try:
                from audit_store import record_tool_call
                await record_tool_call(tool="connect", principal=principal, ok=True,
                                       started=started, headers=headers,
                                       source_ip=_source_ip(headers))
            except Exception:  # noqa: BLE001 — connection audit must never break a connect
                log.warning("connect audit failed", exc_info=True)
            return result

        async def on_call_tool(self, context, call_next):
            name = getattr(context.message, "name", "?")
            principal = _principal()
            headers = get_http_headers() or {}
            started = time.monotonic()
            # Capture context — enriched into metadata only when the MCP_CAPTURE_* flags
            # are on (see audit_store.build_metadata); otherwise these are inert.
            cap = {"arguments": _args(context), "source_ip": _source_ip(headers)}
            # headers → parent trace context (W3C traceparent) when the caller sent one.
            span = _start_tool_span(name, headers)  # None unless OTel tracing is enabled
            outcome, err = "failure", "error"      # finally records the final outcome
            from audit_store import record_tool_call
            try:
                if settings.require_audit:
                    recorded = await record_tool_call(
                        tool=name, principal=principal, ok=None, started=started,
                        headers=headers, scope=_scope(context, principal), **cap)
                    if not recorded:
                        err = "audit_unavailable"
                        raise ToolError(MSG_AUDIT)
                ok, _retry = await limiter.allow(_rate_key())
                if not ok:
                    await record_tool_call(tool=name, principal=principal, ok=False,
                                           started=started, headers=headers,
                                           scope=_scope(context, principal), error_code="rate_limited", **cap)
                    err = "rate_limited"
                    raise ToolError(MSG_RATE)
                try:
                    result = await call_next(context)
                    # The call succeeded — lock in the outcome BEFORE the audit write so a
                    # (defensive) audit failure can never be re-caught below and reported to
                    # the caller as a tool error. record_tool_call is itself exception-safe.
                    outcome, err = "success", None
                    # In-server PII redaction (DPDP / AIA-1356): pseudonymise student identities
                    # BEFORE the result leaves the server or is written to the audit ledger — zero
                    # trust in the client, no reverse `_identity` map ever emitted. shadow logs the
                    # coverage it WOULD remove and serves raw; enforce returns the redacted result.
                    # Fail-open in shadow (never perturb traffic); fail-closed in enforce (a redaction
                    # bug must deny, not leak). record_tool_call then captures what was actually served.
                    pii_mode = settings.pii_redaction_mode()
                    if pii_mode != "off":
                        try:
                            import pii as _pii
                            redacted, _stats = _pii.redact(result)
                            if pii_mode == "shadow":
                                if any(_stats.values()):
                                    log.info("pii.shadow tool=%s ids=%d freetext=%d leak=%d", name,
                                             _stats["ids"], _stats["freetext"], _stats["leak"])
                            else:  # enforce
                                result = redacted
                        except Exception:  # noqa: BLE001
                            if pii_mode == "enforce":
                                log.exception("pii redaction failed for %s — failing closed", name)
                                outcome, err = "failure", "pii_error"
                                raise ToolError(MSG_ERROR)
                            log.warning("pii shadow redaction failed for %s", name, exc_info=True)
                    try:
                        await record_tool_call(tool=name, principal=principal, ok=True,
                                               started=started, headers=headers,
                                               scope=_scope(context, principal), result=result, **cap)
                    except Exception:  # noqa: BLE001 — success audit must never fail the call
                        log.warning("post-success audit for %s failed", name, exc_info=True)
                    return result
                except ToolError:
                    await record_tool_call(tool=name, principal=principal, ok=False,
                                           started=started, headers=headers,
                                           scope=_scope(context, principal), error_code="tool_error", **cap)
                    err = "tool_error"
                    raise  # already a clean, caller-safe message
                except (PydanticValidationError, FastMCPValidationError) as e:
                    # Caller sent bad/missing parameters. Tell them WHICH — this is
                    # their own input, not an internal detail — so an agent can fix
                    # the call instead of uselessly retrying a "server error".
                    # FastMCP 3 wraps Pydantic's error in its own ValidationError;
                    # FastMCP 2 passed the Pydantic exception through directly.
                    detail = e.__cause__ if isinstance(e.__cause__, PydanticValidationError) else e
                    errors = detail.errors() if isinstance(detail, PydanticValidationError) else []
                    first = (errors or [{}])[0]
                    loc = ".".join(str(x) for x in first.get("loc", ())) or "params"
                    await record_tool_call(tool=name, principal=principal, ok=False,
                                           started=started, headers=headers,
                                           scope=_scope(context, principal), error_code="bad_params", **cap)
                    err = "bad_params"
                    raise ToolError(f"Invalid parameters — {loc}: "
                                    f"{first.get('msg') or str(e).splitlines()[0] or 'validation failed'}")
                except PermissionError:
                    await record_tool_call(tool=name, principal=principal, ok=False,
                                           started=started, headers=headers,
                                           scope=_scope(context, principal), error_code="unauthorized", **cap)
                    err = "unauthorized"
                    raise ToolError(MSG_DENIED)
                except Exception:  # noqa: BLE001 - the point is to never leak internals
                    log.exception("tool %s failed", name)
                    await record_tool_call(tool=name, principal=principal, ok=False,
                                           started=started, headers=headers,
                                           scope=_scope(context, principal), error_code="internal_error", **cap)
                    err = "internal_error"
                    raise ToolError(MSG_ERROR)
            finally:
                _end_tool_span(span, outcome, err, _scope(context, principal),
                               duration_s=time.monotonic() - started, tool=name,
                               principal=principal)

    return GuardMiddleware()
