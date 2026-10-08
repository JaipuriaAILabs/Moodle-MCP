# Student RBAC isolation — enforcement model + live test plan

**Guarantee:** when a student signs in, the MCP exposes only a reviewed **self-only tool allowlist**,
and every permitted query is hard-bounded to their own enrolment ID(s). A student can never obtain
another student's data via an explicit ID/name, name resolution, report target, report job, roster,
or aggregate endpoint. If the roster identity is unresolvable or ambiguous, access is denied.

This doc is the repeatable release check. Re-run it whenever auth, RBAC, or the tool surface changes.

## 1. How the boundary is enforced (so you know what you're verifying)

- **Class tool gate.** `security.STUDENT_SELF_TOOLS` is an explicit allowlist. Cohort, roster,
  leaderboard, watchlist, subject-wide, availability, access-request, and admin tools are denied
  before their handlers run. Any new tool defaults to denied for students.
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
- **Cohort endpoints are not callable.** Their underlying helpers remain owner-filtered as
  defense-in-depth, but the student tool gate denies them outright.
- **Report generation forces self.** `create_report` overwrites the `student_id` param with
  `self_student_id` before any resolution or agent call.
- **Report polling re-checks self.** `get_report_job` verifies the completed job's target
  `student_id ∈ self_student_ids` at the MCP layer (fail-closed) — a student cannot poll a
  same-campus peer's `request_id`.
- Per-student results are cached under keys that include `self_student_id`, so a student is never
  served a faculty cohort-cache entry.
- **DB defense-in-depth.** Every service query sends a server-generated `X-MCP-Scope` header.
  The stage-B RLS policies fail closed when it is absent/malformed and branch exclusively on student
  ID for student scopes (campus metadata can never widen a student to peers).

## 2. Per-tool coverage (audited; all self-bounded)

| group | tools | student sees |
|---|---|---|
| individual / report | `get_student`, `student_marks`, `student_attendance`, `student_trajectory`, `student_360`, `get_student_report`, `create_report`, `get_report_job` | own ID(s) only |
| identity | `whoami`, `resolve_identities` | own identity only; peer resolution fails |
| cohort / roster / subjects / risk / availability | all such tools | denied at the class tool gate |

## 3. Automated coverage (run on every change)

```
../moodle-agent/.venv/bin/python tests/test_student_isolation.py   # direct query + peer/report-job isolation
../moodle-agent/.venv/bin/python tests/test_rbac_modes.py          # 44 checks, incl. the per-student boundary block
../moodle-agent/.venv/bin/python tests/test_rbac_guardrails.py     # allowlist, scope header, RLS, forced report target
```
`test_student_isolation.py` asserts every query carries the `("student_id", SELF)` filter and that
roster/subject/availability/declining/get_student/student_marks/get_report_job all return self-only
(or deny). `test_rbac_modes.py` covers multi-enrolment scoping, fail-closed on missing batch/identity,
self-access OFF → deny, and dual-role (student+TA) educator precedence.

## 4. Prerequisite for a LIVE run: deploy self-access

The code default remains off as a fail-safe for new deployments. Production's `render.yaml` explicitly
sets `MCP_STUDENT_SELF_ACCESS=true`; it becomes live only after the reviewed change is merged and
deployed. The environment can be flipped back to `false` as the emergency student-access shutoff.

Apply the scope-contract migration and prove `mcp_scope_probe()` before applying fail-closed RLS; see
`docs/RBAC_ENFORCEMENT_EXECUTION_PLAN.md`.

## 5. LIVE test (run by a consenting student)

A real `@jaipuria.ac.in` **student** (on the roster) connects the Moodle MCP (Claude.ai `/mcp` or
jaipuria-os) with their own account, and needs **one classmate's real enrolment id** from their batch.

| # | prompt to the agent | ✅ pass | ❌ fail → stop & report |
|---|---|---|---|
| 1 | run `whoami` | `role: student`, own name/email, `campuses` = their campus (not `all`) | `cross_campus` / `campuses: all` |
| 2 | `student_marks` / `get_student` for *my* id | own data | — |
| 3 | **`student_marks` for `<a classmate's real id>`** | not-found; never the classmate's | classmate's marks appear |
| 4 | **`get_student` for *‘<a classmate's name>’*** | not-found | classmate's record |
| 5 | `list_students`, `marks_overview`, `cohort_pulse`, `watchlist` | access denied | any roster/cohort result |
| 6 | `create_report` while passing the peer ID | own student is the target | peer report is generated |
| 7 | poll a peer's report-job ID | access denied | peer job/result is returned |

**Steps 3–5 are the core isolation proof.** Any ❌ is a boundary breach — capture the raw result and
file it; it contradicts the automated suite and warrants immediate investigation.

## 6. Notes

- Faculty are unaffected by all of the above (`apply_student` is a no-op; they keep campus-scoped
  access per their `mcp_faculty` grant / RBAC mode).
- No cohort/availability metadata is intentionally exposed on the student tool surface.
