"""Security invariants for the production RBAC enforcement layers.

Run: ../moodle-agent/.venv/bin/python tests/test_rbac_guardrails.py
"""

import ast
import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-key")

import config  # noqa: E402
import faculty  # noqa: E402
import security  # noqa: E402
from supabase import create_client  # noqa: E402
from supabase_client import MoodleService, db_scope_for_principal  # noqa: E402
from tools import actions  # noqa: E402


def _registered_tool_names():
    names = set()
    for path in [ROOT / "server.py", *(ROOT / "tools").glob("*.py")]:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                if (isinstance(decorator, ast.Call)
                        and isinstance(decorator.func, ast.Attribute)
                        and decorator.func.attr == "tool"):
                    names.add(node.name)
    return names


def test_student_tool_surface_is_explicit_and_fail_closed():
    registered = _registered_tool_names()
    assert security.STUDENT_SELF_TOOLS <= registered
    student = {"role": "student"}
    for name in registered:
        assert security.tool_allowed_for_principal(name, student) == (
            name in security.STUDENT_SELF_TOOLS), name

    cohort_or_admin = {
        "list_students", "list_subjects", "subject_performance", "section_compare",
        "assessment_breakdown", "subject_difficulty", "marks_overview",
        "attendance_overview", "top_performers", "cohort_compare", "cohort_pulse",
        "watchlist", "declining_students", "campus_performance_report",
        "at_risk_students", "attendance_watch", "zero_alerts",
        "report_data_availability", "request_access", "list_access_requests",
    }
    assert cohort_or_admin <= registered
    assert cohort_or_admin.isdisjoint(security.STUDENT_SELF_TOOLS)
    assert not security.tool_allowed_for_principal("future_unreviewed_tool", student)


def test_pending_and_staff_tool_entitlements():
    assert security.tool_allowed_for_principal("whoami", {"role": "pending"})
    assert security.tool_allowed_for_principal("request_access", {"role": "pending"})
    assert not security.tool_allowed_for_principal("get_student", {"role": "pending"})
    assert not security.tool_allowed_for_principal(
        "future_unreviewed_tool", {"role": "pending"})
    for role in ("faculty", "campus_admin", "cross_campus", "admin", "viewer"):
        assert security.tool_allowed_for_principal("get_student", {"role": role})
    assert not security.tool_allowed_for_principal("get_student", None)


def test_database_scope_contract_never_mixes_student_and_staff_rules():
    student = db_scope_for_principal({
        "role": "student", "student_ids": ["SELF-B", "SELF-A"],
        "campuses": ["Noida"], "batches": ["2024-26"],
        "email": "student@jaipuria.ac.in", "name": "Student Name",
    })
    assert student == {
        "v": 1, "kind": "student", "role": "student",
        "student_ids": ["SELF-A", "SELF-B"],
        "campuses": ["noida"], "batches": ["2024-26"],
    }
    assert "all" not in student

    dean = db_scope_for_principal({"role": "campus_admin", "campuses": ["Jaipur"]})
    assert dean == {"v": 1, "kind": "staff", "role": "campus_admin",
                    "campuses": ["jaipur"]}
    assert "student_ids" not in dean

    director = db_scope_for_principal({"role": "cross_campus", "campuses": None})
    assert director == {"v": 1, "kind": "staff", "role": "cross_campus", "all": True}
    assert db_scope_for_principal({"role": "faculty", "campuses": None})["kind"] == "deny"
    assert db_scope_for_principal({"role": "cross_campus", "campuses": ["noida"]})[
        "kind"] == "deny"
    assert db_scope_for_principal({"role": "made_up", "campuses": None})["kind"] == "deny"
    assert db_scope_for_principal({"role": "student", "student_ids": [],
                                   "campuses": ["noida"], "batches": ["2024-26"]})[
                                       "kind"] == "deny"
    assert db_scope_for_principal({"role": "pending", "campuses": []})["kind"] == "deny"


class _Builder:
    def __init__(self):
        self.headers = {"Authorization": "Bearer test"}


class _RawClient:
    def __init__(self):
        self.builders = []

    def table(self, _name):
        builder = _Builder()
        self.builders.append(builder)
        return builder


def test_every_service_query_builder_carries_an_isolated_scope_header():
    raw = _RawClient()
    student_svc = MoodleService(raw, {
        "role": "student", "student_ids": ["SELF"], "student_id": "SELF",
        "campuses": ["noida"], "batches": ["2024-26"], "batch": "2024-26",
        "email": "student@jaipuria.ac.in", "name": "Own Student",
    })
    dean_svc = MoodleService(raw, {
        "role": "campus_admin", "campuses": ["jaipur"],
        "email": "dean@jaipuria.ac.in",
    })
    sb = student_svc.client.table("students")
    db = dean_svc.client.table("students")
    student_header = json.loads(sb.headers["X-MCP-Scope"])
    dean_header = json.loads(db.headers["X-MCP-Scope"])
    assert student_header["student_ids"] == ["SELF"]
    assert student_header["kind"] == "student"
    assert dean_header["campuses"] == ["jaipur"]
    assert dean_header["kind"] == "staff"
    assert "student@jaipuria.ac.in" not in sb.headers["X-MCP-Scope"]
    assert "Own Student" not in sb.headers["X-MCP-Scope"]
    assert sb.headers["X-MCP-Scope"] != db.headers["X-MCP-Scope"]


def test_scope_header_survives_real_postgrest_table_and_rpc_builders():
    raw = create_client("https://example.supabase.co", "test-key")
    svc = MoodleService(raw, {
        "role": "student", "student_ids": ["SELF"], "student_id": "SELF",
        "campuses": ["noida"], "batches": ["2024-26"],
    })
    table_request = svc.client.table("students").select("student_id").request
    rpc_request = svc.client.rpc("mcp_scope_probe").request
    for request in (table_request, rpc_request):
        scope = json.loads(request.headers["X-MCP-Scope"])
        assert scope["kind"] == "student"
        assert scope["student_ids"] == ["SELF"]


def test_shared_email_with_different_names_fails_closed():
    original = faculty._fetch_student_identity
    faculty._student_ids = faculty.TTLCache(maxsize=8, ttl=60)
    faculty._stale_student_ids = faculty.TTLCache(maxsize=8, ttl=60)
    faculty._fetch_student_identity = lambda _email: [
        {"student_id": "SELF", "student_name": "Person One",
         "campus": "noida", "batch": "2024-26"},
        {"student_id": "OTHER", "student_name": "Different Person",
         "campus": "noida", "batch": "2024-26"},
    ]
    try:
        assert faculty.student_identity("shared@jaipuria.ac.in") is None
    finally:
        faculty._fetch_student_identity = original


def test_student_report_generation_forces_the_authenticated_student():
    class _Svc:
        can_generate = True
        self_student_id = "SELF"
        self_student_ids = ["SELF"]
        principal = {"role": "student", "student_id": "SELF"}

        def campus_scope(self, campus):
            return [campus] if campus == "noida" else []

        def latest_run(self, campus, batch):
            return "run-1" if (campus, batch) == ("noida", "2024-26") else None

    import tools.common as common
    originals = (common.find_student, actions._agent_generate,
                 actions._enforce_report_budget,
                 config.settings.agent_api_base, config.settings.agent_report_queue,
                 config.settings.agent_admin_user, config.settings.agent_admin_pass)
    called = {}

    def find_student(_svc, student_id):
        if student_id != "SELF":
            raise AssertionError(f"peer target reached resolver: {student_id}")
        return {"student_id": "SELF", "student_name": "Own Student",
                "campus": "noida", "batch": "2024-26"}

    async def generate(campus, batch, student_id, refresh, trimester=None, principal=None):
        called["target"] = (campus, batch, student_id)
        return 200, {"student_id": student_id, "name": "Own Student"}

    async def no_budget(_svc):
        return None

    try:
        common.find_student = find_student
        actions._agent_generate = generate
        actions._enforce_report_budget = no_budget
        config.settings.agent_api_base = "https://agent.example.com"
        config.settings.agent_report_queue = False
        config.settings.agent_admin_user = "test"
        config.settings.agent_admin_pass = "test"
        params = actions.CreateReportParams(
            student_id="OTHER", campus="noida", batch="2024-26")
        out = asyncio.run(actions._create_impl(_Svc(), params))
        assert out.get("found") is True
        assert called["target"] == ("noida", "2024-26", "SELF")
        assert out.get("student_id") == "SELF"
    finally:
        (common.find_student, actions._agent_generate,
         actions._enforce_report_budget,
         config.settings.agent_api_base, config.settings.agent_report_queue,
         config.settings.agent_admin_user, config.settings.agent_admin_pass) = originals


def test_fail_closed_rls_migrations_have_no_read_all_escape_hatch():
    contract = (ROOT / "sql" / "2026-10-08_mcp_scope_contract.sql").read_text().lower()
    enforce = (ROOT / "sql" / "2026-10-08_mcp_scope_rls_enforce.sql").read_text().lower()
    assert "return false" in contract
    assert "kind = 'student'" in contract and "kind = 'staff'" in contract
    assert "campus can never widen" in contract
    assert "only an explicit admin/cross-campus staff role may assert all=true" in contract
    assert "drop policy if exists reporting_readonly_select" in enforce
    assert "using (true)" not in enforce
    for table in ("students", "courses", "enrolments", "marks",
                  "attendance_sessions", "student_reports", "extraction_runs"):
        assert f"alter table public.{table} enable row level security" in enforce


def test_production_manifest_enables_strict_student_self_access():
    render = (ROOT / "render.yaml").read_text(encoding="utf-8")
    assert "key: MCP_RBAC_MODE\n        value: \"enforce\"" in render
    assert "key: MCP_STUDENT_SELF_ACCESS" in render
    student_block = render.split("key: MCP_STUDENT_SELF_ACCESS", 1)[1].split("- key:", 1)[0]
    assert 'value: "true"' in student_block
    assert "key: MCP_SELF_SERVICE_ACCESS\n        value: \"false\"" in render


if __name__ == "__main__":
    tests = [
        value for name, value in sorted(globals().items())
        if name.startswith("test_") and callable(value)
    ]
    for test in tests:
        test()
        print(f"  ✓ {test.__name__}")
    print(f"\n{len(tests)} passed, 0 failed")
