"""Shared query helpers — read-only, campus-scoped. Data-first: the primary source is the raw
Moodle tables (students, courses, enrolments, marks, attendance_sessions); the generated report
is a secondary layer. (The report-QA/accuracy layer is intentionally NOT exposed here.)"""

from fastmcp.exceptions import ToolError

CATALOG = "student_reports"
MARKS = "marks"
ATTENDANCE = "attendance_sessions"


# --- course-code helpers ------------------------------------------------------
def course_trimester(code: str):
    """Trimester digit from a course code 'subjectnum_batch_TRIMESTER_section' (e.g. 30503_26_3_C)."""
    parts = (code or "").split("_")
    return parts[2] if len(parts) >= 3 and parts[2].isdigit() else None


def course_section(code: str):
    """Section token from a course code. Robust across both layouts:
      old 4-field 'subjnum_batch_trim_SECTION'        e.g. 30503_26_3_C     -> 'C'
      new 5-field 'subjnum_batch_trim_SECTION_ABBR'   e.g. 20201_27_1_A_AFB -> 'A'
    Section is always field 4 ([3]); the older format just has it as the last field.
    (Using rsplit('_')[-1] grabbed the subject abbreviation on the new format.)"""
    parts = (code or "").split("_")
    if len(parts) >= 4:
        return parts[3]
    return parts[-1] if parts and parts[0] else None


def clean_subject(name: str) -> str:
    """'Business Research Methods - C' -> 'Business Research Methods' (strip the section suffix)."""
    n = (name or "").strip()
    return n.rsplit(" - ", 1)[0].strip() if " - " in n else n


def pct(obtained, maximum):
    try:
        return round(100 * float(obtained) / float(maximum), 1) if maximum else None
    except (TypeError, ValueError, ZeroDivisionError):
        return None


# --- run + course resolution --------------------------------------------------
def courses_for(svc, run_id, trimester=None) -> dict:
    """course_id -> {code, name, subject, trimester} for a run, optionally one trimester."""
    rows = (svc.client.table("courses").select("course_id,course_code,course_name")
            .eq("run_id", run_id).limit(100000).execute()).data or []
    out = {}
    for c in rows:
        t = course_trimester(c["course_code"])
        if trimester and t != str(trimester):
            continue
        out[c["course_id"]] = {"code": c["course_code"], "name": c["course_name"],
                               "subject": clean_subject(c["course_name"]), "trimester": t}
    return out


def find_student(svc, student_id):
    """Locate a student in the roster within the caller's campus scope, by enrolment id OR name —
    so callers can pass 'JN25MM002' or 'Aashna Gupta' interchangeably. Returns the row
    (campus, batch, student_name) or None. Uses the students table, so it covers EVERY ingested
    student — not only those with a generated report. Always campus-scoped: a name never resolves
    a student outside the caller's grant. When a name/fragment matches several students it raises a
    ToolError listing the candidates (with ids) so the caller can disambiguate — never guesses."""
    query = (student_id or "").strip()
    if not query:
        return None

    def _roster():
        q = svc.client.table("students").select("student_id,student_name,campus,batch,section_group")
        return svc.apply_student(svc.apply_campus(q, requested=None))  # student -> self only

    # 1) Exact enrolment id — fast and unambiguous.
    rows = (_roster().eq("student_id", query).limit(1).execute()).data or []
    if rows:
        return rows[0]

    # 2) Name / id fragment, case-insensitive, still campus-scoped. Sanitised so it can't break
    #    the PostgREST or-filter (keep letters/digits/space/hyphen only; `*` = ilike wildcard).
    #    Apostrophes and dots are turned into token breaks rather than dropped, so "D'Souza"
    #    and "J. Smith" become "*D*Souza*" / "*J*Smith*" and still ilike-match the stored name
    #    (dropping them would make "D'Souza" -> "DSouza", which never matches).
    frag = "".join(ch if (ch.isalnum() or ch in " -") else " "
                   for ch in query.replace("'", " ").replace(".", " ")).strip()
    if not frag:
        return None
    # Join tokens with the ilike wildcard so "Aashna Gupta" matches "AASHNA  GUPTA" — the roster
    # names carry inconsistent internal spacing, which a literal-space pattern would miss.
    pat = "*" + "*".join(frag.split()) + "*"
    matches = (_roster().or_(f"student_name.ilike.{pat},student_id.ilike.{pat}")
               .limit(25).execute()).data or []
    uniq = {}
    for r in matches:                                   # students table is per-run; dedupe by id
        uniq.setdefault(r["student_id"], r)
    cands = list(uniq.values())
    if len(cands) == 1:
        return cands[0]
    if not cands:
        return None
    # A full-name match wins even if the fragment also appears in other names (whitespace-normalised
    # so the double-spaced roster names compare equal to a single-spaced query).
    norm = " ".join(frag.split()).lower()
    exact = [r for r in cands if " ".join((r.get("student_name") or "").split()).lower() == norm]
    if len(exact) == 1:
        return exact[0]
    listing = "; ".join(f"{r.get('student_name')} ({r['student_id']}, {r['campus']}/{r['batch']})"
                        for r in sorted(cands, key=lambda r: r.get("student_name") or "")[:10])
    raise ToolError(f"Multiple students match '{query}'. Re-run with the exact enrolment id — "
                    f"candidates: {listing}")


def roster_member(svc, student_id, campus, batch):
    """The roster row for this exact (student_id, campus, batch) within the caller's scope,
    or None. Used where campus/batch are given explicitly (e.g. create_report) to tell
    'enrolled but ungraded' apart from 'not enrolled here'."""
    if svc.campus_scope(campus) == []:
        return None
    q = (svc.client.table("students")
         .select("student_id,student_name,campus,batch,section_group")
         .eq("student_id", student_id).eq("campus", campus).eq("batch", batch))
    rows = (svc.apply_student(q).limit(1).execute()).data or []  # student -> self only
    return rows[0] if rows else None


def graded_scopes(svc, campus=None, batch=None):
    """(campus, batch, run_id) for every batch with a completed final run, within the
    caller's grant, newest run first. Optionally narrowed to one campus and/or batch.
    This is the set a report can actually be built from (roster-only batches excluded)."""
    from config import settings
    q = (svc.client.table("extraction_runs")
         .select("campus,batch,run_id,finished_at")
         .eq("status", "completed").eq("purpose", settings.report_purpose)
         .not_.is_("finished_at", "null")
         .order("finished_at", desc=True).limit(200))
    q = svc.apply_campus(q, requested=campus)
    if batch:
        q = q.eq("batch", batch)
    rows = q.execute().data or []
    seen, out = set(), []
    for r in rows:  # newest-first: first per (campus,batch) is the live snapshot
        k = (r["campus"], r["batch"])
        if k not in seen:
            seen.add(k)
            out.append((r["campus"], r["batch"], r["run_id"]))
    return out


def random_gradeable_students(svc, run_id, n=4):
    """Up to n random student_ids that have GRADED marks in this run (graded=true) — the
    ones most likely to yield a report (mere roster/marks presence isn't enough; an
    in-progress trimester can be unscored). Shuffled so repeat calls vary."""
    import random
    q = (svc.client.table(MARKS).select("student_id")
         .eq("run_id", run_id).eq("graded", True))
    rows = (svc.apply_student(q).limit(8000).execute()).data or []  # student -> self only
    ids = list({r["student_id"] for r in rows if r.get("student_id")})
    random.shuffle(ids)
    return ids[:max(1, n)]


def cached_report_students(svc, run_id, limit=5):
    """student_ids that already have a cached one-page narrative for this run — generating
    for them is an instant cache hit, so they're the reliable fallback when a randomly
    sampled student's latest trimester turns out to be unscored."""
    try:
        q = svc.client.table("onepager_narratives").select("student_id").eq("run_id", run_id)
        rows = (svc.apply_student(q).limit(limit).execute()).data or []  # student -> self only
    except Exception:  # noqa: BLE001 — cache table optional; never block selection
        return []
    return list({r["student_id"] for r in rows if r.get("student_id")})


# --- paged fetch past the 1000-row PostgREST cap ------------------------------
def paged(query_factory, page: int = 1000, cap: int = 100000):
    out, offset = [], 0
    while offset < cap:
        batch = query_factory(offset, page)
        if not batch:
            break
        out.extend(batch)
        if len(batch) < page:
            break
        offset += page
    return out


def marks_for(svc, run_id, *, student_id=None, course_ids=None):
    def factory(off, lim):
        q = (svc.client.table(MARKS)
             .select("student_id,course_id,component_label,kind,graded,obtained_score,max_score")
             .eq("run_id", run_id))
        if student_id:
            q = q.eq("student_id", student_id)
        if course_ids is not None:
            q = q.in_("course_id", course_ids)
        return (svc.apply_student(q).range(off, off + lim - 1).execute()).data or []
    return paged(factory)


def attendance_for(svc, run_id, *, student_id=None, course_ids=None):
    def factory(off, lim):
        q = (svc.client.table(ATTENDANCE).select("student_id,course_id,status_norm")
             .eq("run_id", run_id))
        if student_id:
            q = q.eq("student_id", student_id)
        if course_ids is not None:
            q = q.in_("course_id", course_ids)
        return (svc.apply_student(q).range(off, off + lim - 1).execute()).data or []
    return paged(factory)


def attendance_pct(rows) -> tuple:
    """(present, total, pct) from attendance_sessions rows (status_norm 'present'/'absent'/...)."""
    total = len(rows)
    present = sum(1 for r in rows if (r.get("status_norm") or "").lower() == "present")
    return present, total, (round(100 * present / total, 1) if total else None)


# --- generated report (secondary) --------------------------------------------
def report_rows(svc, *, campus=None, batch=None, trimester=None, cols="*",
                status="ready", limit=100000):
    q = svc.client.table(CATALOG).select(cols)
    q = svc.apply_student(svc.apply_campus(q, requested=campus))  # student -> self only
    if batch:
        q = q.eq("batch", batch)
    if trimester:
        q = q.eq("trimester", str(trimester))
    if status:
        q = q.eq("status", status)
    return (q.limit(limit).execute()).data or []


def one_report(svc, student_id, *, campus=None, batch=None, trimester=None, cols="*"):
    q = svc.client.table(CATALOG).select(cols).eq("student_id", student_id)
    q = svc.apply_student(svc.apply_campus(q, requested=campus))  # student -> self only
    if batch:
        q = q.eq("batch", batch)
    if trimester:
        q = q.eq("trimester", str(trimester))
    rows = (q.order("generated_at", desc=True).limit(1).execute()).data or []
    return rows[0] if rows else None


def student_label(row: dict) -> dict:
    return {"student_id": row.get("student_id"),
            "name": (row.get("student_name") or row.get("full_name")
                     or row.get("first_name") or row.get("student_id"))}


# --- cohort rollup over raw marks + attendance (cached per run) ---------------
from cache import TTLCache  # noqa: E402
from collections import defaultdict  # noqa: E402

_rollup_cache = TTLCache(maxsize=8, ttl=300)
_marks_cache = TTLCache(maxsize=8, ttl=300)


def scope_marks(svc, run_id, course_ids):
    """All raw marks rows for a set of courses, cached per (run, course-set, self-scope).
    The self_student_id is part of the key so a student's self-bounded result is NEVER served
    from (or to) a faculty cohort cache entry."""
    key = (run_id, tuple(sorted(course_ids)), svc.self_student_id)
    hit = _marks_cache.get(key)
    if hit is not None:
        return hit
    rows = marks_for(svc, run_id, course_ids=list(course_ids))
    _marks_cache.set(key, rows)
    return rows


def cohort_rollup(svc, run_id, courses) -> dict:
    """Per-student rollup from the raw tables for a scope: overall graded-mark %, recorded zeros,
    overall attendance %, and subjects below 75% attendance. Cached per (run, trimester-set)."""
    key = (run_id, tuple(sorted(courses)), svc.self_student_id)  # self-scope segregates the cache
    hit = _rollup_cache.get(key)
    if hit is not None:
        return hit
    cids = list(courses)
    names_q = svc.apply_student(svc.client.table("students")  # student -> self only
                               .select("student_id,student_name").eq("run_id", run_id))
    names = {r["student_id"]: r.get("student_name")
             for r in (names_q.limit(100000).execute()).data or []}
    marks = marks_for(svc, run_id, course_ids=cids)
    att = attendance_for(svc, run_id, course_ids=cids)
    agg = defaultdict(lambda: {"obt": 0.0, "max": 0.0, "zeros": [], "att": defaultdict(list)})
    for m in marks:
        a = agg[m["student_id"]]
        if m.get("graded") and isinstance(m.get("obtained_score"), (int, float)) \
                and isinstance(m.get("max_score"), (int, float)):
            a["obt"] += m["obtained_score"]; a["max"] += m["max_score"]
            if m["obtained_score"] == 0:
                a["zeros"].append(m.get("component_label"))
    for r in att:
        agg[r["student_id"]]["att"][r["course_id"]].append(r.get("status_norm"))
    out = {}
    for sid, a in agg.items():
        att_all = [s for rows in a["att"].values() for s in rows]
        pres = sum(1 for s in att_all if (s or "").lower() == "present")
        low_subj = []
        for cid, rows in a["att"].items():
            p, t, ap = attendance_pct([{"status_norm": s} for s in rows])
            if ap is not None and ap < 75:
                low_subj.append((courses.get(cid, {}).get("subject"), ap))
        out[sid] = {"name": names.get(sid, sid), "mark_pct": pct(a["obt"], a["max"]),
                    "zeros": [z for z in a["zeros"] if z][:8],
                    "attendance_pct": (round(100 * pres / len(att_all), 1) if att_all else None),
                    "low_attendance_subjects": low_subj}
    _rollup_cache.set(key, out)
    return out
