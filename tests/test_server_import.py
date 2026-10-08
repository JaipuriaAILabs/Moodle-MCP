"""Import smoke test for server.py (plain asserts).

The rest of the suite imports security/telemetry/config directly and mocks around the
server, so an import-time crash in server.py (e.g. calling a Starlette API that a new
Starlette major removed) slips past unit tests AND byte-compile. This test actually
imports the module, builds the FastMCP app + full wrapper chain, and asserts the ASGI
app is constructed — reproducing exactly what boot does. It would have caught the
Starlette 1.x `add_event_handler` removal (deploy crash, commit 91ddf68).

Run:  ../moodle-agent/.venv/bin/python tests/test_server_import.py
"""
import asyncio
import os
import sys
from types import SimpleNamespace
import warnings

warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# Static-token mode (no Google OAuth creds) — the default boot path on a bare env.
os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-service-key")
for k in ("GOOGLE_OAUTH_CLIENT_ID", "GOOGLE_OAUTH_CLIENT_SECRET",
          "MCP_REQUIRE_GOOGLE_OAUTH", "NEW_RELIC_LICENSE_KEY"):
    os.environ.pop(k, None)

PASS, FAIL = 0, 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}")


print("\n[ server.py imports and builds the app ]")
try:
    import server  # noqa: E402 — importing IS the test
    imported = True
    err = None
except Exception as e:  # noqa: BLE001
    imported = False
    err = e
    print(f"  ✗ import raised: {type(e).__name__}: {e}")

check("server module imports without raising at boot", imported)
if imported:
    check("mcp server built with the configured name", server.mcp.name == "jaipuria-moodle-mcp")
    # The exported ASGI app must be callable (the outermost wrapper in the chain).
    check("ASGI app is constructed and callable", callable(server.app))
    # whoami is always registered (@mcp.tool turns it into a Tool object, not a plain
    # function); its mere presence means the decorator + all tool registration ran.
    check("whoami tool is registered", getattr(server, "whoami", None) is not None)

    print("\n[ static-token dependency keeps Authorization under FastMCP 3.4.5 ]")
    import fastmcp.server.dependencies as dependencies

    original_get_headers = dependencies.get_http_headers
    original_resolve_request_principal = server.resolve_request_principal
    original_create_service = server.create_service
    header_calls = []
    auth_inputs = []

    def fake_get_headers(include_all=False, include=None):
        header_calls.append({"include_all": include_all, "include": include})
        if include and "authorization" in include:
            return {"authorization": "Bearer regression-static-token"}
        return {"traceparent": "00-00000000000000000000000000000001-0000000000000001-01"}

    dependencies.get_http_headers = fake_get_headers
    def fake_resolve_request_principal(headers):
        auth_inputs.append(headers)
        return (
            {"name": "Regression Admin", "campuses": None, "role": "admin"}
            if headers == {"authorization": "Bearer regression-static-token"} else None
        )

    server.resolve_request_principal = fake_resolve_request_principal
    server.create_service = lambda principal: SimpleNamespace(principal=principal)
    try:
        service = asyncio.run(server.get_authenticated_service())
        check("Authorization is requested explicitly", any(
            call["include"] and "authorization" in call["include"]
            for call in header_calls
        ))
        check("only filtered auth headers reach the principal resolver", auth_inputs == [
            {"authorization": "Bearer regression-static-token"}
        ])
        check("static bearer resolves inside the tool dependency",
              service.principal["role"] == "admin")
    except Exception as e:  # noqa: BLE001
        check(f"static bearer resolves without raising ({type(e).__name__}: {e})", False)
    finally:
        dependencies.get_http_headers = original_get_headers
        server.resolve_request_principal = original_resolve_request_principal
        server.create_service = original_create_service

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
