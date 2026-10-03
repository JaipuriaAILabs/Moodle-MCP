"""Student-session row isolation across every direct student-bearing query.

Run: ../moodle-agent/.venv/bin/python tests/test_student_isolation.py
from the moodle-mcp directory.
"""
import os
import sys
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

    def execute(self):
        rows = [row for row in self.rows
                if all(row.get(column) == value for column, value in self.filters)]
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


if __name__ == "__main__":
    tests = [test_roster_is_self_only, test_subject_enrolment_counts_are_self_only,
             test_availability_roster_count_is_self_only,
             test_declining_name_lookup_is_self_only]
    for test in tests:
        test()
        print(f"  ✓ {test.__name__}")
    print(f"\n{len(tests)} passed, 0 failed")
