"""Report retrieval — find students, open one full report, check data availability. Read-only."""
from pydantic import BaseModel, Field, field_validator

from annotations import READONLY_ANNOTATIONS
from config import settings
from guardrails import clamp_limit, enrolled_no_data, not_found, truncate
from tools.common import one_report, report_rows, student_label

_CARD = "student_id,full_name,first_name,campus,batch,trimester,status,above_average_count,strongest_subject,subjects_count,personal_pattern_kind"


class SearchParams(BaseModel):
    query: str | None = Field(default=None, description="name or enrolment-id fragment to match", max_length=120)
    campus: str | None = Field(default=None, description="campus filter (within your grant)", max_length=64)
    batch: str | None = Field(default=None, description="batch e.g. '2024-26'", max_length=64)
    trimester: str | None = Field(default=None, description="trimester number e.g. '5'", max_length=8)
    limit: int = Field(default=20, description="max cards, 1-50", ge=1, le=50)

    @field_validator("campus")
    @classmethod
    def _campus_lower(cls, value):
        return value.strip().lower() if isinstance(value, str) and value.strip() else None


class ReportParams(BaseModel):
    student_id: str = Field(description="enrolment id OR student name — e.g. 'JJ24PG099' or 'Aashna Gupta'. A unique name resolves automatically; an ambiguous one returns candidates to choose from.", max_length=120)
    campus: str | None = Field(default=None, max_length=64)
    batch: str | None = Field(default=None, max_length=64)
    trimester: str | None = Field(default=None, max_length=8)

    @field_validator("campus")
    @classmethod
    def _campus_lower(cls, value):
        return value.strip().lower() if isinstance(value, str) and value.strip() else None


class AvailabilityParams(BaseModel):
    campus: str | None = Field(default=None, description="one campus (within your grant); omit for all", max_length=64)
    batch: str | None = Field(default=None, description="batch e.g. '2024-26'; omit for all", max_length=64)

    @field_validator("campus")
    @classmethod
    def _campus_lower(cls, value):
        return value.strip().lower() if isinstance(value, str) and value.strip() else None


def _search_impl(svc, p: SearchParams) -> dict:
    rows = report_rows(svc, campus=p.campus, batch=p.batch, trimester=p.trimester, cols=_CARD)
    if p.query:
        ql = p.query.lower()
        rows = [r for r in rows if ql in (r.get("full_name") or "").lower()
                or ql in (r.get("student_id") or "").lower()]
    rows.sort(key=lambda r: (r.get("full_name") or r.get("student_id") or ""))
    rows = rows[:clamp_limit(p.limit)]
    cards = [{**student_label(r), "campus": r.get("campus"), "batch": r.get("batch"),
              "trimester": r.get("trimester"), "subjects": r.get("subjects_count"),
              "subjects_at_or_above_average": r.get("above_average_count"),
              "strongest_subject": r.get("strongest_subject")} for r in rows]
    return {"count": len(cards), "students": cards}


def _report_impl(svc, p: ReportParams) -> dict:
    # one_report keys on the exact enrolment id, so resolve a name -> id first (find_student
    # accepts either); fall back to the raw value if nothing resolves.
    from tools.common import find_student
    resolved = find_student(svc, p.student_id)
    sid = resolved["student_id"] if resolved else p.student_id
    row = one_report(svc, sid, campus=p.campus, batch=p.batch, trimester=p.trimester)
    if not row:
        return not_found("report")
    narr = row.get("narrative") or {}
    pkt = row.get("evidence_packet") or {}
    subjects = pkt.get("subjects", [])
    att = pkt.get("attendance_summary", {})
    actions = [{"title": a.get("title"), "instruction": a.get("instruction") or a.get("detail")}
               for a in (narr.get("actions") or [])[:3]]
    pp = narr.get("personal_pattern") or {}
    ast = narr.get("attendance_story") or {}
    return {
        "found": True,
        "student": {**student_label(row), "campus": row.get("campus"),
                    "batch": row.get("batch"), "trimester": row.get("trimester")},
        "summary": {
            "subjects": row.get("subjects_count"),
            "subjects_at_or_above_average": row.get("above_average_count"),
            "strongest_subject": row.get("strongest_subject"),
            "attendance_at_or_above_average_subjects": att.get("at_or_above_count"),
        },
        "narrative": {
            "headline": pp.get("headline"),
            "personal_pattern": pp.get("observation"),
            "attendance": " ".join(x for x in (ast.get("headline"), ast.get("observation")) if x),
            "next_moves": actions,
        },
        "subjects": [{"subject": s.get("name"), "your_marks": s.get("student_score"),
                      "class_average": s.get("class_marks_average"),
                      "delta": s.get("score_delta"), "attendance": s.get("student_attendance")}
                     for s in subjects],
        "link_status": "not_generated",
        "link_note": "Use create_report when a new shareable link is required.",
    }


def _onepager_fetch(svc, p: ReportParams):
    """The production (one-page) report narrative straight from the cache — the
    same artifact create_report generates, read without an agent round-trip or
    LLM cost. Returns None when no cached narrative exists (caller falls back to
    the legacy classic-format tables). Selects explicit columns only: this table
    also holds the rendered ~1MB html column, which must never ride a tool call."""
    from tools.common import find_student
    row = find_student(svc, p.student_id)
    if not row:
        return None
    if (p.campus and row.get("campus") != p.campus) or \
            (p.batch and row.get("batch") != p.batch):
        return None
    run_id = svc.latest_run(row["campus"], row["batch"])
    if not run_id:
        return None
    # p.student_id may be a NAME; key the cache lookup on the resolved enrolment id.
    sid = row["student_id"]
    q = (svc.client.table("onepager_narratives")
         .select("trimester,narrative,created_at")
         .eq("run_id", run_id).eq("student_id", sid))
    q = svc.apply_student(q)
    if p.trimester:
        q = q.eq("trimester", str(p.trimester))
    rows = q.limit(20).execute().data or []
    if not rows:
        return None
    rows.sort(key=lambda r: int(r["trimester"]) if str(r["trimester"]).isdigit() else -1)
    # Mirror the agent's closed-trimester rule: a just-started trimester (the
    # headline reads "... of 1/2 subjects") loses to the previous full one.
    def _n_subjects(r):
        import re
        m = re.search(r"of\s+(\d+)\s+subjects", (r.get("narrative") or {}).get("headline", ""))
        return int(m.group(1)) if m else 99
    full = [r for r in rows if _n_subjects(r) >= 3]
    latest = (full or rows)[-1]
    n = {k: v for k, v in (latest.get("narrative") or {}).items()
         if not k.startswith("_")}
    if not n:
        return None
    return {
        "found": True, "format": "onepage",
        "student": {"student_id": sid, "name": row.get("student_name"),
                    "campus": row.get("campus"), "batch": row.get("batch")},
        "trimester": latest.get("trimester"),
        "narrative": n,
        "generated_at": latest.get("created_at"),
        "note": ("Cached production report. create_report regenerates it and returns a "
                 "shareable link + per-subject table; student_marks / student_attendance "
                 "have the raw numbers."),
    }


def _availability_impl(svc, p: AvailabilityParams) -> dict:
    """What report data exists per (campus, batch) in the caller's grant: latest
    snapshot time, roster size, and the trimesters covered by that snapshot."""
    if p.campus and svc.campus_scope(p.campus) == []:
        return not_found("scope")
    self_batch = getattr(svc, "self_batch", None)
    if self_batch is not None and p.batch is not None and p.batch != self_batch:
        return not_found("scope")
    q = (svc.client.table("extraction_runs")
         .select("campus,batch,run_id,finished_at")
         .eq("status", "completed").eq("purpose", settings.report_purpose)
         .not_.is_("finished_at", "null")
         .order("finished_at", desc=True).limit(200))
    q = svc.apply_campus(q, requested=p.campus)
    if p.batch or self_batch:
        q = q.eq("batch", p.batch or self_batch)
    runs = q.execute().data or []
    latest, order = {}, []
    for r in runs:  # newest-first: first hit per scope is the live snapshot
        k = (r["campus"], r["batch"])
        if k not in latest:
            latest[k] = r
            order.append(k)
    scopes = []
    for k in order[:12]:
        r = latest[k]
        roster_q = (svc.client.table("students").select("student_id", count="exact")
                    .eq("campus", r["campus"]).eq("batch", r["batch"]))
        n = (svc.apply_student(roster_q).limit(1).execute()).count or 0
        from tools.common import courses_for
        tris = sorted({c["trimester"] for c in courses_for(svc, r["run_id"]).values()
                       if c["trimester"]}, key=str)
        scopes.append({"campus": r["campus"], "batch": r["batch"],
                       "latest_snapshot": r["finished_at"], "students": n,
                       "trimesters_with_data": tris})
    return {"scopes": scopes, "found": bool(scopes),
            "note": ("Reports are generated on demand with create_report and read "
                     "with get_student_report — no pre-built queue to wait on.")}


def register(mcp, get_service):
    # NOTE: this module is the SECONDARY report layer. Raw student data lives in tools/students.py
    # (list_students / get_student / student_marks / student_attendance) — prefer those. Here we
    # only expose the generated narrative report and the data-availability overview.

    @mcp.tool(title="Get Student Report", annotations=READONLY_ANNOTATIONS)
    async def get_student_report(params: ReportParams) -> dict:
        """
        WHAT: One student's report, read instantly from the cache. Serves the PRODUCTION
        one-page narrative (headline, personal pattern, attendance line, the four moves) when
        one exists; falls back to the legacy classic-format report otherwise.
        USE WHEN they say: 'open <id>'s report', 'how did <name> do', 'show me JJ24PG099',
        'what are this student's next steps'.
        DO NOT USE WHEN searching many students (use search_students), for cohort stats, or
        when they want a FRESH generation / shareable link (use create_report).
        RETURNS: student, trimester, narrative. found:false if out of scope — then offer
        create_report, which generates the report on the spot.
        """
        svc = await get_service()
        hit = _onepager_fetch(svc, params)
        if hit is not None:
            return hit
        # roster-first, data-second: an enrolled student in an ungraded batch is not a
        # missing student — say so, instead of "no report found for your access scope".
        from tools.common import find_student
        stu = find_student(svc, params.student_id)
        if stu is not None and svc.latest_run(stu["campus"], stu["batch"]) is None:
            return enrolled_no_data(stu)
        out = _report_impl(svc, params)
        if not out.get("found"):
            out["note"] = ("No cached report for this scope yet — create_report will "
                           "generate it now and return the full content plus a link.")
        return out

    @mcp.tool(title="Report Data Availability", annotations=READONLY_ANNOTATIONS)
    async def report_data_availability(params: AvailabilityParams) -> dict:
        """
        WHAT: What report data exists in your grant — per campus/batch: when the latest Moodle
        snapshot finished, how many students are on the roster, and which trimesters have data.
        USE WHEN they say: 'is the data in', 'which trimesters do we have', 'how fresh is the
        data', 'can I generate reports for noida yet', 'what batches are covered'.
        DO NOT USE WHEN they want one student's report (use get_student_report) or want to
        generate a shareable link (use create_report).
        RETURNS: scopes[{campus, batch, latest_snapshot, students, trimesters_with_data}].
        """
        return _availability_impl(await get_service(), params)
