#!/usr/bin/env python3
"""Fetch one student's trimester data (plus same-class benchmarks) into the JSON that
build_report.py consumes.

Reads the same tables the MCP server reads (extraction_runs, courses, marks,
attendance_sessions) with the same credential model (SUPABASE_URL +
SUPABASE_SERVICE_ROLE_KEY, optionally SUPABASE_ANON_KEY as the gateway apikey when
the DB key is a custom-role JWT — see supabase_client._client in the server).

Benchmarks mirror the report pipeline's evidence packet:
  * class average per course_id = same section (course_id is section-specific);
  * subject %  = sum(obtained)/sum(max) over graded components with both scores;
  * class subject average = mean of per-student subject %;
  * attendance = present / (present + absent + late); excused/unmarked excluded.

Usage:
  python fetch_student.py --campus noida --batch 2024-26 --trimester 5 \
      --student JN24PG013 --out student.json
"""
import argparse
import json
import os
import re
import sys
from collections import defaultdict

from supabase import create_client

# subject keyword -> specialisation track (first match wins; order matters)
TRACK_RULES = [
    (r"financ|wealth|invest|bank|derivat|account|audit|tax", "Finance"),
    (r"forecast|analytic|operation|supply|logistic|quantitat|statistic", "Analytics & Operations"),
    (r"genai|artificial intelligence|\bai\b|digital|technology|python|machine learning", "GenAI"),
    (r"market|retail|brand|consumer|crm|customer|sales|category|advertis", "Marketing"),
    (r"hr\b|human resource|people|talent|organis|organiz", "HR & People"),
]
DEFAULT_TRACK = "General management"

STORE = {"ios": "https://apps.apple.com/in/app/try-rehearsal-ai/id6762619041",
         "android": "https://play.google.com/store/apps/details?id=ai.rehearsal.app"}

# The end-term (kind "ET" / "__total__") row is stored with a NULL max_score (see
# tools/students.py), so its percentage can only be computed against a fixed denominator.
# The programme convention is an end-term out of 40 (matches the kit's make_inputs.to_data);
# override per cohort with REPORT_ENDTERM_MAX if a batch/trimester uses a different maximum.
ENDTERM_MAX = float(os.environ.get("REPORT_ENDTERM_MAX", "40"))


def client():
    url = os.environ.get("SUPABASE_URL", "")
    db_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
    anon = os.environ.get("SUPABASE_ANON_KEY", "")
    if not url or not db_key:
        sys.exit("set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY")
    if anon:  # custom-role JWT rides as the bearer; anon key satisfies the gateway
        c = create_client(url, anon)
        c.postgrest.auth(db_key)
        return c
    return create_client(url, db_key)


def paged(factory, page=1000, cap=200000):
    out, off = [], 0
    while off < cap:
        batch = factory(off, page)
        if not batch:
            break
        out.extend(batch)
        if len(batch) < page:
            break
        off += page
    return out


def clean_subject(name):
    """'Business Forecasting_GR2' / 'Financial Modelling__GR1' -> 'Business Forecasting'."""
    return re.sub(r"_+GR\d+$", "", (name or "").strip()).strip("_ ")


def track_of(subject):
    low = subject.lower()
    for pat, track in TRACK_RULES:
        if re.search(pat, low):
            return track
    return DEFAULT_TRACK


def pct(o, m):
    return round(100.0 * o / m, 1) if m else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--campus", required=True)
    ap.add_argument("--batch", required=True)
    ap.add_argument("--trimester", required=True)
    ap.add_argument("--student", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    c = client()

    run = (c.table("extraction_runs").select("run_id,finished_at")
           .eq("campus", a.campus).eq("batch", a.batch)
           .eq("status", "completed").eq("purpose", "final")
           .not_.is_("finished_at", "null")
           .order("finished_at", desc=True).limit(1).execute()).data
    if not run:
        sys.exit(f"no completed final run for {a.campus}/{a.batch}")
    run_id, finished = run[0]["run_id"], run[0]["finished_at"]

    courses = (c.table("courses").select("course_id,course_code,course_name")
               .eq("run_id", run_id).limit(100000).execute()).data or []
    tri = {r["course_id"]: r for r in courses
           if (r.get("course_code") or "").split("_")[2:3] == [str(a.trimester)]}
    if not tri:
        sys.exit(f"no trimester-{a.trimester} courses in the latest run")
    cids = list(tri)

    stu = (c.table("students").select("student_id,student_name,section_group")
           .eq("run_id", run_id).eq("student_id", a.student).limit(1).execute()).data
    if not stu:
        sys.exit(f"student {a.student} not in run for {a.campus}/{a.batch}")
    name = " ".join(w.capitalize() for w in (stu[0].get("student_name") or a.student).split())

    marks = paged(lambda off, lim: (
        c.table("marks").select("student_id,course_id,kind,component_label,obtained_score,max_score")
        .eq("run_id", run_id).in_("course_id", cids).eq("graded", True)
        .range(off, off + lim - 1).execute()).data or [])
    att = paged(lambda off, lim: (
        c.table("attendance_sessions").select("student_id,course_id,status_norm")
        .eq("run_id", run_id).in_("course_id", cids)
        .range(off, off + lim - 1).execute()).data or [])
    # End-term rows are fetched separately: kind "ET" carries an obtained score but a NULL max,
    # so the "graded"/"max_score > 0" CE filter below would drop them. Scored against ENDTERM_MAX.
    et_rows = paged(lambda off, lim: (
        c.table("marks").select("student_id,course_id,kind,component_label,obtained_score")
        .eq("run_id", run_id).in_("course_id", cids).eq("kind", "ET")
        .range(off, off + lim - 1).execute()).data or [])

    # scored components only: both obtained and max present, max > 0
    scored = [m for m in marks
              if isinstance(m.get("obtained_score"), (int, float))
              and isinstance(m.get("max_score"), (int, float)) and m["max_score"] > 0]
    per_student_subject = defaultdict(lambda: [0.0, 0.0])   # (cid, sid) -> [obt, max]
    comp_class = defaultdict(list)                          # (cid, kind, label) -> [pct]
    my_comps = defaultdict(list)                            # cid -> [component rows]
    for m in scored:
        key = (m["course_id"], m["student_id"])
        per_student_subject[key][0] += m["obtained_score"]
        per_student_subject[key][1] += m["max_score"]
        p = pct(m["obtained_score"], m["max_score"])
        comp_class[(m["course_id"], m["kind"], m["component_label"])].append(p)
        if m["student_id"] == a.student:
            my_comps[m["course_id"]].append(m)

    subj_class = defaultdict(list)                          # cid -> [per-student pct]
    for (cid, _sid), (o, mx) in per_student_subject.items():
        if mx:
            subj_class[cid].append(100.0 * o / mx)

    att_counts = defaultdict(lambda: [0, 0])                # (cid, sid) -> [present, counted]
    for r in att:
        st = (r.get("status_norm") or "").lower()
        if st in ("present", "absent", "late"):
            k = (r["course_id"], r["student_id"])
            att_counts[k][1] += 1
            if st == "present":
                att_counts[k][0] += 1
    att_class = defaultdict(list)                           # cid -> [per-student att pct]
    for (cid, _sid), (p_, t) in att_counts.items():
        if t:
            att_class[cid].append(100.0 * p_ / t)

    et_obt = {}                                             # (cid, sid) -> end-term obtained
    for r in et_rows:
        o = r.get("obtained_score")
        if isinstance(o, (int, float)):
            et_obt[(r["course_id"], r["student_id"])] = o
    et_class = defaultdict(list)                            # cid -> [per-student end-term pct]
    for (cid, _sid), o in et_obt.items():
        et_class[cid].append(100.0 * o / ENDTERM_MAX)

    subjects = []
    for cid, meta in tri.items():
        mine = per_student_subject.get((cid, a.student))
        if not mine or not mine[1]:
            continue
        my_att = att_counts.get((cid, a.student))
        # Continuous components. kind is normalised to "CE" (the kit's rule: everything that is
        # not the end-term total is continuous) and obtained/max are carried as o/x, because the
        # v8 renderer recomputes the continuous-vs-end-term split from o/x (build_report_v5.so_what)
        # and the assignment-wise marks PDF prints them (make_inputs.marks_pdf).
        comps = [{"component": m["component_label"], "kind": "CE",
                  "you_pct": pct(m["obtained_score"], m["max_score"]),
                  "class_pct": round(sum(comp_class[(cid, m["kind"], m["component_label"])]) /
                                     len(comp_class[(cid, m["kind"], m["component_label"])]), 1),
                  "o": m["obtained_score"], "x": m["max_score"]}
                 for m in sorted(my_comps.get(cid, []), key=lambda x: x["component_label"])]
        my_et = et_obt.get((cid, a.student))                # end-term, scored out of ENDTERM_MAX
        if my_et is not None:
            comps.append({"component": "End-term exam", "kind": "ET",
                          "you_pct": round(100.0 * my_et / ENDTERM_MAX, 1),
                          "class_pct": round(sum(et_class[cid]) / len(et_class[cid]), 1) if et_class[cid] else None,
                          "o": my_et, "x": ENDTERM_MAX})
        subject = clean_subject(meta["course_name"])
        subjects.append({
            "subject": subject, "track": track_of(subject),
            "you_pct": pct(*mine),
            "class_pct": round(sum(subj_class[cid]) / len(subj_class[cid]), 1) if subj_class[cid] else None,
            "att_you": round(100.0 * my_att[0] / my_att[1], 1) if my_att and my_att[1] else None,
            "att_class": round(sum(att_class[cid]) / len(att_class[cid]), 1) if att_class[cid] else None,
            "components": comps})
    subjects.sort(key=lambda s: s["subject"])

    tracks = []
    for s in subjects:  # preserve a stable, first-seen track order
        if s["track"] not in tracks:
            tracks.append(s["track"])

    from datetime import datetime
    data_date = datetime.fromisoformat(finished.replace("Z", "+00:00")).strftime("%-d %B %Y")
    out = {"student": {"id": a.student, "name": name, "campus": a.campus.capitalize(),
                       "batch": a.batch},
           "trimester": str(a.trimester), "data_date": data_date,
           "benchmark": "same-class average (your section)",
           "tracks": tracks, "store": STORE, "subjects": subjects}
    json.dump(out, open(a.out, "w"), indent=1)
    print(f"{a.out}: {len(subjects)} subjects, tracks={tracks}")


if __name__ == "__main__":
    main()
