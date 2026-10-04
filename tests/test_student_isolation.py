"""Student-session row isolation across every direct student-bearing query.

Run: ../moodle-agent/.venv/bin/python tests/test_student_isolation.py
from the moodle-mcp directory.
"""
import asyncio
import json
import os
import sys
import uuid
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-key")

from tools import insights, reports, students, subjects  # noqa: E402
from tools.reports import AvailabilityParams  # noqa: E402
from tools.students import RosterParams  # noqa: E402
from tools.subjects import ScopeParams  # noqa: E402


class Query:
    def __init__(self, table, rows, calls):
        self.table = table
        self.rows = list(rows)
        self.calls = calls
        self.filters = []
        calls.append(self)

    def select(self, *_args, **_kwargs):
        return self

    def eq(self, column, value):
        self.filters.append((column, value))
        return self

    def limit(self, _limit):
        return self

    def order(self, *_args, **_kwargs):
        return self

    @property
    def not_(self):
        return self

    def is_(self, *_args, **_kwargs):
        return self

    def or_(self, expr, *_args, **_kwargs):
        # Model PostgREST or=...ilike.*frag*: keep only rows whose name/id contains a fragment,
        # applied ON TOP of the eq filters — so apply_student's self bound still holds first and a
        # classmate name/id can never match the student's own (self-scoped) row set.
        import re
        self._or_terms = [f.lower() for f in re.findall(r"ilike\.\*?([^*,]+)\*?", expr) if f]
        return self

    def execute(self):
        rows = [row for row in self.rows
                if all(row.get(column) == value for column, value in self.filters)]
        terms = getattr(self, "_or_terms", None)
        if terms:
            rows = [r for r in rows if any(
                t in str(r.get("student_name", "")).lower()
                or t in str(r.get("student_id", "")).lower() for t in terms)]
        return SimpleNamespace(data=rows, count=len(rows))


class Client:
    def __init__(self, data):
        self.data = data
        self.calls = []

    def table(self, name):
        return Query(name, self.data.get(name, []), self.calls)


class StudentService:
    self_student_id = "SELF"
    self_student_ids = ["SELF"]
    self_batch = "2024-26"
    self_batches = {"2024-26"}
    role = "student"

    def __init__(self, data):
        self.client = Client(data)

    def apply_student(self, query, col="student_id"):
        return query.eq(col, self.self_student_id)

    def apply_campus(self, query, col="campus", requested=None):
        return query.eq(col, requested) if requested else query

    def campus_scope(self, requested):
        return [requested or "noida"]

    def latest_run(self, _campus, _batch, purpose=None):
        return "run-1"


def _assert_self_filter(svc, table):
    matching = [q for q in svc.client.calls if q.table == table]
    assert matching, f"expected a {table} query"
    assert all(("student_id", "SELF") in q.filters for q in matching), matching


def test_roster_is_self_only():
    svc = StudentService({"students": [
        {"student_id": "SELF", "student_name": "Own Student", "campus": "noida",
         "batch": "2024-26", "section_group": "A"},
        {"student_id": "OTHER", "student_name": "Other Student", "campus": "noida",
         "batch": "2024-26", "section_group": "A"},
    ]})
    out = students._roster_impl(
        svc, RosterParams(campus="noida", batch="2024-26", limit=50))
    assert out["count"] == 1
    assert [row["student_id"] for row in out["students"]] == ["SELF"]
    _assert_self_filter(svc, "students")


def test_subject_enrolment_counts_are_self_only():
    svc = StudentService({"enrolments": [
        {"run_id": "run-1", "course_id": "own-course", "student_id": "SELF"},
        {"run_id": "run-1", "course_id": "other-course", "student_id": "OTHER"},
    ]})
    original = subjects.courses_for
    subjects.courses_for = lambda *_args, **_kwargs: {
        "own-course": {"subject": "Own Subject", "trimester": "1"},
        "other-course": {"subject": "Other Subject", "trimester": "1"},
    }
    try:
        out = subjects._list_impl(svc, ScopeParams(campus="noida", batch="2024-26"))
    finally:
        subjects.courses_for = original
    counts = {row["subject"]: row["students"] for row in out["subjects"]}
    assert counts == {"Own Subject": 1}
    _assert_self_filter(svc, "enrolments")


def test_availability_roster_count_is_self_only():
    svc = StudentService({
        "extraction_runs": [{"campus": "noida", "batch": "2024-26",
                             "run_id": "run-1", "finished_at": "2026-10-01",
                             "status": "completed", "purpose": "final"},
                            {"campus": "noida", "batch": "2025-27",
                             "run_id": "run-2", "finished_at": "2026-10-01",
                             "status": "completed", "purpose": "final"}],
        "students": [
            {"student_id": "SELF", "campus": "noida", "batch": "2024-26"},
            {"student_id": "OTHER", "campus": "noida", "batch": "2024-26"},
        ],
    })
    import tools.common as common
    original = common.courses_for
    common.courses_for = lambda *_args, **_kwargs: {
        "c1": {"trimester": "1"},
    }
    try:
        out = reports._availability_impl(svc, AvailabilityParams())
    finally:
        common.courses_for = original
    assert len(out["scopes"]) == 1
    assert out["scopes"][0]["batch"] == "2024-26"
    assert out["scopes"][0]["students"] == 1
    _assert_self_filter(svc, "students")


def test_declining_name_lookup_is_self_only():
    svc = StudentService({"students": [
        {"run_id": "run-1", "student_id": "SELF", "student_name": "Own Student"},
        {"run_id": "run-1", "student_id": "OTHER", "student_name": "Other Student"},
    ]})
    original_courses, original_marks = insights.courses_for, insights.scope_marks
    insights.courses_for = lambda *_args, **_kwargs: {
        "c1": {"trimester": "1"}, "c2": {"trimester": "2"},
    }
    insights.scope_marks = lambda *_args, **_kwargs: [
        {"student_id": "SELF", "course_id": "c1", "graded": True,
         "obtained_score": 80, "max_score": 100},
        {"student_id": "SELF", "course_id": "c2", "graded": True,
         "obtained_score": 60, "max_score": 100},
    ]
    try:
        params = insights.ScopeParams(campus="noida", batch="2024-26")
        out = insights._declining_impl(svc, params)
    finally:
        insights.courses_for, insights.scope_marks = original_courses, original_marks
    assert [row["name"] for row in out["students"]] == ["Own Student"]
    _assert_self_filter(svc, "students")


def _two_student_roster():
    return StudentService({"students": [
        {"student_id": "SELF", "student_name": "Own Student", "campus": "noida",
         "batch": "2024-26", "section_group": "A"},
        {"student_id": "OTHER", "student_name": "Peer Student", "campus": "noida",
         "batch": "2024-26", "section_group": "A"},
    ]})


def test_get_student_rejects_classmate_lookup():
    # The headline adversarial case: a student explicitly asks for a classmate — by enrolment id
    # AND by name — and must get a uniform not-found, never the peer's record.
    from tools.students import StudentParams
    for query in ("OTHER", "Peer Student"):
        svc = _two_student_roster()
        out = students._student_impl(svc, StudentParams(student_id=query))
        assert out.get("found") is False, (query, out)
        blob = json.dumps(out)
        assert "Peer" not in blob and "OTHER" not in blob, (query, out)


def test_student_marks_rejects_classmate_id():
    from tools.students import StudentParams
    svc = _two_student_roster()
    out = students._marks_impl(svc, StudentParams(student_id="OTHER"))
    assert out.get("found") is False, out
    assert "OTHER" not in json.dumps(out), out


def test_report_job_is_self_only():
    # get_report_job fetches a queued job by request_id; the MCP layer must additionally confirm the
    # job's target is the caller's OWN id, so a student cannot poll a same-campus peer's request_id.
    from tools import actions

    captured = {}

    class _MCP:
        def tool(self, **_kw):
            def deco(fn):
                captured[fn.__name__] = fn
                return fn
            return deco

    class _Svc:
        self_student_id = "SELF"
        self_student_ids = ["SELF"]
        principal = {"email": "student@jaipuria.ac.in"}

        def campus_scope(self, campus):
            return [campus] if campus else []

    svc = _Svc()

    async def _get_service():
        return svc

    actions.register(_MCP(), _get_service)
    get_report_job = captured["get_report_job"]
    original_agent_job = actions._agent_job

    def _stub_job(target_id):
        async def _aj(_request_id, _principal):
            return {"status": "completed", "campus": "noida", "batch": "2024-26",
                    "result": {"student_id": target_id, "name": f"Report {target_id}"}}
        return _aj

    try:
        # The student's OWN completed job -> returned.
        actions._agent_job = _stub_job("SELF")
        out = asyncio.run(get_report_job(actions.ReportJobParams(request_id=str(uuid.uuid4()))))
        assert out.get("found") and out.get("student_id") == "SELF", out

        # A same-campus PEER's job, polled by its request_id -> denied at the MCP boundary.
        actions._agent_job = _stub_job("OTHER")
        denied = False
        try:
            asyncio.run(get_report_job(actions.ReportJobParams(request_id=str(uuid.uuid4()))))
        except PermissionError:
            denied = True
        assert denied, "a student must not read a peer's report job"
    finally:
        actions._agent_job = original_agent_job


if __name__ == "__main__":
    tests = [test_roster_is_self_only, test_subject_enrolment_counts_are_self_only,
             test_availability_roster_count_is_self_only,
             test_declining_name_lookup_is_self_only,
             test_get_student_rejects_classmate_lookup,
             test_student_marks_rejects_classmate_id,
             test_report_job_is_self_only]
    for test in tests:
        test()
        print(f"  ✓ {test.__name__}")
    print(f"\n{len(tests)} passed, 0 failed")
