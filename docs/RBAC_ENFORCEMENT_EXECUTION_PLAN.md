# Moodle MCP RBAC enforcement — execution plan

**Security objective:** a verified student can read or generate data only for the
student ID(s) mapped to their own Google email. The eight cross-campus accounts can
query all four campuses. Each dean can query only their assigned campus. Everyone
else is denied.

## Access matrix

| principal class | assignment | effective data scope | tool surface |
| --- | --- | --- | --- |
| Student | exact verified Google email match in the Moodle roster | own enrolment ID(s) only | `whoami`, own student/marks/attendance/trajectory/360/report, own report job, identity resolution |
| Cross-campus | the 8 approved `cross_campus` rows in `mcp_faculty` | Noida, Lucknow, Jaipur, Indore | all read/report tools; no access-registry administration |
| Noida dean | `rahul.singh@jaipuria.ac.in` | Noida only | campus read/report tools |
| Lucknow dean | `sushma.vishnani@jaipuria.ac.in` | Lucknow only | campus read/report tools |
| Jaipur dean | `daneshwar.sharma@jaipuria.ac.in` | Jaipur only | campus read/report tools |
| Indore dean | `deepankar.chakrabarti@jaipuria.ac.in` | Indore only | campus read/report tools |
| Pending/unlisted | no active whitelist or roster identity | none | `whoami` and `request_access` only when self-service is enabled; production self-service is off |

`cross_campus` is intentionally not `admin`: all-campus data access does not grant
access-request administration or mutation rights.

## Enforcement flow

```text
Google verified email
        |
        v
educator registry first -> student roster second -> otherwise deny
        |
        v
class tool allowlist (student/pending fail closed)
        |
        v
MoodleService app filters (student IDs + campus/batch)
        |
        v
X-MCP-Scope on every PostgREST query
        |
        v
Supabase RLS (student ID OR staff campus, never both)
        |
        v
configured response-redaction boundary + append-only audit record
```

## Implemented guardrails

1. **Authoritative whitelist:** `MCP_RBAC_MODE=enforce`, unlisted default `none`,
   self-service off, and stale `MCP_FACULTY` environment overrides ignored.
2. **Student classification fails closed:** incomplete identities, roster lookup
   errors without a last-known-good identity, or one email mapping to different
   student names produce no session.
3. **Explicit student tool allowlist:** cohort, roster, leaderboard, risk-list,
   subject-wide, availability, access-request, and admin tools are denied before
   their handlers execute. New tools are denied until reviewed.
4. **Central owner filtering:** all student-bearing queries retain the application
   `student_id IN own_ids` boundary; cache keys are segregated from staff data.
5. **Report boundary:** `create_report` overwrites any requested target with the
   authenticated student's primary ID. `get_report_job` rechecks the completed
   target against all owned IDs.
6. **Database scope contract:** every service query carries a compact internal
   `X-MCP-Scope` header with mutually exclusive `student`, `staff`, or `deny` kind.
   It contains no email or name.
7. **Fail-closed RLS:** stage-B policies remove all historical `USING(true)`
   reporting policies and constrain students, marks, attendance, enrolments,
   reports, runs, courses, and cached narratives. A student campus claim can never
   authorize peers because student decisions branch only on `student_ids`.
8. **Least-privilege runtime:** `reporting_readonly` remains SELECT-only; audit and
   OAuth storage use separate credentials.

## Deployment sequence

### Gate 1 — code and tests

- Run every `tests/test_*.py` file and byte compilation.
- Confirm the static tool inventory equals the student allowlist decision matrix.
- Confirm the 12-account whitelist regression test passes.
- Confirm CI `test` and `dependency-audit` are green.
- Obtain independent PR approval.

### Gate 2 — scope contract canary

1. Confirm `SUPABASE_DATA_KEY` is a JWT for `reporting_readonly`, not `service_role`.
   A service-role credential bypasses RLS and fails this gate.
2. Apply `sql/2026-10-08_mcp_scope_contract.sql`.
3. Deploy the MCP with `MCP_STUDENT_SELF_ACCESS=true`.
4. Through the deployed scoped data client, call the non-tool RPC
   `mcp_scope_probe()` for:
   - a test student: `kind=student`, exact own IDs/campuses/batches, no `all`;
   - one dean: `kind=staff`, exactly one campus;
   - one approved cross-campus account: `kind=staff`, `all=true`.
5. If the probe is missing or different, stop. Do not apply stage B.

### Gate 3 — database enforcement

1. Apply `sql/2026-10-08_mcp_scope_rls_enforce.sql`.
2. Verify a query with no scope header returns zero protected rows.
3. Verify the student scope returns only owned IDs, including when explicitly
   filtering for a known peer ID.
4. Verify every dean receives rows only from their assigned campus.
5. Verify a cross-campus account can query all four campuses.

### Gate 4 — live user smoke

Use a consenting student and a known peer in the same batch:

| test | expected result |
| --- | --- |
| `whoami` | `role=student`; own campus; never `all` |
| own `get_student`, marks, attendance | own record only |
| peer ID and peer name in individual tools | uniform not-found; no peer identifier or name in output |
| roster/cohort/watchlist/subject-wide tools | access denied |
| `create_report` with a peer ID | own report target only |
| another student's report job ID | access denied |

Repeat with each dean against one own-campus and one other-campus target, then with
one cross-campus account against all four campuses.

## Monitoring and rollback

- Alert on `unauthorized` tool outcomes, RLS/PostgREST failures, and audit write
  failures. Production keeps `MCP_REQUIRE_AUDIT=true`.
- Roll back application code before database policy rollback to avoid a mixed
  scoped/unscoped state.
- Emergency student shutoff: set `MCP_STUDENT_SELF_ACCESS=false`; educators remain
  governed by the whitelist.
- Stage-B rollback requires a privileged SQL session. Re-create broad policies only
  as a time-bounded incident action, document the window, and reapply scoped RLS.

## Verification record — 2026-10-08

- All 24 repository test programs passed, including the 89-case hardening suite,
  student peer/report-job isolation, RBAC modes, and the new scope/RLS guardrails.
- Python byte-compilation and `git diff --check` passed.
- The live registry has exactly the requested 12 active grants and 9 inactive
  historical rows. No approved data user has the `admin` role.
- A PII-free scan covered 3,145 live roster rows / 2,799 distinct emails: zero
  incomplete identities and zero emails mapping to materially different names.
- Read-only live application probes passed: student own-visible/peer-denied,
  Noida dean own-campus-visible/Jaipur-denied, and cross-campus Jaipur-visible.
- Gate 2 is not yet complete: `mcp_scope_probe()` is absent in live Supabase, and
  the stored Supabase management token returns HTTP 401. Stage A/B therefore have
  not been applied; a valid migration credential is required.
