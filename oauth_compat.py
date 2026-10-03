"""Compatibility layer that makes the OAuth flow robust for EVERY connecting host.

Two real-world failure modes were observed with Claude.ai's connector (and would hit
any faculty member adding the server):

1. "No MCP server was found at the provided URL" — the user enters the bare domain
   without the /mcp path. OAuth completes, then the host POSTs to "/" and gets 404.
   Fix: PathAliases rewrites "/" -> "/mcp" (and the root discovery document to its
   path-scoped variant) so both URL forms work.

2. "invalid_grant / mcp_token_exchange_failed" — Claude.ai's backend registers
   SEVERAL dynamic clients concurrently (one per node), authorizes with one and
   redeems the code as another. FastMCP binds each code to the registering client
   and rejects the mismatch. Fix: TolerantGoogleProvider accepts the exchange from
   a different *registered* client as long as the code carries a PKCE challenge —
   the MCP token handler still verifies code_verifier, redirect_uri, expiry, and
   one-time use after this hook, so the cryptographic binding (RFC 7636) that
   actually protects the code is fully retained. Codes without PKCE keep the
   strict client check.

Pinned to fastmcp==3.4.5 (requirements.txt): the tolerant override touches only the
authorization-code store and is covered by cross-client/PKCE tests. FastMCP's built-in
authorization consent remains explicitly enabled to preserve the v3 confused-deputy fix.
"""
import ipaddress
import json
import logging
import time
from urllib.parse import parse_qsl, urlencode, urlsplit

from fastmcp.server.auth.providers.google import GoogleProvider
from mcp.server.auth.provider import AuthorizationCode
from mcp.shared.auth import OAuthClientInformationFull
from pydantic import AnyUrl

log = logging.getLogger("moodle-mcp.oauth_compat")

# The Google provider only accepts the fully-qualified userinfo scope URLs; a client
# that requests the short OIDC names 'email' / 'profile' is rejected at DCR ("scopes
# are not valid") and at /authorize ("client was not registered with scope email").
# Codex uses the server-advertised scopes so it's unaffected, but any client that
# sends the short names would fail. Normalize them to the full URLs at the edge so
# every client works, whichever form it sends. 'openid' is already accepted as-is.
_SCOPE_ALIASES = {
    "email": "https://www.googleapis.com/auth/userinfo.email",
    "profile": "https://www.googleapis.com/auth/userinfo.profile",
}
# Endpoints that carry a `scope`: /authorize (query), /register + /token (body).
_SCOPE_PATHS = ("/authorize", "/register", "/token")


def _safe_redirect_uri(uri: object, allowed_https_hosts: set[str] | None = None) -> bool:
    """Allow browser HTTPS callbacks and native-client loopback HTTP only.

    Dynamic client registration is public by design, so accepting active-content
    schemes such as ``javascript:`` would let an untrusted registrant turn the
    post-consent redirect into script execution. Codex and the supported hosted
    clients use HTTPS; local/native clients use an ephemeral loopback listener.
    """
    if not isinstance(uri, str) or not uri or len(uri) > 2048:
        return False
    if any(ch.isspace() for ch in uri):
        return False
    try:
        parsed = urlsplit(uri)
        # Accessing port performs its own range/format validation.
        _ = parsed.port
    except ValueError:
        return False
    if parsed.fragment or parsed.username is not None or parsed.password is not None:
        return False
    host = parsed.hostname
    if parsed.scheme == "https":
        return bool(host) and (
            not allowed_https_hosts or host.lower() in allowed_https_hosts
        )
    if parsed.scheme != "http" or not host:
        return False
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


class RegistrationGuard:
    """Reject unsafe OAuth redirect URIs before FastMCP persists a DCR client.

    The request body is replayed byte-for-byte for accepted registrations. Other
    paths and methods are untouched. Malformed registration documents are left to
    FastMCP's RFC-aware validator; this layer owns only the security boundary for
    callback URI schemes and hosts.
    """

    def __init__(self, app, allowed_https_hosts: list[str] | None = None):
        self.app = app
        self.allowed_https_hosts = {
            host.strip().lower() for host in (allowed_https_hosts or []) if host.strip()
        }

    async def __call__(self, scope, receive, send):
        if (scope.get("type") != "http" or scope.get("path") != "/register"
                or scope.get("method") != "POST"):
            return await self.app(scope, receive, send)

        upstream_receive = receive
        messages = []
        body = b""
        while True:
            message = await upstream_receive()
            messages.append(message)
            if message.get("type") != "http.request":
                break
            body += message.get("body", b"")
            if not message.get("more_body"):
                break

        try:
            document = json.loads(body or b"{}")
            redirects = document.get("redirect_uris") if isinstance(document, dict) else None
            if (redirects is not None
                    and (not isinstance(redirects, list) or not redirects
                         or not all(_safe_redirect_uri(uri, self.allowed_https_hosts)
                                    for uri in redirects))):
                return await self._reject(send)
        except (UnicodeDecodeError, json.JSONDecodeError):
            pass

        index = 0

        async def replay_receive():
            nonlocal index
            if index < len(messages):
                message = messages[index]
                index += 1
                return message
            return await upstream_receive()

        return await self.app(scope, replay_receive, send)

    @staticmethod
    async def _reject(send):
        body = json.dumps({
            "error": "invalid_client_metadata",
            "error_description": (
                "redirect_uris must use HTTPS, or HTTP on a loopback host"
            ),
        }).encode()
        await send({
            "type": "http.response.start",
            "status": 400,
            "headers": [
                (b"content-type", b"application/json"),
                (b"cache-control", b"no-store"),
                (b"content-length", str(len(body)).encode()),
            ],
        })
        await send({"type": "http.response.body", "body": body})


def _normalize_scope(scope: str) -> str:
    """Rewrite short OIDC scope names to the full Google URLs; leave everything else
    (openid, already-full URLs, unknown scopes) untouched. Order/dupes preserved."""
    if not scope or not scope.strip():
        return scope
    return " ".join(_SCOPE_ALIASES.get(tok, tok) for tok in scope.split())

# Exact-path rewrites applied before routing. Kept deliberately tiny and explicit.
PATH_ALIASES = {
    # bare-domain connector URL -> the real MCP endpoint
    "/": "/mcp",
    # RFC 9728 resource-metadata for the bare URL -> the /mcp-scoped document
    "/.well-known/oauth-protected-resource": "/.well-known/oauth-protected-resource/mcp",
    # RFC 8414 path-insertion variant some clients probe for a /mcp resource
    "/.well-known/oauth-authorization-server/mcp": "/.well-known/oauth-authorization-server",
}


class PathAliases:
    """ASGI wrapper: rewrite a handful of exact paths so both the bare server URL
    and the /mcp-suffixed URL behave identically. No other request is touched."""

    def __init__(self, app, aliases: dict | None = None):
        self.app = app
        self.aliases = dict(aliases or PATH_ALIASES)

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http":
            target = self.aliases.get(scope.get("path", ""))
            if target:
                scope = dict(scope)
                scope["path"] = target
                scope["raw_path"] = target.encode()
        return await self.app(scope, receive, send)


class ScopeNormalizer:
    """ASGI wrapper: rewrite short OIDC scope names ('email'/'profile') to the full
    Google scope URLs on the OAuth endpoints, before FastMCP validates them — so a
    client that sends the short names is accepted instead of rejected. Touches only
    /authorize (query string) and /register + /token (request body); every other
    request passes straight through. Fail-open: any parse error leaves the request
    untouched so the OAuth flow is never broken by this hook."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or scope.get("path", "") not in _SCOPE_PATHS:
            return await self.app(scope, receive, send)

        # /authorize carries scope in the query string (GET) — rewrite in place.
        if scope.get("method") == "GET":
            try:
                qs = scope.get("query_string", b"").decode()
                if "scope=" in qs:
                    pairs = parse_qsl(qs, keep_blank_values=True)
                    new = [(k, _normalize_scope(v) if k == "scope" else v) for k, v in pairs]
                    if new != pairs:
                        scope = dict(scope)
                        scope["query_string"] = urlencode(new).encode()
            except Exception:  # noqa: BLE001 — never break the flow
                log.warning("scope query normalize skipped", exc_info=True)
            return await self.app(scope, receive, send)

        # /register (JSON) and /token (form) carry scope in the body — buffer, rewrite,
        # replay with a corrected Content-Length.
        if scope.get("method") == "POST":
            upstream_receive = receive
            body = b""
            while True:
                msg = await upstream_receive()
                body += msg.get("body", b"")
                if not msg.get("more_body"):
                    break
            try:
                new_body = self._rewrite_body(scope, body)
            except Exception:  # noqa: BLE001 — never break the flow
                log.warning("scope body normalize skipped", exc_info=True)
                # The upstream receive stream has already been consumed. Replaying
                # the original bytes is the actual fail-open behavior; forwarding
                # the exhausted receive callable would turn a harmless malformed
                # body into a disconnect/hang in the OAuth endpoint.
                new_body = body
            if new_body != body:
                headers = [(k, v) for k, v in scope.get("headers", [])
                           if k.lower() != b"content-length"]
                headers.append((b"content-length", str(len(new_body)).encode()))
                scope = dict(scope)
                scope["headers"] = headers
            replayed = False

            async def _receive():
                nonlocal replayed
                if not replayed:
                    replayed = True
                    return {"type": "http.request", "body": new_body, "more_body": False}
                return await upstream_receive()

            return await self.app(scope, _receive, send)

        return await self.app(scope, receive, send)

    @staticmethod
    def _rewrite_body(scope, body: bytes) -> bytes:
        ct = b""
        for k, v in scope.get("headers", []):
            if k.lower() == b"content-type":
                ct = v.lower()
                break
        if b"json" in ct:
            try:
                data = json.loads(body or b"{}")
            except json.JSONDecodeError:
                return body
            if not isinstance(data, dict):
                return body
            if isinstance(data.get("scope"), str):
                fixed = _normalize_scope(data["scope"])
                if fixed != data["scope"]:
                    data["scope"] = fixed
                    return json.dumps(data).encode()
            return body
        # default: form-encoded (application/x-www-form-urlencoded)
        pairs = parse_qsl(body.decode(), keep_blank_values=True)
        new = [(k, _normalize_scope(v) if k == "scope" else v) for k, v in pairs]
        return urlencode(new).encode() if new != pairs else body


class TolerantGoogleProvider(GoogleProvider):
    """GoogleProvider that tolerates Claude.ai's multi-client DCR race at /token.

    The stock provider refuses to load an authorization code when the exchanging
    client_id differs from the authorizing client_id. Claude.ai's distributed
    backend routinely trips this (it registers a client per node). For PKCE-bound
    codes we return the code anyway — the framework still enforces the verifier,
    redirect_uri, expiry and single use — and just log the mismatch."""

    async def load_authorization_code(
        self,
        client: OAuthClientInformationFull,
        authorization_code: str,
    ) -> AuthorizationCode | None:
        code_model = await self._code_store.get(key=authorization_code)
        if not code_model:
            log.debug("authorization code not found")
            return None
        if time.time() > code_model.expires_at:
            log.debug("authorization code expired")
            await self._code_store.delete(key=authorization_code)
            return None
        if code_model.client_id != client.client_id:
            if not code_model.code_challenge:
                # No PKCE -> the client binding is the only protection; keep it.
                log.warning("code client mismatch without PKCE — rejecting")
                return None
            from config import settings
            if not settings.oauth_allow_cross_client_pkce:
                log.warning("code client mismatch with PKCE — compatibility exception disabled")
                return None
            redirect_host = urlsplit(str(code_model.redirect_uri)).hostname or ""
            if redirect_host.lower() not in set(settings.oauth_redirect_hosts()):
                log.warning("code client mismatch for unapproved redirect host — rejecting")
                return None
            log.info(
                "tolerating approved client mismatch at /token; PKCE still enforced"
            )
        if client.client_id is None:
            return None
        return AuthorizationCode(
            code=authorization_code,
            client_id=client.client_id,
            redirect_uri=AnyUrl(url=code_model.redirect_uri),
            redirect_uri_provided_explicitly=True,
            scopes=code_model.scopes,
            expires_at=code_model.expires_at,
            code_challenge=code_model.code_challenge or "",
        )
