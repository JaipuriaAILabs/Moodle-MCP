"""OAuth claim-shape resolution — regression guard for the FastMCP 3.x GoogleProvider.

FastMCP 3.4.5 changed the claims its GoogleProvider attaches to a verified token:
  * `email_verified` now arrives from Google's tokeninfo endpoint as the STRING "true"
    (older builds handed us a bool), and
  * the audience is the flat `aud` claim (older builds nested it under `google_token_info`).
The pre-3.x `verified is not True` check silently rejected every verified sign-in
(`PermissionError: missing or invalid access token` on every tool call). These checks pin the
tolerant behaviour so the regression cannot return.

Run:  ../moodle-agent/.venv/bin/python tests/test_oauth_claims.py   (from moodle-mcp/)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-service-key")
os.environ["OAUTH_ALLOWED_DOMAINS"] = "jaipuria.ac.in"
os.environ["OAUTH_DEFAULT_CAMPUSES"] = "all"
os.environ["GOOGLE_OAUTH_CLIENT_ID"] = "123-test.apps.googleusercontent.com"

import config  # noqa: E402
import faculty  # noqa: E402
from security import principal_from_claims  # noqa: E402

CID = "123-test.apps.googleusercontent.com"
PASS, FAIL = 0, 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}")


# OFF mode (default): a verified Jaipuria non-student gets the historical all-campus grant.
config.settings.rbac_mode_raw = "off"
# Stub the registry so the test account is neither a listed educator nor a roster student.
faculty._grants = faculty.TTLCache(maxsize=8, ttl=0.0)
faculty._students = faculty.TTLCache(maxsize=8, ttl=0.0)
faculty._fetch_faculty_row = lambda email: None
faculty._fetch_student_hit = lambda email: False

E = "prof@jaipuria.ac.in"

print("[ email_verified: FastMCP 3.4.5 string + legacy encodings ]")
check("tokeninfo string 'true' accepted (THE regression)",
      principal_from_claims({"email": E, "email_verified": "true", "aud": CID}) is not None)
check("legacy bool True still accepted",
      principal_from_claims({"email": E, "email_verified": True, "aud": CID}) is not None)
check("string 'TRUE' (case-insensitive) accepted",
      principal_from_claims({"email": E, "email_verified": "TRUE", "aud": CID}) is not None)
check("int 1 accepted",
      principal_from_claims({"email": E, "email_verified": 1, "aud": CID}) is not None)
check("userinfo v2 verified_email bool fallback accepted",
      principal_from_claims({"email": E, "aud": CID,
                             "google_user_data": {"verified_email": True}}) is not None)

print("\n[ unverified / missing still rejected ]")
check("string 'false' rejected",
      principal_from_claims({"email": E, "email_verified": "false", "aud": CID}) is None)
check("bool False rejected",
      principal_from_claims({"email": E, "email_verified": False, "aud": CID}) is None)
check("missing verification claim rejected",
      principal_from_claims({"email": E, "aud": CID}) is None)
check("userinfo verified_email False rejected",
      principal_from_claims({"email": E, "aud": CID,
                             "google_user_data": {"verified_email": False}}) is None)

print("\n[ audience guard: re-synced to the flat 3.4.5 `aud` claim ]")
check("matching flat aud accepted",
      principal_from_claims({"email": E, "email_verified": "true", "aud": CID}) is not None)
check("mismatched flat aud rejected (confused-deputy defense)",
      principal_from_claims({"email": E, "email_verified": "true",
                             "aud": "999-other.apps.googleusercontent.com"}) is None)
check("legacy nested google_token_info audience still honored",
      principal_from_claims({"email": E, "email_verified": True,
                             "google_token_info": {"audience": CID}}) is not None)
check("absent audience allowed (OAuthProxy binds it upstream)",
      principal_from_claims({"email": E, "email_verified": "true"}) is not None)

print("\n[ the end-to-end shape FastMCP 3.4.5 actually sends ]")
real = {"sub": "117xxx", "aud": CID, "email": E, "email_verified": "true",
        "name": "Prof X", "google_user_data": {"email": E, "verified_email": True, "name": "Prof X"}}
p = principal_from_claims(real)
check("full 3.4.5 token resolves to a principal", p is not None)
check("resolved principal carries the email", p and p.get("email") == E)
check("resolved principal has all-campus (OFF mode)", p and p.get("campuses") is None)

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
