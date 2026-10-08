-- Request-scoped RBAC enforcement (stage B: FAIL-CLOSED data policies).
--
-- PRECONDITIONS — do not apply until all are true:
--   [ ] 2026-10-08_mcp_scope_contract.sql is applied.
--   [ ] The MCP code sending X-MCP-Scope is deployed.
--   [ ] mcp_scope_probe() returned the expected scope for a student, one campus
--       dean, and one cross-campus account through the deployed MCP data client.
--
-- Missing/malformed scope denies every reporting_readonly data row. The educator
-- registry and identity bootstrap RPC remain available so sign-in can resolve a
-- principal before the scoped data client is constructed.

begin;

-- Remove the historical read-all policies. PostgreSQL ORs permissive policies;
-- leaving even one USING(true) policy would bypass every scoped policy below.
drop policy if exists reporting_readonly_select on public.students;
drop policy if exists reporting_readonly_select on public.courses;
drop policy if exists reporting_readonly_select on public.enrolments;
drop policy if exists reporting_readonly_select on public.marks;
drop policy if exists reporting_readonly_select on public.attendance_sessions;
drop policy if exists reporting_readonly_select on public.student_reports;
drop policy if exists reporting_readonly_select on public.extraction_runs;

drop policy if exists mcp_students_scope on public.students;
drop policy if exists mcp_courses_scope on public.courses;
drop policy if exists mcp_enrolments_scope on public.enrolments;
drop policy if exists mcp_marks_scope on public.marks;
drop policy if exists mcp_attendance_scope on public.attendance_sessions;
drop policy if exists mcp_student_reports_scope on public.student_reports;
drop policy if exists mcp_extraction_runs_scope on public.extraction_runs;

alter table public.students enable row level security;
alter table public.courses enable row level security;
alter table public.enrolments enable row level security;
alter table public.marks enable row level security;
alter table public.attendance_sessions enable row level security;
alter table public.student_reports enable row level security;
alter table public.extraction_runs enable row level security;

create policy mcp_students_scope on public.students
  for select to reporting_readonly
  using (public.mcp_scope_allows(student_id::text, campus::text));

create policy mcp_courses_scope on public.courses
  for select to reporting_readonly
  using (public.mcp_scope_allows_course(run_id::text, course_id::text));

create policy mcp_enrolments_scope on public.enrolments
  for select to reporting_readonly
  using (public.mcp_scope_allows_student_row(run_id::text, student_id::text));

create policy mcp_marks_scope on public.marks
  for select to reporting_readonly
  using (public.mcp_scope_allows_student_row(run_id::text, student_id::text));

create policy mcp_attendance_scope on public.attendance_sessions
  for select to reporting_readonly
  using (public.mcp_scope_allows_student_row(run_id::text, student_id::text));

create policy mcp_student_reports_scope on public.student_reports
  for select to reporting_readonly
  using (public.mcp_scope_allows(student_id::text, campus::text));

create policy mcp_extraction_runs_scope on public.extraction_runs
  for select to reporting_readonly
  using (public.mcp_scope_allows_run(campus::text, batch::text));

-- Optional one-page cache: exact student/run scoping and no rendered HTML grant.
do $$
begin
  if to_regclass('public.onepager_narratives') is not null then
    execute 'drop policy if exists reporting_readonly_select on public.onepager_narratives';
    execute 'drop policy if exists mcp_onepager_scope on public.onepager_narratives';
    execute 'alter table public.onepager_narratives enable row level security';
    execute 'create policy mcp_onepager_scope on public.onepager_narratives '
            'for select to reporting_readonly '
            'using (public.mcp_scope_allows_student_row(run_id::text, student_id::text))';
  end if;
end $$;

-- These tables are not used by the MCP runtime. Remove stale read grants instead
-- of carrying unscoped surfaces forward.
revoke select on public.report_accuracy, public.student_report_jobs
  from reporting_readonly;

commit;

-- Rollback (privileged SQL session only): re-create the historical
-- reporting_readonly_select USING(true) policies on the affected tables. Prefer
-- rolling back the application first; do not leave a mixed scoped/unscoped state.
