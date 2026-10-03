"""Subject / course data — the catalog of subjects and a whole subject's marks + attendance across
the cohort, from the raw tables. Read-only, campus-scoped."""
from collections import defaultdict

from pydantic import BaseModel, Field

from annotations import READONLY_ANNOTATIONS
from guardrails import not_found
from tools.common import (attendance_for, attendance_pct, course_section, courses_for, marks_for, pct, scope_marks)


class ScopeParams(BaseModel):
    campus: str = Field(description="campus (within your grant)", max_length=64)
    batch: str = Field(description="batch e.g. '2024-26'", max_length=64)
    trimester: str | None = Field(default=None, description="restrict to one trimester", max_length=8)


class SubjectParams(ScopeParams):
    subject: str = Field(description="subject-name fragment, e.g. 'Wealth Management'", max_length=120)


def _run(svc, p):
    if svc.campus_scope(p.campus) == []:
        return None
    return svc.latest_run(p.campus, p.batch)


def _mean(xs, nd=1):
    xs = [x for x in xs if isinstance(x, (int, float))]
    return round(sum(xs) / len(xs), nd) if xs else None


def _list_impl(svc, p: ScopeParams) -> dict:
    run_id = _run(svc, p)
    if not run_id:
        return {"available": False, "note": "no completed run for this scope"}
    courses = courses_for(svc, run_id, p.trimester)
    enr_q = (svc.client.table("enrolments").select("course_id,student_id")
             .eq("run_id", run_id))
    enr = (svc.apply_student(enr_q).limit(100000).execute()).data or []
    counts = defaultdict(set)
    for e in enr:
        counts[e["course_id"]].add(e["student_id"])
    if getattr(svc, "self_student_id", None):
        # Do not expose the wider campus curriculum to a student session; keep only
        # courses in which the caller's own row appears after the self filter above.
        courses = {cid: meta for cid, meta in courses.items() if counts.get(cid)}
    by_subject = defaultdict(lambda: {"trimester": None, "sections": 0, "students": set()})
    for cid, meta in courses.items():
        s = by_subject[meta["subject"]]
        s["trimester"] = meta["trimester"]
        s["sections"] += 1
        s["students"] |= counts.get(cid, set())
    subjects = [{"subject": name, "trimester": v["trimester"], "sections": v["sections"],
                 "students": len(v["students"])} for name, v in by_subject.items()]
    subjects.sort(key=lambda x: (str(x["trimester"]), x["subject"]))
    return {"campus": p.campus, "batch": p.batch, "subject_count": len(subjects),
            "subjects": subjects}


def _match_courses(courses, fragment):
    fl = fragment.lower()
    return {cid: m for cid, m in courses.items() if fl in m["subject"].lower()}


def _subject_impl(svc, p: SubjectParams) -> dict:
    run_id = _run(svc, p)
    if not run_id:
        return {"available": False, "note": "no completed run for this scope"}
    courses = _match_courses(courses_for(svc, run_id, p.trimester), p.subject)
    if not courses:
        return not_found("subject")
    cids = list(courses)
    marks = marks_for(svc, run_id, course_ids=cids)
    att = attendance_for(svc, run_id, course_ids=cids)
    # per-student overall subject mark (graded components), then cohort distribution
    per_student = defaultdict(lambda: [0.0, 0.0])
    comp = defaultdict(lambda: {"obt": [], "kind": None})
    for m in marks:
        if m.get("graded") and isinstance(m.get("obtained_score"), (int, float)) \
                and isinstance(m.get("max_score"), (int, float)):
            per_student[m["student_id"]][0] += m["obtained_score"]
            per_student[m["student_id"]][1] += m["max_score"]
        c = comp[m.get("component_label")]
        c["kind"] = m.get("kind")
        c["obt"].append(pct(m.get("obtained_score"), m.get("max_score")))
    student_pcts = [pct(o, mx) for o, mx in per_student.values() if mx]
    present, total, apct = attendance_pct(att)
    subject_name = next(iter(courses.values()))["subject"]
    return {"subject": subject_name, "campus": p.campus, "batch": p.batch,
            "trimester": next(iter(courses.values()))["trimester"],
            "sections": len(cids), "students_graded": len(student_pcts),
            "mean_mark_pct": _mean(student_pcts),
            "pass_rate_pct": (round(100 * sum(1 for x in student_pcts if x >= 40) / len(student_pcts), 1)
                              if student_pcts else None),
            "cohort_attendance_pct": apct,
            "components": [{"component": name, "kind": c["kind"],
                            "mean_pct": _mean(c["obt"]), "n": len(c["obt"])}
                           for name, c in comp.items()]}


def _student_means(marks, cids):
    """course_id -> {student_id -> overall graded mark %} for a set of courses."""
    from collections import defaultdict
    agg = defaultdict(lambda: defaultdict(lambda: [0.0, 0.0]))
    for m in marks:
        if m["course_id"] in cids and m.get("graded") \
                and isinstance(m.get("obtained_score"), (int, float)) \
                and isinstance(m.get("max_score"), (int, float)):
            a = agg[m["course_id"]][m["student_id"]]
            a[0] += m["obtained_score"]; a[1] += m["max_score"]
    return {cid: {sid: pct(o, mx) for sid, (o, mx) in st.items() if mx} for cid, st in agg.items()}


def _section_impl(svc, p: SubjectParams) -> dict:
    run_id = _run(svc, p)
    if not run_id:
        return {"available": False, "note": "no completed run for this scope"}
    courses = _match_courses(courses_for(svc, run_id, p.trimester), p.subject)
    if not courses:
        return not_found("subject")
    cids = list(courses)
    means = _student_means(scope_marks(svc, run_id, cids), set(cids))
    att = attendance_for(svc, run_id, course_ids=cids)
    att_by_course = {}
    from collections import defaultdict
    tmp = defaultdict(list)
    for a in att:
        tmp[a["course_id"]].append(a)
    for cid, rows in tmp.items():
        att_by_course[cid] = attendance_pct(rows)[2]
    sections = []
    for cid, meta in courses.items():
        vals = list(means.get(cid, {}).values())
        sections.append({"section": course_section(meta["code"]), "students": len(vals),
                         "mean_mark_pct": _mean(vals), "attendance_pct": att_by_course.get(cid)})
    sections.sort(key=lambda s: (s["mean_mark_pct"] is None, -(s["mean_mark_pct"] or 0)))
    subj = next(iter(courses.values()))["subject"]
    spread = [s["mean_mark_pct"] for s in sections if s["mean_mark_pct"] is not None]
    return {"subject": subj, "campus": p.campus, "batch": p.batch, "sections": sections,
            "mark_spread_pts": (round(max(spread) - min(spread), 1) if len(spread) > 1 else 0),
            "note": "Compare section means — a large spread can signal a teaching/marking difference."}


def _assessment_impl(svc, p: ScopeParams) -> dict:
    run_id = _run(svc, p)
    if not run_id:
        return {"available": False, "note": "no completed run for this scope"}
    courses = courses_for(svc, run_id, p.trimester)
    marks = scope_marks(svc, run_id, list(courses))
    from collections import defaultdict
    agg = defaultdict(list)
    for m in marks:
        if m.get("graded"):
            agg[m.get("kind") or "other"].append(pct(m.get("obtained_score"), m.get("max_score")))
    kinds = [{"assessment_kind": k, "graded_rows": len(v), "mean_pct": _mean(v),
              "pass_rate_pct": (round(100 * sum(1 for x in v if x is not None and x >= 40)
                                      / len([x for x in v if x is not None]), 1)
                                if any(x is not None for x in v) else None)}
             for k, v in agg.items()]
    kinds.sort(key=lambda x: (x["mean_pct"] is None, x["mean_pct"] or 0))
    return {"campus": p.campus, "batch": p.batch, "trimester": p.trimester or "all",
            "by_assessment_kind": kinds,
            "note": "Sorted weakest-mean first — which assessment types drag the cohort."}


def _difficulty_impl(svc, p: ScopeParams) -> dict:
    run_id = _run(svc, p)
    if not run_id:
        return {"available": False, "note": "no completed run for this scope"}
    courses = courses_for(svc, run_id, p.trimester)
    marks = scope_marks(svc, run_id, list(courses))
    from collections import defaultdict
    by_subj = defaultdict(lambda: defaultdict(lambda: [0.0, 0.0]))
    zeros = defaultdict(int)
    for m in marks:
        subj = courses.get(m["course_id"], {}).get("subject")
        if not subj or not m.get("graded"):
            continue
        if isinstance(m.get("obtained_score"), (int, float)) and isinstance(m.get("max_score"), (int, float)):
            a = by_subj[subj][m["student_id"]]
            a[0] += m["obtained_score"]; a[1] += m["max_score"]
            if m["obtained_score"] == 0:
                zeros[subj] += 1
    rows = []
    for subj, st in by_subj.items():
        pcts = [pct(o, mx) for o, mx in st.values() if mx]
        if not pcts:
            continue
        rows.append({"subject": subj, "students": len(pcts), "mean_mark_pct": _mean(pcts),
                     "pass_rate_pct": round(100 * sum(1 for x in pcts if x >= 40) / len(pcts), 1),
                     "recorded_zeros": zeros.get(subj, 0)})
    rows.sort(key=lambda x: (x["pass_rate_pct"], -x["recorded_zeros"]))
    return {"campus": p.campus, "batch": p.batch, "trimester": p.trimester or "all",
            "hardest_first": rows[:20],
            "note": "Ranked by lowest pass rate then most zeros — the toughest subjects."}


def register(mcp, get_service):
    @mcp.tool(title="List Subjects", annotations=READONLY_ANNOTATIONS)
    async def list_subjects(params: ScopeParams) -> dict:
        """
        WHAT: The subjects/courses offered for a campus/batch (optionally one trimester), each with
        its trimester, number of sections, and enrolled-student count.
        USE WHEN they say: 'what subjects are there', 'list courses in 2024-26', 'which subjects in
        trimester 5', 'how many sections of <subject>'.
        DO NOT USE WHEN they want marks in a subject (subject_performance) or a student.
        RETURNS: subjects[] (subject, trimester, sections, students).
        """
        return _list_impl(await get_service(), params)

    @mcp.tool(title="Subject Performance", annotations=READONLY_ANNOTATIONS)
    async def subject_performance(params: SubjectParams) -> dict:
        """
        WHAT: A whole subject's data across the cohort — mean student mark %, pass rate, cohort
        attendance, and a per-component mean — computed from the raw marks/attendance rows.
        USE WHEN they say: 'how did the class do in <subject>', 'pass rate in wealth management',
        'component-wise averages for <subject>', 'subject performance'.
        DO NOT USE WHEN they want one student (get_student) or the subject list (list_subjects).
        RETURNS: mean_mark_pct, pass_rate_pct, cohort_attendance_pct, components[].
        """
        return _subject_impl(await get_service(), params)

    @mcp.tool(title="Section Compare", annotations=READONLY_ANNOTATIONS)
    async def section_compare(params: SubjectParams) -> dict:
        """
        WHAT: Compare the SECTIONS of one subject (A vs B vs C) — each section's mean mark % and
        attendance, plus the spread. A teaching / marking fairness signal.
        USE WHEN they say: 'compare sections of <subject>', 'is section A better than B', 'which
        section scored higher in wealth management', 'section-wise'.
        DO NOT USE WHEN they want the whole subject (subject_performance) or one student.
        RETURNS: sections[] with mean_mark_pct + attendance, and mark_spread_pts.
        """
        return _section_impl(await get_service(), params)

    @mcp.tool(title="Assessment Breakdown", annotations=READONLY_ANNOTATIONS)
    async def assessment_breakdown(params: ScopeParams) -> dict:
        """
        WHAT: Cohort performance by ASSESSMENT KIND (quiz vs assignment vs project vs class
        participation, etc.) — mean % and pass rate per kind, weakest first.
        USE WHEN they say: 'how do students do on quizzes vs assignments', 'which assessment type is
        weakest', 'assessment breakdown', 'are projects dragging scores'.
        DO NOT USE WHEN they want one subject (subject_performance) or overall marks.
        RETURNS: by_assessment_kind[] with mean_pct and pass_rate_pct.
        """
        return _assessment_impl(await get_service(), params)

    @mcp.tool(title="Subject Difficulty", annotations=READONLY_ANNOTATIONS)
    async def subject_difficulty(params: ScopeParams) -> dict:
        """
        WHAT: Subjects ranked hardest-first by lowest pass rate then most recorded zeros — the
        curriculum pressure points.
        USE WHEN they say: 'which subjects are hardest', 'lowest pass rate', 'where are students
        failing most', 'toughest subjects', 'curriculum difficulty'.
        DO NOT USE WHEN they want one subject's detail (subject_performance).
        RETURNS: hardest_first[] with pass_rate_pct, mean_mark_pct, recorded_zeros.
        """
        return _difficulty_impl(await get_service(), params)
