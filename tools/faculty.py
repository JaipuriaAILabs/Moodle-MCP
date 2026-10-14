"""Faculty & course-assignment tools — read-only, campus-scoped.

Reads the faculty/course snapshot ingested from the v2 Moodle API (see
moodle-agent/app/ingest/faculty.py) through the `current_faculty_*` views. This is a STAFF
surface: who teaches what, per-course mid/end-term feedback volume, and a campus faculty
directory. It is deliberately NOT in STUDENT_SELF_TOOLS, so a student session can never reach it.

Campus scope is enforced exactly like every other tool — via ``svc.apply_campus`` / the
``campus_scope(...) == []`` guard — so a scoped dean only ever sees their own campus and a
cross-campus principal sees all. These tools never return student identifiers; faculty are staff.
Output fields are named ``faculty_name`` (never the reserved ``name``/``full_name``/``first_name``)
so the in-server student-PII redactor leaves the staff directory intact.
"""
from collections import defaultdict

from pydantic import BaseModel, Field

from annotations import READONLY_ANNOTATIONS
from guardrails import clamp_limit, not_found

_ASSIGNMENT = "current_faculty_course_assignments"
_FEEDBACK = "current_faculty_feedback"
# Explicit columns only — never the raw `source_record`/`details` jsonb blobs.
_A_COLS = "campus,batch,term,course_id,course_name,course_code,faculty_id,faculty_username,faculty_name"
_F_COLS = "campus,batch,term,course_id,course_name,faculty_id,faculty_name,phase,total_responses"


class ScopeParams(BaseModel):
    campus: str | None = Field(default=None, description="campus (within your grant); omit for all you can see", max_length=64)
    batch: str | None = Field(default=None, description="batch e.g. '2026-28'", max_length=64)
    term: int | None = Field(default=None, description="trimester 1-6", ge=1, le=6)
    limit: int = Field(default=50, description="max rows, 1-200", ge=1, le=200)


class FacultyParams(ScopeParams):
    faculty: str = Field(description="faculty NAME or faculty id to resolve (within your grant)", max_length=120)


class CourseScopeParams(ScopeParams):
    course: str | None = Field(default=None, description="optional course code / name fragment", max_length=120)


class FeedbackScopeParams(CourseScopeParams):
    # faculty_feedback may be filtered to one teacher; the impl reads ``p.faculty`` (optional).
    faculty: str | None = Field(default=None, description="optional faculty NAME or id to filter to one teacher", max_length=120)


# --- scoped fetch -------------------------------------------------------------
def _scoped_rows(svc, table, cols, p, *, course=None):
    """Campus-scoped rows from a current_faculty_* view, or None when the requested campus
    is outside the caller's grant (so the tool returns a uniform not_found, never a leak)."""
    if p.campus and svc.campus_scope(p.campus) == []:
        return None
    q = svc.client.table(table).select(cols)
    q = svc.apply_campus(q, requested=p.campus)   # campus grant is the tenant boundary
    if p.batch:
        q = q.eq("batch", p.batch)
    if p.term is not None:
        q = q.eq("term", p.term)
    rows = (q.limit(100000).execute()).data or []
    if course:
        c = course.strip().lower()
        rows = [r for r in rows
                if c in (r.get("course_code") or "").lower()
                or c in (r.get("course_name") or "").lower()]
    return rows


def _match_faculty(rows, query):
    """Rows for one faculty member: exact id first, else a case-insensitive name contains."""
    q = (query or "").strip()
    if not q:
        return []
    exact = [r for r in rows if (r.get("faculty_id") or "") == q]
    if exact:
        return exact
    ql = q.lower()
    return [r for r in rows if ql in (r.get("faculty_name") or "").lower()]


def _course_entry(r):
    return {"course_id": r.get("course_id"), "course_code": r.get("course_code"),
            "course_name": r.get("course_name"), "batch": r.get("batch"), "term": r.get("term")}


def _aggregate_faculty(rows):
    """rows -> per-faculty {faculty_id, faculty_name, campus, course_count, courses[]}, busiest first."""
    by = defaultdict(lambda: {"faculty_id": None, "faculty_name": None, "campus": None,
                              "faculty_username": None, "courses": []})
    for r in rows:
        fid = r.get("faculty_id") or r.get("faculty_name")
        if not fid:
            continue
        f = by[fid]
        f["faculty_id"] = r.get("faculty_id")
        f["faculty_name"] = r.get("faculty_name")
        f["faculty_username"] = r.get("faculty_username")
        f["campus"] = r.get("campus")
        f["courses"].append(_course_entry(r))
    out = []
    for f in by.values():
        f["course_count"] = len(f["courses"])
        out.append(f)
    out.sort(key=lambda f: (-f["course_count"], (f.get("faculty_name") or "")))
    return out


# --- tool implementations -----------------------------------------------------
def _list_faculty_impl(svc, p) -> dict:
    rows = _scoped_rows(svc, _ASSIGNMENT, _A_COLS, p)
    if rows is None:
        return not_found("faculty")
    faculty = _aggregate_faculty(rows)
    if p.limit:
        faculty = faculty[:clamp_limit(p.limit, default=50)]
    return {"count": len(faculty),
            "faculty": [{"faculty_id": f["faculty_id"], "faculty_name": f["faculty_name"],
                         "campus": f["campus"], "course_count": f["course_count"]}
                        for f in faculty]}


def _faculty_courses_impl(svc, p) -> dict:
    rows = _scoped_rows(svc, _ASSIGNMENT, _A_COLS, p)
    if rows is None:
        return not_found("faculty")
    mine = _match_faculty(rows, p.faculty)
    if not mine:
        return {"found": False, "note": (f"'{p.faculty}' did not match any faculty in the scope "
                                         "you can access — check the name or faculty id.")}
    agg = _aggregate_faculty(mine)
    if len(agg) > 1:
        return {"found": True, "ambiguous": True,
                "candidates": [{"faculty_id": f["faculty_id"], "faculty_name": f["faculty_name"],
                                "campus": f["campus"], "course_count": f["course_count"]} for f in agg]}
    f = agg[0]
    return {"found": True, "faculty_id": f["faculty_id"], "faculty_name": f["faculty_name"],
            "campus": f["campus"], "course_count": f["course_count"], "courses": f["courses"]}


def _course_faculty_impl(svc, p) -> dict:
    rows = _scoped_rows(svc, _ASSIGNMENT, _A_COLS, p, course=p.course)
    if rows is None:
        return not_found("courses")
    rows.sort(key=lambda r: ((r.get("course_code") or ""), (r.get("faculty_name") or "")))
    total = len(rows)
    rows = rows[:clamp_limit(p.limit, default=50)]
    return {"count": total, "showing": len(rows),
            "assignments": [{"course_id": r.get("course_id"), "course_code": r.get("course_code"),
                             "course_name": r.get("course_name"), "campus": r.get("campus"),
                             "batch": r.get("batch"), "term": r.get("term"),
                             "faculty_id": r.get("faculty_id"), "faculty_name": r.get("faculty_name")}
                            for r in rows]}


def _faculty_feedback_impl(svc, p) -> dict:
    rows = _scoped_rows(svc, _FEEDBACK, _F_COLS, p, course=p.course)
    if rows is None:
        return not_found("feedback")
    if p.faculty:
        rows = _match_faculty(rows, p.faculty)
    rows.sort(key=lambda r: ((r.get("course_code") or r.get("course_name") or ""), r.get("phase") or ""))
    total = len(rows)
    rows = rows[:clamp_limit(p.limit, default=50)]
    return {"count": total, "showing": len(rows),
            "feedback": [{"course_id": r.get("course_id"), "course_name": r.get("course_name"),
                          "campus": r.get("campus"), "batch": r.get("batch"), "term": r.get("term"),
                          "faculty_id": r.get("faculty_id"), "faculty_name": r.get("faculty_name"),
                          "phase": r.get("phase"), "total_responses": r.get("total_responses")}
                         for r in rows]}


def register(mcp, get_service):
    @mcp.tool(title="List Faculty", annotations=READONLY_ANNOTATIONS)
    async def list_faculty(params: CourseScopeParams) -> dict:
        """
        WHAT: The faculty directory for a campus — each teacher with how many courses they are
        assigned, from the latest ingested faculty/course snapshot.
        USE WHEN they say: 'list faculty in noida', 'who teaches in lucknow', 'faculty directory',
        'how many faculty in jaipur', 'teaching load by faculty'.
        DO NOT USE WHEN they want a specific teacher's courses (faculty_courses) or student data.
        RETURNS: count + faculty[] (faculty_id, faculty_name, campus, course_count). Pass campus
        (and optionally batch/term) to scope; omit campus to span every campus you may access.
        """
        return _list_faculty_impl(await get_service(), params)

    @mcp.tool(title="Faculty Courses", annotations=READONLY_ANNOTATIONS)
    async def faculty_courses(params: FacultyParams) -> dict:
        """
        WHAT: The courses a specific faculty member is assigned, resolved by NAME or faculty id.
        USE WHEN they say: 'what does <name> teach', 'courses for faculty X', 'teaching assignments
        of <name>', 'which subjects does <name> take'.
        DO NOT USE WHEN they want the whole directory (list_faculty) or feedback (faculty_feedback).
        RETURNS: the resolved faculty + courses[] (course_code, course_name, batch, term). An
        ambiguous name returns candidates to choose from.
        """
        return _faculty_courses_impl(await get_service(), params)

    @mcp.tool(title="Course Faculty", annotations=READONLY_ANNOTATIONS)
    async def course_faculty(params: CourseScopeParams) -> dict:
        """
        WHAT: Course-to-faculty assignments for a scope — who teaches each course.
        USE WHEN they say: 'who teaches <course>', 'faculty for <course code>', 'course assignments
        in noida 2026-28 term 1', 'list courses and their teachers'.
        DO NOT USE WHEN they want one teacher's full load (faculty_courses) or student marks.
        RETURNS: count + assignments[] (course_code, course_name, faculty_name, campus, batch, term).
        Pass campus/batch/term to scope; pass course to filter to one course code/name.
        """
        return _course_faculty_impl(await get_service(), params)

    @mcp.tool(title="Faculty Feedback", annotations=READONLY_ANNOTATIONS)
    async def faculty_feedback(params: FeedbackScopeParams) -> dict:
        """
        WHAT: Per-course student-feedback response volume (mid-term and end-term) from the faculty
        snapshot — how many responses each course/phase received.
        USE WHEN they say: 'feedback responses for <course>', 'how many filled the mid-term feedback',
        'end-term feedback counts in lucknow', 'feedback volume by course'.
        DO NOT USE WHEN they want marks/attendance or report narratives.
        RETURNS: count + feedback[] (course_name, faculty_name, phase, total_responses, campus/batch/term).
        Pass campus/batch/term to scope; course to filter one course; faculty to filter one teacher.
        """
        return _faculty_feedback_impl(await get_service(), params)
