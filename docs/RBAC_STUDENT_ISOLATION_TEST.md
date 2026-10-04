# Student RBAC isolation — enforcement model + live test plan

**Guarantee:** when a student signs in, every MCP tool is hard-bounded to **their own data**. A student
can never obtain another student's data via (a) an explicit enrolment id / name parameter, (b) a
name→row resolution, or (c) a cohort/aggregate computed over peers. If the student's roster identity
is unresolvable, access is **denied** (fail-closed), never partially scoped.

This doc is the repeatable release check. Re-run it whenever auth, RBAC, or the tool surface changes.

## 1. How the boundary is enforced (so you know what you're verifying)

- **Principal → self state.** A student principal (`role=student`, built in `security.py`
  `_student_principal`, requires complete campus+batch+id or it fail-closes) yields a `MoodleService`
  carrying `self_student_ids` (all their enrolment ids), `self_batches`, and primaries
  (`supabase_client.py` `MoodleService.__init__`).
- **Central query bound.** `apply_student()` (`supabase_client.py`) adds `.eq/.in_(student_id, own)`
  to every data query; it is a no-op for faculty. `latest_run`/`graded_scopes` additionally refuse any
  run/batch outside `self_batches`.
- **Name resolution can't escape.** `find_student()` (`tools/common.py`) wraps **both** the exact-id
  lookup and the name/fragment search in `apply_student`, so a classmate's name or id resolves to
  **no row** → uniform not-found. It never lists or returns another student.
- **Cohort aggregates collapse to n=1.** `cohort_rollup`/`scope_marks`/`marks_for`/`attendance_for`
  all run through `apply_student`, so every cohort/analytics tool computes over the student alone.
- **Report generation forces self.** `create_report` overwrites the `student_id` param with
  `self_student_id` before any resolution or agent call.
- **Report polling re-checks self.** `get_report_job` verifies the completed job's target
  `student_id ∈ self_student_ids` at the MCP layer (fail-closed) — a student cannot poll a
  same-campus peer's `request_id`.
- Per-student results are cached under keys that include `self_student_id`, so a student is never
  served a faculty cohort-cache entry.

## 2. Per-tool coverage (audited; all self-bounded)

| group | tools | student sees |
|---|---|---|
| identity | `whoami` | only their own principal |
| individual | `get_student`, `student_marks`, `student_attendance`, `student_trajectory`, `get_student_report` | only themselves; classmate id/name → not-found |
| 360 / watch | `student_360`, `cohort_pulse`, `watchlist`, `at_risk_students`, `attendance_watch`, `zero_alerts`, `declining_students` | self only (n≤1) |
| cohort / analytics | `marks_overview`, `attendance_overview`, `top_performers`, `cohort_compare`, `section_compare`, `campus_performance_report` | aggregates over themselves only (n=1) |
| subjects | `list_subjects`, `subject_performance`, `subject_difficulty`, `assessment_breakdown` | self only |
| roster / availability | `list_students`, `report_data_availability` | count = 1 (themselves); own-batch curriculum metadata only |
| reports | `create_report` (target forced to self), `get_report_job` (self-checked) | only their own report |
| access / identity | `request_access` (files own email), `list_access_requests` (admin-only → denied), `resolve_identities` (classmate → `resolved:false`) | self only / denied |

## 3. Automated coverage (run on every change)

```
../moodle-agent/.venv/bin/python tests/test_student_isolation.py   # 7 checks, incl. classmate-by-id/name + report-job self-check
../moodle-agent/.venv/bin/python tests/test_rbac_modes.py          # 44 checks, incl. the per-student boundary block
```
`test_student_isolation.py` asserts every query carries the `("student_id", SELF)` filter and that
roster/subject/availability/declining/get_student/student_marks/get_report_job all return self-only
(or deny). `test_rbac_modes.py` covers multi-enrolment scoping, fail-closed on missing batch/identity,
self-access OFF → deny, and dual-role (student+TA) educator precedence.

## 4. Prerequisite for a LIVE run: enable self-access

Student self-access is flag-gated and **off by default** — with it off, a student is *denied outright*
(not self-scoped). To run the live test you must enable it (this opens self-access to **all** students
org-wide; there is no per-user flag):

1. Render → service `srv-da61ppjncjis73aer1hg` → **Environment**
   (`https://dashboard.render.com/web/srv-da61ppjncjis73aer1hg/env`).
2. Set **`MCP_STUDENT_SELF_ACCESS=true`** → Save (triggers a redeploy; sessions drop → testers re-auth).
3. Reversible: set back to `false` to disable.

## 5. LIVE test (run by a consenting student)

A real `@jaipuria.ac.in` **student** (on the roster) connects the Moodle MCP (Claude.ai `/mcp` or
jaipuria-os) with their own account, and needs **one classmate's real enrolment id** from their batch.

| # | prompt to the agent | ✅ pass | ❌ fail → stop & report |
|---|---|---|---|
| 1 | run `whoami` | `role: student`, own name/email, `campuses` = their campus (not `all`) | `cross_campus` / `campuses: all` |
| 2 | `list_students` for my campus+batch | count = 1 (themselves) | any other student |
| 3 | `student_marks` / `get_student` for *my* id | own data | — |
| 4 | **`student_marks` for `<a classmate's real id>`** | own data or **not-found** — never the classmate's | classmate's marks appear |
| 5 | **`get_student` for *‘<a classmate's name>’*** | own record or not-found | classmate's record |
| 6 | `report_data_availability` | 1 student, their scope only | counts beyond themselves |
| 7 | `marks_overview` / `cohort_pulse` for my batch | self-scoped (n=1) or denied | cohort-wide numbers |

**Steps 4–5 are the core isolation proof.** Any ❌ is a boundary breach — capture the raw result and
file it; it contradicts the automated suite and warrants immediate investigation.

## 6. Notes

- Faculty are unaffected by all of the above (`apply_student` is a no-op; they keep campus-scoped
  access per their `mcp_faculty` grant / RBAC mode).
- Residual (accepted) exposure: `report_data_availability` reveals the student's own-batch trimester
  list — curriculum metadata shared across the batch, not personal peer data.
