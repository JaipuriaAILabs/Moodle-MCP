"""Regression tests for the approved 2026-10-08 Moodle MCP whitelist."""

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from supabase_client import MoodleService  # noqa: E402


MIGRATION = ROOT / "sql" / "2026-10-08_mcp_access_whitelist.sql"
CONTROL_HARDENING = ROOT / "sql" / "2026-10-08_rbac_audit_control_hardening.sql"

FULL_ACCESS = {
    "shreevats@jaipuria.ac.in",
    "shiva.kakkar@jaipuria.ac.in",
    "prabhat.pankaj@jaipuria.ac.in",
    "shailaja.sharma@jaipuria.ac.in",
    "amit.attry@jaipuria.ac.in",
    "sauresh.mehrotra@jaipuria.ac.in",
    "kanchan.rana@jaipuria.ac.in",
    "sandip.das@jaipuria.ac.in",
}

CAMPUS_ACCESS = {
    "rahul.singh@jaipuria.ac.in": "noida",
    "sushma.vishnani@jaipuria.ac.in": "lucknow",
    "daneshwar.sharma@jaipuria.ac.in": "jaipur",
    "deepankar.chakrabarti@jaipuria.ac.in": "indore",
}


def _seeded_grants():
    sql = MIGRATION.read_text(encoding="utf-8")
    entries = re.findall(
        r"\('([^']+@jaipuria\.ac\.in)',\s*'[^']+',\s*"
        r"'(cross_campus|campus_admin)',\s*'([^']+)'::jsonb,\s*true,\s*true,",
        sql,
    )
    return {
        email: {"role": role, "campuses": json.loads(campuses)}
        for email, role, campuses in entries
    }


def test_whitelist_contains_exactly_the_approved_accounts():
    grants = _seeded_grants()
    assert set(grants) == FULL_ACCESS | set(CAMPUS_ACCESS)
    assert len(grants) == 12


def test_full_access_accounts_are_cross_campus_data_users():
    grants = _seeded_grants()
    for email in FULL_ACCESS:
        assert grants[email] == {"role": "cross_campus", "campuses": "all"}


def test_each_dean_is_restricted_to_exactly_their_own_campus():
    grants = _seeded_grants()
    for email, campus in CAMPUS_ACCESS.items():
        assert grants[email] == {"role": "campus_admin", "campuses": [campus]}
        svc = MoodleService(None, {
            "email": email,
            "role": "campus_admin",
            "campuses": [campus],
            "can_generate": True,
        })
        assert svc.campus_scope(campus) == [campus]
        for other in set(CAMPUS_ACCESS.values()) - {campus}:
            assert svc.campus_scope(other) == []


def test_deployment_is_enforced_and_unlisted_users_cannot_self_request():
    from config import Settings

    defaults = Settings(_env_file=None)
    assert defaults.rbac_mode() == "enforce"
    assert defaults.self_service_access is False

    render = (ROOT / "render.yaml").read_text(encoding="utf-8")
    assert re.search(r"key: MCP_RBAC_MODE\s+value: [\"']?enforce", render)
    assert re.search(r"key: OAUTH_DEFAULT_CAMPUSES\s+value: [\"']?none", render)
    assert re.search(r"key: MCP_SELF_SERVICE_ACCESS\s+value: [\"']?false", render)


def test_enforce_mode_cannot_be_bypassed_by_a_stale_env_override():
    import config
    import faculty
    from security import principal_from_claims

    old = (config.settings.rbac_mode_raw, config.settings.self_service_access,
           config.settings.mcp_faculty_raw, faculty.faculty_grant, faculty.is_student)
    try:
        config.settings.rbac_mode_raw = "enforce"
        config.settings.self_service_access = False
        config.settings.mcp_faculty_raw = '{"legacy@jaipuria.ac.in":{"campuses":null}}'
        faculty.faculty_grant = lambda _email: None
        faculty.is_student = lambda _email: False

        assert principal_from_claims({
            "email": "legacy@jaipuria.ac.in",
            "email_verified": True,
        }) is None
    finally:
        (config.settings.rbac_mode_raw, config.settings.self_service_access,
         config.settings.mcp_faculty_raw, faculty.faculty_grant, faculty.is_student) = old


def test_audit_control_sql_is_invoker_safe_and_partition_hardened():
    sql = CONTROL_HARDENING.read_text(encoding="utf-8").lower()
    assert "security_invoker = true" in sql
    assert "revoke all on mcp_audit.v_activity" in sql
    assert "alter table mcp_audit.%i enable row level security" in sql
    assert "from public, anon, authenticated, reporting_readonly" in sql


if __name__ == "__main__":
    test_whitelist_contains_exactly_the_approved_accounts()
    test_full_access_accounts_are_cross_campus_data_users()
    test_each_dean_is_restricted_to_exactly_their_own_campus()
    test_deployment_is_enforced_and_unlisted_users_cannot_self_request()
    test_enforce_mode_cannot_be_bypassed_by_a_stale_env_override()
    test_audit_control_sql_is_invoker_safe_and_partition_hardened()
    print("6 passed, 0 failed")
