#!/usr/bin/env python3
"""Read-only smoke test for a deployed Moodle MCP OAuth boundary."""

from __future__ import annotations

import json
import ssl
import sys
from urllib.error import HTTPError
from urllib.parse import urljoin, urlsplit
from urllib.request import Request, urlopen


def tls_context() -> ssl.SSLContext:
    """Use certifi when the active Python lacks a configured system CA bundle."""
    try:
        import certifi
    except ImportError:
        return ssl.create_default_context()
    return ssl.create_default_context(cafile=certifi.where())


def request(base_url: str, path: str, *, body: bytes | None = None):
    parsed = urlsplit(base_url)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username
            or parsed.password or parsed.query or parsed.fragment):
        raise ValueError("base URL must be HTTPS without credentials, query, or fragment")
    # Cloudflare rejects urllib's default Python-urllib signature (error 1010).
    # Use an explicit, identifiable release-smoke agent so this read-only check
    # exercises the same public edge that production MCP clients use.
    headers = {
        "Accept": "application/json, text/event-stream",
        "User-Agent": "Moodle-MCP-release-smoke/1.0",
    }
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = Request(urljoin(base_url.rstrip("/") + "/", path.lstrip("/")),
                  data=body, headers=headers, method="POST" if body is not None else "GET")
    try:
        # URL is derived from the HTTPS-only base validated above; path is made
        # relative with lstrip, so urljoin cannot switch to another authority.
        with urlopen(req, timeout=75, context=tls_context()) as response:  # nosec B310
            return response.status, dict(response.headers), response.read()
    except HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read()


def json_body(body: bytes, label: str) -> dict:
    try:
        value = json.loads(body)
    except json.JSONDecodeError as exc:
        raise AssertionError(f"{label} did not return JSON") from exc
    if not isinstance(value, dict):
        raise AssertionError(f"{label} did not return a JSON object")
    return value


def main() -> int:
    base_url = (sys.argv[1] if len(sys.argv) > 1
                else "https://moodle-mcp.tryrehearsal.ai")
    health_status, _, health_raw = request(base_url, "/health")
    assert health_status == 200 and json_body(health_raw, "health") == {"status": "ok"}

    resource_status, _, resource_raw = request(
        base_url, "/.well-known/oauth-protected-resource/mcp")
    resource = json_body(resource_raw, "protected-resource metadata")
    assert resource_status == 200
    assert resource.get("resource") == f"{base_url.rstrip('/')}/mcp"
    assert resource.get("authorization_servers")

    auth_status, _, auth_raw = request(base_url, "/.well-known/oauth-authorization-server")
    auth = json_body(auth_raw, "authorization-server metadata")
    assert auth_status == 200
    for field in ("authorization_endpoint", "token_endpoint", "registration_endpoint"):
        assert isinstance(auth.get(field), str) and auth[field].startswith("https://")
    assert "authorization_code" in auth.get("grant_types_supported", [])
    assert "refresh_token" in auth.get("grant_types_supported", [])
    assert "S256" in auth.get("code_challenge_methods_supported", [])

    initialize = json.dumps({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "release-smoke", "version": "1.0"},
        },
    }).encode()
    mcp_status, mcp_headers, mcp_raw = request(base_url, "/mcp", body=initialize)
    assert mcp_status == 401
    challenge = next((value for key, value in mcp_headers.items()
                      if key.lower() == "www-authenticate"), "")
    assert "Bearer" in challenge and "resource_metadata=" in challenge
    # RFC 6750 permits a 401 challenge with an empty body. Some ASGI stacks
    # return a JSON error instead, so validate it when it is present.
    if mcp_raw:
        assert json_body(mcp_raw, "MCP auth challenge").get("error") == "invalid_token"

    print(f"Live MCP smoke passed: {base_url.rstrip('/')}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AssertionError, OSError) as exc:
        print(f"Live MCP smoke failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
