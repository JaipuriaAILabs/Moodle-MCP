"""Name-resolution propagation (plain asserts; no pytest infra).

Regression for the bug where per-student tools resolved a NAME to the right roster
row for the header but then queried marks/attendance/narratives with the raw name
string — returning the right identity but ZERO data. Every per-student impl must use
the RESOLVED enrolment id for its data lookups and echo it back.

Run:  ../moodle-agent/.venv/bin/python tests/test_name_resolution.py
from the moodle-mcp directory.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-service-key")

import tools.common as common  # noqa: E402
import tools.insights as insights  # noqa: E402
import tools.reports as reports  # noqa: E402
import tools.students as students  # noqa: E402

PASS, FAIL = 0, 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}")


RAW = "shubham rawat"                 # what the user typed (a name, lower/single-spaced)
RESOLVED_ID = "JN24PG223"             # what find_student resolves it to
ROW = {"student_id": RESOLVED_ID, "student_name": "SHUBHAM  RAWAT",
       "campus": "noida", "batch": "2024-26", "section_group": None}


class P:
    def __init__(self, sid, trimester=None, campus=None, batch=None):
        self.student_id, self.trimester, self.campus, self.batch = sid, trimester, campus, batch


class Svc:
    def latest_run(self, campus, batch):
        return "run1"


# ==========================================================================
print("\n[ students.py — get_student / student_marks / student_attendance ]")
students.find_student = lambda svc, q: ROW
students.courses_for = lambda svc, run_id, tri=None: {}
_seen = []
students.marks_for = lambda svc, run_id, student_id=None, course_ids=None: (
    _seen.append(("marks", student_id)) or [])
students.attendance_for = lambda svc, run_id, student_id=None, course_ids=None: (
    _seen.append(("att", student_id)) or [])

_seen.clear()
r = students._student_impl(Svc(), P(RAW))
check("get_student queries marks with RESOLVED id", ("marks", RESOLVED_ID) in _seen)
check("get_student queries attendance with RESOLVED id", ("att", RESOLVED_ID) in _seen)
check("get_student echoes resolved id", r["student"]["student_id"] == RESOLVED_ID)
check("get_student never used the raw name", all(s[1] == RESOLVED_ID for s in _seen))

_seen.clear()
r = students._marks_impl(Svc(), P(RAW))
check("student_marks queries with RESOLVED id", _seen == [("marks", RESOLVED_ID)])
check("student_marks echoes resolved id", r["student_id"] == RESOLVED_ID)

_seen.clear()
r = students._attendance_impl(Svc(), P(RAW))
check("student_attendance queries with RESOLVED id", _seen == [("att", RESOLVED_ID)])
check("student_attendance echoes resolved id", r["student_id"] == RESOLVED_ID)


# ==========================================================================
print("\n[ insights.py — student_trajectory / student_360 ]")
insights.find_student = lambda svc, q: ROW
insights.courses_for = lambda svc, run_id, tri=None: {}
_series_seen = []
insights._series = lambda svc, run_id, sid, courses: (
    _series_seen.append(sid) or [{"trimester": "6", "mark_pct": 88, "attendance_pct": 85}])
insights.cohort_rollup = lambda svc, run_id, courses: {
    RESOLVED_ID: {"mark_pct": 88, "attendance_pct": 85, "zeros": []}}

_series_seen.clear()
r = insights._traj_impl(Svc(), P(RAW))
check("trajectory series keyed on RESOLVED id", _series_seen == [RESOLVED_ID])
check("trajectory echoes resolved id", r["student_id"] == RESOLVED_ID)

_series_seen.clear()
r = insights._360_impl(Svc(), P(RAW))
check("360 series keyed on RESOLVED id", _series_seen == [RESOLVED_ID])
check("360 cohort lookup hit (resolved id matches rollup key)", r["latest_mark_pct"] == 88)
check("360 echoes resolved id", r["student"]["student_id"] == RESOLVED_ID)


# ==========================================================================
print("\n[ reports.py — classic _report_impl + one-pager cache fetch ]")
common.find_student = lambda svc, q: ROW  # _report_impl imports find_student from tools.common
_one_report_seen = []
reports.one_report = lambda svc, sid, campus=None, batch=None, trimester=None, cols="*": (
    _one_report_seen.append(sid) or None)

_one_report_seen.clear()
reports._report_impl(Svc(), P(RAW))
check("classic report keyed on RESOLVED id", _one_report_seen == [RESOLVED_ID])


class _Q:
    def __init__(self, seen):
        self.seen = seen

    def select(self, *a, **k):
        return self

    def eq(self, col, val):
        if col == "student_id":
            self.seen.append(val)
        return self

    def limit(self, n):
        return self

    def execute(self):
        class _R:
            data = []
        return _R()


class _Client:
    def __init__(self, seen):
        self.seen = seen

    def table(self, name):
        return _Q(self.seen)


class SvcC:
    def __init__(self, seen):
        self.client = _Client(seen)

    def latest_run(self, campus, batch):
        return "run1"

    def apply_student(self, q, col="student_id"):
        return q


_op_seen = []
reports._onepager_fetch(SvcC(_op_seen), P(RAW))
check("one-pager cache keyed on RESOLVED id", _op_seen == [RESOLVED_ID])


# The RETURN payload must echo the resolved id too (regression: it returned the raw
# name while keying the query on sid). Exercise the return branch with a narrative row.
class _QRow:
    def select(self, *a, **k):
        return self

    def eq(self, *a, **k):
        return self

    def limit(self, n):
        return self

    def execute(self):
        class _R:
            data = [{"trimester": "5", "created_at": "2026-01-01T00:00:00Z",
                     "narrative": {"personal_pattern": {"headline": "H", "observation": "O"}}}]
        return _R()


class SvcRow:
    client = type("C", (), {"table": lambda self, name: _QRow()})()

    def latest_run(self, campus, batch):
        return "run1"

    def apply_student(self, q, col="student_id"):
        return q


_ret = reports._onepager_fetch(SvcRow(), P(RAW))
check("one-pager RETURN echoes RESOLVED id (not the raw name)",
      isinstance(_ret, dict) and _ret.get("student", {}).get("student_id") == RESOLVED_ID)

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
