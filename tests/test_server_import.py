"""Import smoke test for server.py (plain asserts).

The rest of the suite imports security/telemetry/config directly and mocks around the
server, so an import-time crash in server.py (e.g. calling a Starlette API that a new
Starlette major removed) slips past unit tests AND byte-compile. This test actually
imports the module, builds the FastMCP app + full wrapper chain, and asserts the ASGI
app is constructed — reproducing exactly what boot does. It would have caught the
Starlette 1.x `add_event_handler` removal (deploy crash, commit 91ddf68).

Run:  ../moodle-agent/.venv/bin/python tests/test_server_import.py
"""
import os
import sys
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

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
