"""Signed access-request approver notifications (offline, httpx mocked)."""
import asyncio
import hashlib
import hmac
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-service-key")

import access_notifications as notices  # noqa: E402
import config  # noqa: E402
from config import settings  # noqa: E402

PASS, FAIL = 0, 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}")


def raises(fn):
    try:
        fn()
        return False
    except RuntimeError:
        return True


orig = (settings.access_request_webhook_url, settings.access_request_webhook_secret,
        notices._http)

print("\n[ disabled is a no-op ]")
settings.access_request_webhook_url = ""
settings.access_request_webhook_secret = ""
check("disabled notification returns false without network",
      asyncio.run(notices.notify_access_request(role="faculty", campuses=["noida"])) is False)

print("\n[ boot validation fails closed ]")
settings.access_request_webhook_url = "https://hooks.example.test/access"
settings.access_request_webhook_secret = ""
check("half-configured webhook rejected", raises(config.validate_config))
settings.access_request_webhook_secret = "short"
check("short signing secret rejected", raises(config.validate_config))
settings.access_request_webhook_secret = "s" * 32
settings.access_request_webhook_url = "http://hooks.example.test/access"
check("plaintext webhook rejected", raises(config.validate_config))
settings.access_request_webhook_url = "https://hooks.example.test/access"
check("complete https webhook accepted", not raises(config.validate_config))

print("\n[ signed identity-free event ]")
captured = {}


class _Response:
    def raise_for_status(self):
        return None


class _Client:
    async def post(self, url, *, content, headers):
        captured.update(url=url, content=content, headers=headers)
        return _Response()


notices._http = _Client()
sent = asyncio.run(notices.notify_access_request(role="faculty", campuses=["noida"]))
check("notification delivered", sent is True)
payload = json.loads(captured["content"])
check("event contains role + campus", payload.get("requested_role") == "faculty"
      and payload.get("requested_campuses") == ["noida"])
check("event has no requester identity or derived subject",
      not ({"email", "name", "requester", "requester_subject"} & set(payload)))
headers = captured["headers"]
message = (headers["X-MCP-Timestamp"].encode() + b"."
           + headers["X-MCP-Nonce"].encode() + b"." + captured["content"])
expected = "sha256=" + hmac.new(("s" * 32).encode(), message, hashlib.sha256).hexdigest()
check("HMAC covers timestamp + nonce + exact body",
      hmac.compare_digest(headers["X-MCP-Signature"], expected))

print("\n[ delivery failure does not raise ]")


class _FailClient:
    async def post(self, *_args, **_kwargs):
        raise RuntimeError("contains details that must not be logged")


notices._http = _FailClient()
check("notification outage returns false", asyncio.run(
    notices.notify_access_request(role="viewer", campuses=["jaipur"])) is False)

settings.access_request_webhook_url, settings.access_request_webhook_secret, notices._http = orig

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
