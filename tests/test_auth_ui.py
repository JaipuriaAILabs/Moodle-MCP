"""Google-only consent presentation and ASGI pass-through regression checks.

Run:  ../moodle-agent/.venv/bin/python tests/test_auth_ui.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from auth_ui import GoogleConsentBranding, brand_consent_html  # noqa: E402
from fastmcp.server.auth.oauth_proxy.ui import create_consent_html  # noqa: E402

PASS, FAIL = 0, 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}")


SOURCE = create_consent_html(
    client_id="client-123",
    redirect_uri="https://rehearsal-os.app/oauth/callback",
    scopes=["openid", "email", "profile"],
    txn_id="txn-keep-me",
    csrf_token="csrf-keep-me",
    client_name="Jaipuria OS",
    server_name="jaipuria-moodle-mcp",
    server_icon_url="data:image/png;base64,OLD",
    server_website_url="https://www.jaipuria.ac.in",
)

print("[ consent page is branded without changing security fields ]")
BRANDED = brand_consent_html(SOURCE, logo_data_uri="data:image/png;base64,JAIPURIA")
check("Jaipuria identity is visible", "Jaipuria Institute of Management" in BRANDED)
check("primary action is Continue with Google", "Continue with Google" in BRANDED)
check("Google mark is present", 'class="google-mark"' in BRANDED)
check("generic FastMCP title is removed", "Application Access Request" not in BRANDED)
check("old Allow Access label is removed", ">Allow Access</button>" not in BRANDED)
check("password input is absent", 'type="password"' not in BRANDED.lower())
check("transaction id is unchanged", 'name="txn_id" value="txn-keep-me"' in BRANDED)
check("CSRF token is unchanged", 'name="csrf_token" value="csrf-keep-me"' in BRANDED)
check("approve semantic is unchanged", 'name="action" value="approve"' in BRANDED)
check("deny semantic is unchanged", 'name="action" value="deny"' in BRANDED)
check("form still POSTs to the protected handler",
      '<form id="consentForm" method="POST" action="">' in BRANDED)
check("advanced redirect details remain available",
      "https://rehearsal-os.app/oauth/callback" in BRANDED)
check("CSP remains present", "Content-Security-Policy" in BRANDED)


async def response_app(scope, receive, send):
    body = SOURCE.encode()
    await send({
        "type": "http.response.start",
        "status": 200,
        "headers": [
            (b"content-type", b"text/html; charset=utf-8"),
            (b"content-length", str(len(body)).encode()),
            (b"set-cookie", b"MCP_CONSENT_STATE=signed; HttpOnly; Secure"),
            (b"x-frame-options", b"DENY"),
        ],
    })
    midpoint = len(body) // 2
    await send({"type": "http.response.body", "body": body[:midpoint], "more_body": True})
    await send({"type": "http.response.body", "body": body[midpoint:], "more_body": False})


async def drive(scope, *, enabled=True):
    sent = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    wrapped = GoogleConsentBranding(
        response_app, enabled=enabled, logo_data_uri="data:image/png;base64,JAIPURIA"
    )
    await wrapped(scope, receive, send)
    return sent


print("\n[ middleware changes only GET consent presentation ]")
GET_CONSENT = {"type": "http", "method": "GET", "path": "/consent"}
messages = asyncio.run(drive(GET_CONSENT))
start, body_message = messages
headers = start["headers"]
body = body_message["body"]
check("GET consent response is branded", b"Continue with Google" in body)
check("security cookie is preserved",
      (b"set-cookie", b"MCP_CONSENT_STATE=signed; HttpOnly; Secure") in headers)
check("clickjacking header is preserved", (b"x-frame-options", b"DENY") in headers)
check("consent response is no-store", (b"cache-control", b"no-store") in headers)
length = next(value for key, value in headers if key == b"content-length")
check("content length is recalculated", int(length) == len(body))

post_messages = asyncio.run(drive({"type": "http", "method": "POST", "path": "/consent"}))
post_body = b"".join(m.get("body", b"") for m in post_messages
                     if m.get("type") == "http.response.body")
check("POST consent response is untouched", post_body == SOURCE.encode())

other_messages = asyncio.run(drive({"type": "http", "method": "GET", "path": "/health"}))
other_body = b"".join(m.get("body", b"") for m in other_messages
                      if m.get("type") == "http.response.body")
check("non-consent response is untouched", other_body == SOURCE.encode())

disabled_messages = asyncio.run(drive(GET_CONSENT, enabled=False))
disabled_body = b"".join(m.get("body", b"") for m in disabled_messages
                         if m.get("type") == "http.response.body")
check("static-auth deployment does not rewrite pages", disabled_body == SOURCE.encode())

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
