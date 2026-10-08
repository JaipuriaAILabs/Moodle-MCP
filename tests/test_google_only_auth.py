"""Google-only auth-mode exclusivity and production boot-guard checks.

Run:  ../moodle-agent/.venv/bin/python tests/test_google_only_auth.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-service-key")

import config  # noqa: E402
import security  # noqa: E402

PASS, FAIL = 0, 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}")


fields = (
    "google_oauth_client_id",
    "google_oauth_client_secret",
    "require_google_oauth",
    "mcp_tokens_raw",
    "mcp_admin_token",
)
original_settings = {field: getattr(config.settings, field) for field in fields}
original_oauth = security.resolve_oauth_principal
original_static = security.resolve_principal

try:
    print("[ configured Google OAuth is exclusive ]")
    config.settings.google_oauth_client_id = "client.apps.googleusercontent.com"
    config.settings.google_oauth_client_secret = "secret"
    oauth_principal = {"email": "user@jaipuria.ac.in", "campuses": ["noida"]}
    static_principal = {"name": "legacy-admin", "campuses": None}
    static_calls = []

    security.resolve_oauth_principal = lambda: oauth_principal
    security.resolve_principal = lambda token: static_calls.append(token) or static_principal
    resolved = security.resolve_request_principal(
        {"authorization": "Bearer legacy-static-token"}
    )
    check("OAuth principal is used", resolved == oauth_principal)
    check("static resolver is never consulted in OAuth mode", static_calls == [])

    security.resolve_oauth_principal = lambda: None
    resolved_missing = security.resolve_request_principal(
        {"authorization": "Bearer legacy-static-token"}
    )
    check("missing OAuth context fails closed", resolved_missing is None)
    check("static fallback remains blocked", static_calls == [])

    print("\n[ static auth remains isolated to non-OAuth deployments ]")
    config.settings.google_oauth_client_id = ""
    config.settings.google_oauth_client_secret = ""
    resolved_static = security.resolve_request_principal(
        {"authorization": "Bearer legacy-static-token"}
    )
    check("explicit static deployment can resolve its token", resolved_static == static_principal)
    check("static resolver receives only the bearer value",
          static_calls == ["legacy-static-token"])

    print("\n[ production boot guard prevents downgrade ]")
    config.settings.require_google_oauth = True
    config.settings.mcp_tokens_raw = ""
    config.settings.mcp_admin_token = ""
    try:
        config.validate_config()
        guarded = False
    except RuntimeError as exc:
        guarded = "MCP_REQUIRE_GOOGLE_OAUTH=true" in str(exc)
    check("missing Google credentials stop startup", guarded)
finally:
    for field, value in original_settings.items():
        setattr(config.settings, field, value)
    security.resolve_oauth_principal = original_oauth
    security.resolve_principal = original_static

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
