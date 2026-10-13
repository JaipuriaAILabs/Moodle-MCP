-- Expose the faculty/course snapshot to the Moodle MCP's least-privilege reporting_readonly role,
-- WITHOUT granting it the raw-payload base tables.
--
-- Migration 0015 (moodle-agent) created the faculty_* tables service_role-only, with three
-- curated views (current_faculty_directory / _course_assignments / _feedback) as security_invoker.
-- The MCP's reporting_readonly role must read ONLY those curated views (campus/course/faculty columns
-- and feedback counts) and must NEVER reach `faculty_scope_snapshots.payload` (the full raw API dump)
-- or `source_record`/`details`.
--
-- Approach: flip the three views to SECURITY DEFINER (they run with the owner's privileges, which can
-- read the base tables), then grant SELECT on ONLY the views to reporting_readonly. No base-table grant
-- is given, so reporting_readonly can never select the raw payload directly. Campus scope stays enforced
-- in the MCP application layer (svc.apply_campus), exactly as it is for the student tables today. When the
-- request-scoped RLS contract (sql/2026-10-08_mcp_scope_*.sql) is deployed, add campus-scoped policies to
-- the faculty base tables and revert these views to security_invoker so the DB becomes the backstop here too.

begin;

alter view public.current_faculty_course_assignments set (security_invoker = false);
alter view public.current_faculty_directory          set (security_invoker = false);
alter view public.current_faculty_feedback           set (security_invoker = false);

grant select on
    public.current_faculty_course_assignments,
    public.current_faculty_directory,
    public.current_faculty_feedback
  to reporting_readonly;

-- The base tables stay service_role-only: reporting_readonly gets NO direct grant on
-- faculty_scope_snapshots / faculty_course_assignments / faculty_course_feedback.

notify pgrst, 'reload schema';
commit;

-- Rollback (privileged session):
--   revoke select on public.current_faculty_course_assignments, public.current_faculty_directory,
--     public.current_faculty_feedback from reporting_readonly;
--   alter view public.current_faculty_course_assignments set (security_invoker = true);
--   alter view public.current_faculty_directory          set (security_invoker = true);
--   alter view public.current_faculty_feedback           set (security_invoker = true);
--   notify pgrst, 'reload schema';
