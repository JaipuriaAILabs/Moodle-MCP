"""Student data — the primary surface. Roster, and each student's RAW marks and attendance read
straight from the Moodle tables (not the generated report). Covers every ingested student.
Read-only, campus-scoped."""
from collections import defaultdict

from pydantic import BaseModel, Field

from annotations import READONLY_ANNOTATIONS
from guardrails import enrolled_no_data, clamp_limit, not_found
from tools.common import (attendance_for, attendance_pct, courses_for, find_student,
                          marks_for, pct)


class RosterParams(BaseModel):
    campus: str = Field(description="campus (within your grant)", max_length=64)
    batch: str = Field(description="batch e.g. '2024-26'", max_length=64)
    section: str | None = Field(default=None, description="optional section/group filter", max_length=64)
    query: str | None = Field(default=None, description="optional name / enrolment-id fragment", max_length=120)
    limit: int = Field(default=50, description="max students, 1-50", ge=1, le=50)


class StudentParams(BaseModel):
    student_id: str = Field(description="enrolment id OR student name — e.g. 'JJ24PG099' or 'Aashna Gupta'. A unique name resolves automatically; an ambiguous one returns candidates to choose from.", max_length=120)
    trimester: str | None = Field(default=None, description="restrict to one trimester's subjects", max_length=8)


def _roster_impl(svc, p: RosterParams) -> dict:
    if svc.campus_scope(p.campus) == []:
        return not_found("students")
    q = (svc.client.table("students")
         .select("student_id,student_name,campus,batch,section_group")
         .eq("campus", p.campus).eq("batch", p.batch))
    # A student session shares this tool surface with educators, so the row-owner
    # boundary must be applied even to roster queries. Without this filter a student
    # could list every name and enrolment id in their campus/batch.
    rows = (svc.apply_student(q).limit(100000).execute()).data or []
    seen, uniq = set(), []
    for r in rows:  # students table is per-run; dedupe by id
        if r["student_id"] in seen:
            continue
        seen.add(r["student_id"]); uniq.append(r)
    if p.section:
        uniq = [r for r in uniq if (r.get("section_group") or "") == p.section]
    if p.query:
        ql = p.query.lower()
        uniq = [r for r in uniq if ql in (r.get("student_name") or "").lower()
                or ql in (r.get("student_id") or "").lower()]
    uniq.sort(key=lambda r: (r.get("student_name") or ""))
    total = len(uniq)
    uniq = uniq[:clamp_limit(p.limit)]
    return {"count": total, "showing": len(uniq),
            "students": [{"student_id": r["student_id"], "name": r.get("student_name"),
                          "section": r.get("section_group")} for r in uniq]}


def _resolve(svc, student_id, trimester):
    stu = find_student(svc, student_id)
    if not stu:
        return None
    run_id = svc.latest_run(stu["campus"], stu["batch"])
    if not run_id:
        return (stu, None, None)  # enrolled, but no completed run -> no graded data
    courses = courses_for(svc, run_id, trimester)
    return stu, run_id, courses


def _subject_rollup(courses, marks, att):
    by_course_marks = defaultdict(list)
    for m in marks:
        by_course_marks[m["course_id"]].append(m)
    by_course_att = defaultdict(list)
    for a in att:
        by_course_att[a["course_id"]].append(a)
    subjects = []
    for cid, meta in courses.items():
        cms = by_course_marks.get(cid, [])
        graded = [m for m in cms if m.get("graded")]
        # Only components with BOTH a recorded score AND max count toward the subject
        # total. The end-term (__total__ / kind ET) row carries an obtained score but a
        # NULL max, so summing obtained and max independently would add to the numerator
        # without the denominator and push the % past 100. Pair them (mirrors
        # cohort_rollup, so get_student and marks_overview stay consistent).
        scored = [m for m in graded
                  if isinstance(m.get("obtained_score"), (int, float))
                  and isinstance(m.get("max_score"), (int, float))]
        tot_obt = sum(float(m["obtained_score"]) for m in scored)
        tot_max = sum(float(m["max_score"]) for m in scored)
        present, total, apct = attendance_pct(by_course_att.get(cid, []))
        if not cms and not by_course_att.get(cid):
            continue
        subjects.append({
            "subject": meta["subject"], "trimester": meta["trimester"],
            "overall_mark_pct": pct(tot_obt, tot_max),
            "components": [{"component": m.get("component_label"), "kind": m.get("kind"),
                            "obtained": m.get("obtained_score"), "max": m.get("max_score"),
                            "pct": pct(m.get("obtained_score"), m.get("max_score")),
                            "graded": m.get("graded")} for m in cms],
            "attendance": {"present": present, "sessions": total, "pct": apct},
        })
    subjects.sort(key=lambda s: (s["overall_mark_pct"] is None, s["overall_mark_pct"] or 0))
    return subjects


def _student_impl(svc, p: StudentParams) -> dict:
    got = _resolve(svc, p.student_id, p.trimester)
    if not got:
        return not_found("student")
    stu, run_id, courses = got
    if run_id is None:
        return enrolled_no_data(stu)
    # p.student_id may be a NAME; find_student resolved it to the row. Use the resolved
    # enrolment id for the marks/attendance lookups (they key on the exact id) and echo it
    # back — else a name returns the right header but zero data.
    sid = stu["student_id"]
    cids = list(courses)
    marks = marks_for(svc, run_id, student_id=sid, course_ids=cids)
    att = attendance_for(svc, run_id, student_id=sid, course_ids=cids)
    subjects = _subject_rollup(courses, marks, att)
    ov_att = attendance_pct(att)
    return {"found": True,
            "student": {"student_id": sid, "name": stu.get("student_name"),
                        "campus": stu.get("campus"), "batch": stu.get("batch"),
                        "section": stu.get("section_group")},
            "trimester": p.trimester or "all",
            "subjects_count": len(subjects),
            "overall_attendance_pct": ov_att[2],
            "subjects": subjects}


def _marks_impl(svc, p: StudentParams) -> dict:
    got = _resolve(svc, p.student_id, p.trimester)
    if not got:
        return not_found("student")
    stu, run_id, courses = got
    if run_id is None:
        return enrolled_no_data(stu)
    sid = stu["student_id"]  # resolved id (find_student accepts a name); see _student_impl
    marks = marks_for(svc, run_id, student_id=sid, course_ids=list(courses))
    out = []
    for m in marks:
        meta = courses.get(m["course_id"], {})
        out.append({"subject": meta.get("subject"), "trimester": meta.get("trimester"),
                    "component": m.get("component_label"), "kind": m.get("kind"),
                    "obtained": m.get("obtained_score"), "max": m.get("max_score"),
                    "pct": pct(m.get("obtained_score"), m.get("max_score")),
                    "graded": m.get("graded")})
    out.sort(key=lambda x: (x["subject"] or "", x["component"] or ""))
    return {"student_id": sid, "name": stu.get("student_name"),
            "components": len(out), "marks": out}


def _attendance_impl(svc, p: StudentParams) -> dict:
    got = _resolve(svc, p.student_id, p.trimester)
    if not got:
        return not_found("student")
    stu, run_id, courses = got
    if run_id is None:
        return enrolled_no_data(stu)
    sid = stu["student_id"]  # resolved id (find_student accepts a name); see _student_impl
    att = attendance_for(svc, run_id, student_id=sid, course_ids=list(courses))
    by_course = defaultdict(list)
    for a in att:
        by_course[a["course_id"]].append(a)
    subjects = []
    for cid, rows in by_course.items():
        present, total, apct = attendance_pct(rows)
        subjects.append({"subject": courses.get(cid, {}).get("subject"),
                         "present": present, "sessions": total, "attendance_pct": apct})
    subjects.sort(key=lambda s: (s["attendance_pct"] is None, s["attendance_pct"] or 0))
    ov = attendance_pct(att)
    return {"student_id": sid, "name": stu.get("student_name"),
            "overall_attendance_pct": ov[2], "overall_sessions": ov[1], "subjects": subjects}


def register(mcp, get_service):
    @mcp.tool(title="List Students", annotations=READONLY_ANNOTATIONS)
    async def list_students(params: RosterParams) -> dict:
        """
        WHAT: The student roster for a campus/batch (optionally one section) — every ingested
        student, with name and section. Report status is irrelevant here.
        USE WHEN they say: 'list students in indore 2024-26', 'class roster', 'who is in section A',
        'find <name>', 'how many students in this batch'.
        DO NOT USE WHEN they want one student's marks (get_student) or subject data.
        RETURNS: count + students (student_id, name, section).
        """
        return _roster_impl(await get_service(), params)

    @mcp.tool(title="Get Student", annotations=READONLY_ANNOTATIONS)
    async def get_student(params: StudentParams) -> dict:
        """
        WHAT: One student's complete raw data — every enrolled subject with its component marks
        (obtained/max/%), an overall per-subject mark, and per-subject attendance — straight from
        Moodle. Works for ANY ingested student, with or without a generated report.
        USE WHEN they say: 'show me <id>'s marks and attendance', 'how is <name> doing',
        'full record for JJ24PG099', 'what did this student score'.
        DO NOT USE WHEN they want only marks (student_marks) or only attendance (student_attendance),
        or a whole subject/cohort.
        RETURNS: student, subjects[] with components + attendance, overall attendance. Pass
        trimester to scope to one term.
        """
        return _student_impl(await get_service(), params)

    @mcp.tool(title="Student Marks", annotations=READONLY_ANNOTATIONS)
    async def student_marks(params: StudentParams) -> dict:
        """
        WHAT: A flat, component-level list of one student's marks (subject, component, obtained/max,
        %, graded flag) — the raw gradebook rows.
        USE WHEN they say: 'list <id>'s marks', 'component-wise scores', 'what did they get in each
        quiz/assignment', 'gradebook for <name>'.
        DO NOT USE WHEN they want attendance or the combined record (get_student).
        RETURNS: marks[] (subject, component, obtained, max, pct, graded).
        """
        return _marks_impl(await get_service(), params)

    @mcp.tool(title="Student Attendance", annotations=READONLY_ANNOTATIONS)
    async def student_attendance(params: StudentParams) -> dict:
        """
        WHAT: One student's attendance per subject — sessions present, total sessions, and % — from
        the raw attendance records.
        USE WHEN they say: 'what is <id>'s attendance', 'attendance per subject', 'how many classes
        did <name> miss', 'attendance record'.
        DO NOT USE WHEN they want marks (student_marks) or the combined record.
        RETURNS: overall_attendance_pct + per-subject present/sessions/pct.
        """
        return _attendance_impl(await get_service(), params)
