-- SUPERSEDED — DO NOT APPLY.
--
-- This draft deliberately failed open when request scope was absent and allowed student-id OR campus
-- membership. That is not safe for production student isolation. Use these reviewed two-stage migrations:
--   1. 2026-10-08_mcp_scope_contract.sql
--   2. 2026-10-08_mcp_scope_rls_enforce.sql
-- and follow docs/RBAC_ENFORCEMENT_EXECUTION_PLAN.md.
--
-- Historical draft retained only for audit context below.
--
-- AIA-1386 (D1) — per-student + per-campus RLS on the Moodle student data, as DEFENSE-IN-DEPTH
-- behind the app-layer boundary (apply_student / apply_campus). Goal: even if application filtering
-- regresses, the shared read-only DB credential cannot return another student's / another campus's rows.
--
-- ┌────────────────────────────────────────────────────────────────────────────────────────────┐
-- │ DRAFT — DO NOT APPLY TO PROD BLIND. Test on a Supabase BRANCH first (see "Rollout" below).    │
-- │ This changes the CONNECTION MODEL: RLS only becomes a real boundary once the MCP sends a       │
-- │ PER-REQUEST JWT carrying the caller's scope. Until then these policies are intentionally a      │
-- │ NO-OP (fail-OPEN when the scope claim is absent) so the current single shared reporting_readonly│
-- │ key keeps reading exactly as today. Applying it is safe (no behaviour change); it only tightens │
-- │ after the MCP starts stamping the `mcp_scope` claim.                                            │
-- └────────────────────────────────────────────────────────────────────────────────────────────┘
--
-- Project: "Moodle Data" (sadbfvfcmmxgtatfjfmc). Role that reads: reporting_readonly (SELECT-only JWT;
-- RLS applies to it — service_role bypasses RLS and is unaffected). PostgREST forwards the request JWT,
-- so policies read its claims via current_setting('request.jwt.claims').
--
-- Contract for the per-request claim (the connection-model change the MCP must make before this bites):
--   mcp_scope = {
--     "all":          <bool>,          -- director/all-campus → unrestricted read
--     "student_ids":  ["JN25..", ...], -- student self-scope: only these enrolment ids
--     "campuses":     ["noida", ...]   -- faculty: only students in these campuses
--   }
-- Absent mcp_scope  => fail-open (current behaviour; nothing is restricted).

begin;

-- ── scope reader + decision helper ───────────────────────────────────────────────────────────
-- STABLE, reads only the request JWT claim. Returns NULL when no scope claim is present.
create or replace function public.mcp_request_scope()
returns jsonb language sql stable as $$
  select nullif(current_setting('request.jwt.claims', true), '')::jsonb -> 'mcp_scope';
$$;

-- The single decision used by every policy below. Fail-OPEN when scope is absent (backward compatible).
create or replace function public.mcp_scope_allows(p_student_id text, p_campus text)
returns boolean language sql stable as $$
  with s as (select public.mcp_request_scope() as j)
  select
    case
      when (select j from s) is null then true                       -- no claim → unrestricted (today)
      when coalesce(((select j from s) ->> 'all')::boolean, false) then true
      else
        -- student self-scope: enrolment id is in the allowed list
        ( p_student_id is not null
          and p_student_id = any (select jsonb_array_elements_text(coalesce((select j from s) -> 'student_ids', '[]'::jsonb))) )
        or
        -- faculty campus-scope: the row's campus is in the allowed list
        ( p_campus is not null
          and lower(p_campus) = any (select lower(x) from jsonb_array_elements_text(coalesce((select j from s) -> 'campuses', '[]'::jsonb)) x) )
    end;
$$;

-- ── students: campus is on the row; self-scope by student_id ──────────────────────────────────
alter table public.students enable row level security;
drop policy if exists mcp_students_scope on public.students;
create policy mcp_students_scope on public.students
  for select to reporting_readonly
  using ( public.mcp_scope_allows(student_id, campus) );

-- ── marks / attendance: no campus column → resolve via the student's campus ───────────────────
-- (per-row EXISTS against students; fine for correctness. If this is hot, replace with a
--  SECURITY DEFINER lookup or a denormalised campus column.)
alter table public.marks enable row level security;
drop policy if exists mcp_marks_scope on public.marks;
create policy mcp_marks_scope on public.marks
  for select to reporting_readonly
  using (
    exists (
      select 1 from public.students s
      where s.student_id = marks.student_id
        and public.mcp_scope_allows(s.student_id, s.campus)
    )
  );

alter table public.attendance_sessions enable row level security;
drop policy if exists mcp_attendance_scope on public.attendance_sessions;
create policy mcp_attendance_scope on public.attendance_sessions
  for select to reporting_readonly
  using (
    exists (
      select 1 from public.students s
      where s.student_id = attendance_sessions.student_id
        and public.mcp_scope_allows(s.student_id, s.campus)
    )
  );

-- courses is reference data (no student PII) → intentionally NOT RLS-restricted here.

-- helpers readable by the app role
revoke all on function public.mcp_request_scope() from public;
revoke all on function public.mcp_scope_allows(text, text) from public;
grant execute on function public.mcp_request_scope() to reporting_readonly, service_role;
grant execute on function public.mcp_scope_allows(text, text) to reporting_readonly, service_role;

commit;

-- ── Rollout (staged, fail-closed only at the very end) ────────────────────────────────────────
-- 1. Apply on a Supabase BRANCH of sadbfvfcmmxgtatfjfmc. With NO mcp_scope claim, confirm the
--    reporting_readonly key still reads students/marks/attendance exactly as prod (fail-open proven).
-- 2. Apply to prod (still a no-op: the live shared key sends no mcp_scope claim).
-- 3. Connection-model change in the MCP: stamp a per-request mcp_scope claim on the DB JWT
--    (student → {student_ids}; faculty → {campuses}; director → {all:true}). Options: a short-lived
--    per-request JWT signed with the project JWT secret, or PostgREST GUC if a direct connection is used.
-- 4. Shadow: log any row the app returns that RLS WOULD have denied (mismatch = app bug). Then it's live
--    defense-in-depth — a regression in apply_student/apply_campus can no longer leak cross-student rows.
-- Rollback at any point: `alter table <t> disable row level security;` (or drop the policies).
