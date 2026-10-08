-- Authoritative Moodle MCP data-access whitelist (2026-10-08).
-- Project: Moodle Data (sadbfvfcmmxgtatfjfmc)
--
-- public.mcp_faculty is the dedicated Supabase access-control table read by the
-- MCP in MCP_RBAC_MODE=enforce. This migration replaces the active grant set
-- with the 12 accounts approved below. Existing rows are deactivated, not
-- deleted, so the audit trail and a recoverable rollback remain intact.
--
-- Role semantics:
--   cross_campus -> query all four campuses; no access-request administration
--   campus_admin -> query only the single campus in `campuses`
--
-- Apply with a service-role/migration credential. The MCP runtime credential is
-- SELECT-only and cannot modify this registry.

begin;

-- Fail closed: any grant not explicitly present below becomes inactive.
update public.mcp_faculty
   set active = false,
       updated_at = now()
 where email not in (
   'shreevats@jaipuria.ac.in',
   'shiva.kakkar@jaipuria.ac.in',
   'prabhat.pankaj@jaipuria.ac.in',
   'shailaja.sharma@jaipuria.ac.in',
   'amit.attry@jaipuria.ac.in',
   'sauresh.mehrotra@jaipuria.ac.in',
   'kanchan.rana@jaipuria.ac.in',
   'sandip.das@jaipuria.ac.in',
   'rahul.singh@jaipuria.ac.in',
   'sushma.vishnani@jaipuria.ac.in',
   'daneshwar.sharma@jaipuria.ac.in',
   'deepankar.chakrabarti@jaipuria.ac.in'
 );

insert into public.mcp_faculty
  (email, name, role, campuses, can_generate, active, granted_by,
   expires_at, updated_at)
values
  ('shreevats@jaipuria.ac.in', 'Shreevats', 'cross_campus', '"all"'::jsonb, true, true,
   'authoritative-whitelist-2026-10-08', null, now()),
  ('shiva.kakkar@jaipuria.ac.in', 'Shiva Kakkar', 'cross_campus', '"all"'::jsonb, true, true,
   'authoritative-whitelist-2026-10-08', null, now()),
  ('prabhat.pankaj@jaipuria.ac.in', 'Prabhat Pankaj', 'cross_campus', '"all"'::jsonb, true, true,
   'authoritative-whitelist-2026-10-08', null, now()),
  ('shailaja.sharma@jaipuria.ac.in', 'Shailaja Sharma', 'cross_campus', '"all"'::jsonb, true, true,
   'authoritative-whitelist-2026-10-08', null, now()),
  ('amit.attry@jaipuria.ac.in', 'Amit Attry', 'cross_campus', '"all"'::jsonb, true, true,
   'authoritative-whitelist-2026-10-08', null, now()),
  ('sauresh.mehrotra@jaipuria.ac.in', 'Sauresh Mehrotra', 'cross_campus', '"all"'::jsonb, true, true,
   'authoritative-whitelist-2026-10-08', null, now()),
  ('kanchan.rana@jaipuria.ac.in', 'Kanchan Rana', 'cross_campus', '"all"'::jsonb, true, true,
   'authoritative-whitelist-2026-10-08', null, now()),
  ('sandip.das@jaipuria.ac.in', 'Sandip Das', 'cross_campus', '"all"'::jsonb, true, true,
   'authoritative-whitelist-2026-10-08', null, now()),
  ('rahul.singh@jaipuria.ac.in', 'Rahul Singh', 'campus_admin', '["noida"]'::jsonb, true, true,
   'authoritative-whitelist-2026-10-08', null, now()),
  ('sushma.vishnani@jaipuria.ac.in', 'Sushma Vishnani', 'campus_admin', '["lucknow"]'::jsonb, true, true,
   'authoritative-whitelist-2026-10-08', null, now()),
  ('daneshwar.sharma@jaipuria.ac.in', 'Daneshwar Sharma', 'campus_admin', '["jaipur"]'::jsonb, true, true,
   'authoritative-whitelist-2026-10-08', null, now()),
  ('deepankar.chakrabarti@jaipuria.ac.in', 'Deepankar Chakrabarti', 'campus_admin', '["indore"]'::jsonb, true, true,
   'authoritative-whitelist-2026-10-08', null, now())
on conflict (email) do update
  set name = excluded.name,
      role = excluded.role,
      campuses = excluded.campuses,
      can_generate = excluded.can_generate,
      active = true,
      granted_by = excluded.granted_by,
      expires_at = null,
      updated_at = now();

commit;

-- Verification (expect exactly 12 active rows: 8 cross_campus + 4 campus_admin):
-- select email, role, campuses, active
-- from public.mcp_faculty
-- where active
-- order by role, email;
