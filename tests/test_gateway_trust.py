"""AIA-1013 #4 downstream guard: GatewayEnforce blocks un-gatewayed /mcp only when
enforced + with a valid secret; inert by default; non-/mcp paths always open."""
import asyncio
import importlib
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")

from gateway_trust import GatewayEnforce, secret_ok


ROOT = Path(__file__).resolve().parents[1]


def _scope(path="/mcp", secret=None):
    headers = []
    if secret is not None:
        headers.append((b"x-mcp-gateway-secret", secret.encode()))
    return {"type": "http", "path": path, "headers": headers}


def _drive(app_wrapper, scope):
    sent = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(msg):
        sent.append(msg)

    asyncio.new_event_loop().run_until_complete(app_wrapper(scope, receive, send))
    return sent


async def _inner_ok(scope, receive, send):
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"OK"})


def _status(sent):
    return next((m["status"] for m in sent if m["type"] == "http.response.start"), None)


def test_inert_when_not_enforced():
    gw = GatewayEnforce(_inner_ok, enforced=False, accepted_secrets=["s"])
    assert _status(_drive(gw, _scope())) == 200          # passes through, no secret


def test_enforced_blocks_missing_secret():
    gw = GatewayEnforce(_inner_ok, enforced=True, accepted_secrets=["s3cret"])
    sent = _drive(gw, _scope(secret=None))
    assert _status(sent) == 403
    body = next(m["body"] for m in sent if m["type"] == "http.response.body")
    assert json.loads(body)["error"] == "gateway_required"


def test_enforced_blocks_wrong_secret():
    gw = GatewayEnforce(_inner_ok, enforced=True, accepted_secrets=["s3cret"])
    assert _status(_drive(gw, _scope(secret="wrong"))) == 403


def test_enforced_allows_valid_secret():
    gw = GatewayEnforce(_inner_ok, enforced=True, accepted_secrets=["s3cret"])
    assert _status(_drive(gw, _scope(secret="s3cret"))) == 200


def test_enforced_leaves_non_mcp_open():
    gw = GatewayEnforce(_inner_ok, enforced=True, accepted_secrets=["s3cret"])
    for path in ("/health", "/.well-known/oauth-protected-resource", "/token"):
        assert _status(_drive(gw, _scope(path=path))) == 200


def test_enforced_with_no_configured_secret_fails_closed():
    gw = GatewayEnforce(_inner_ok, enforced=True, accepted_secrets=[])
    assert _status(_drive(gw, _scope(secret="anything"))) == 403


def test_secret_ok_rotation():
    assert secret_ok(_scope(secret="new"), ["new", "old"])
    assert secret_ok(_scope(secret="old"), ["new", "old"])
    assert not secret_ok(_scope(secret="x"), ["new", "old"])


def test_gateway_enforcement_boot_validation(monkeypatch):
    import config as cfgmod

    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "test-service-key")
    monkeypatch.setenv("GATEWAY_ENFORCED", "true")
    monkeypatch.setenv("GATEWAY_SHARED_SECRET", "")
    importlib.reload(cfgmod)
    try:
        cfgmod.validate_config()
    except RuntimeError as exc:
        assert "requires GATEWAY_SHARED_SECRET" in str(exc)
    else:
        raise AssertionError("gateway enforcement started without a shared secret")

    monkeypatch.setenv("GATEWAY_SHARED_SECRET", "short")
    importlib.reload(cfgmod)
    try:
        cfgmod.validate_config()
    except RuntimeError as exc:
        assert "at least 32 characters" in str(exc)
    else:
        raise AssertionError("gateway enforcement accepted a weak shared secret")

    monkeypatch.setenv("GATEWAY_SHARED_SECRET", "g" * 32)
    importlib.reload(cfgmod)
    cfgmod.validate_config()
    # Restore both the process environment and the module-level Settings object so
    # this test cannot leak enforced-gateway state into later tests in the same run.
    monkeypatch.undo()
    importlib.reload(cfgmod)


def test_cloudflare_forwards_gateway_origin_settings():
    worker = (ROOT / "cloudflare" / "src" / "index.ts").read_text(encoding="utf-8")
    assert '"GATEWAY_ENFORCED", "GATEWAY_SHARED_SECRET"' in worker
    for config_name in ("wrangler.jsonc", "wrangler.staging.jsonc"):
        config = (ROOT / "cloudflare" / config_name).read_text(encoding="utf-8")
        assert '"GATEWAY_ENFORCED": "false"' in config
