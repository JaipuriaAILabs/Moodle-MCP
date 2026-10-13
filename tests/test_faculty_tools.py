"""Faculty/course tools — campus-scope + shaping (fakes, no DB). Plain-assert script."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import faculty as F  # noqa: E402

ROWS = [
    {"campus": "noida", "batch": "2026-28", "term": 1, "course_id": "c1",
     "course_code": "30101_26_1_A", "course_name": "Marketing", "faculty_id": "F1",
     "faculty_username": "a.rao", "faculty_name": "Anil Rao"},
    {"campus": "noida", "batch": "2026-28", "term": 1, "course_id": "c2",
     "course_code": "30102_26_1_A", "course_name": "Finance", "faculty_id": "F1",
     "faculty_username": "a.rao", "faculty_name": "Anil Rao"},
    {"campus": "noida", "batch": "2026-28", "term": 1, "course_id": "c3",
     "course_code": "30103_26_1_A", "course_name": "Stats", "faculty_id": "F2",
     "faculty_username": "b.sen", "faculty_name": "Bela Sen"},
    {"campus": "jaipur", "batch": "2026-28", "term": 1, "course_id": "c9",
     "course_code": "30901_26_1_A", "course_name": "Ops", "faculty_id": "F9",
     "faculty_username": "c.jha", "faculty_name": "Chetan Jha"},
]


class FakeQuery:
    def __init__(self, data):
        self._data = data
        self._preds = []

    def select(self, *_a, **_k):
        return self

    def eq(self, col, val):
        self._preds.append(lambda r: str(r.get(col)) == str(val))
        return self

    def in_(self, col, vals):
        vs = {str(v) for v in vals}
        self._preds.append(lambda r: str(r.get(col)) in vs)
        return self

    def limit(self, _n):
        return self

    def execute(self):
        rows = [r for r in self._data if all(p(r) for p in self._preds)]
        return type("R", (), {"data": rows})()


class FakeClient:
    def __init__(self, data):
        self._data = data

    def table(self, _name):
        return FakeQuery(list(self._data))


class FakeService:
    def __init__(self, allowed, data=ROWS):
        self.allowed_campuses = allowed
        self.client = FakeClient(data)

    def campus_scope(self, requested):
        requested = requested.strip().lower() if isinstance(requested, str) else requested
        if self.allowed_campuses is None:
            return [requested] if requested else None
        allowed = list(self.allowed_campuses)
        if not requested:
            return allowed
        return [requested] if requested in allowed else []

    def apply_campus(self, query, col="campus", requested=None):
        scope = self.campus_scope(requested)
        if scope is None:
            return query
        return query.in_(col, scope)


def P(cls, **kw):
    return cls(**kw)


# --- pure helpers ---
agg = F._aggregate_faculty(ROWS)
assert agg[0]["faculty_id"] == "F1" and agg[0]["course_count"] == 2, "busiest faculty first"
assert {f["faculty_id"] for f in agg} == {"F1", "F2", "F9"}
assert [r["course_id"] for r in F._match_faculty(ROWS, "F2")] == ["c3"], "exact id match"
assert len(F._match_faculty(ROWS, "anil")) == 2, "case-insensitive name contains"
assert F._match_faculty(ROWS, "nobody") == []

# --- campus scope: a noida-scoped dean never sees jaipur ---
noida = FakeService(["noida"])
out = F._list_faculty_impl(noida, P(F.CourseScopeParams, campus=None))
camps = {f["campus"] for f in out["faculty"]}
assert camps == {"noida"}, f"scoped dean leaked campuses: {camps}"
assert out["count"] == 2

# requesting a campus outside the grant -> uniform not_found, no data
denied = F._course_faculty_impl(noida, P(F.CourseScopeParams, campus="jaipur"))
assert denied.get("found") is False, "out-of-scope campus must deny"

# cross-campus (allowed None) sees every campus
allcamp = FakeService(None)
out_all = F._list_faculty_impl(allcamp, P(F.CourseScopeParams, campus=None))
assert {f["campus"] for f in out_all["faculty"]} == {"noida", "jaipur"}

# faculty_courses resolves one teacher + lists their courses
fc = F._faculty_courses_impl(noida, P(F.FacultyParams, faculty="Anil Rao"))
assert fc["found"] and fc["faculty_id"] == "F1" and fc["course_count"] == 2
assert {c["course_name"] for c in fc["courses"]} == {"Marketing", "Finance"}

# course_faculty filters by course fragment
cf = F._course_faculty_impl(noida, P(F.CourseScopeParams, campus="noida", course="Finance"))
assert cf["count"] == 1 and cf["assignments"][0]["faculty_name"] == "Anil Rao"

# no output field is a reserved student-PII key (would be redacted in enforce mode)
reserved = {"name", "full_name", "first_name", "student_name", "student_email"}
for f in out["faculty"]:
    assert not (set(f) & reserved), f"reserved PII key in output: {set(f) & reserved}"

print("test_faculty_tools: OK")
