-- =====================================================================================
-- Faculty intake / staging table — applied to prod 2026-10-03
-- Project: Moodle Data (sadbfvfcmmxgtatfjfmc)
--
-- A human-friendly table to COLLECT faculty details as they arrive (fill via the Supabase
-- table editor or CSV import). Rows are then validated + PROMOTED into public.mcp_faculty
-- (the live access registry). Admin-only: no anon/authenticated access. Campuses are plain
-- text here for easy entry ('noida', 'noida|lucknow', or 'all') and normalized to jsonb on
-- promotion.
-- =====================================================================================
create table if not exists public.mcp_faculty_intake (
  id            bigint generated always as identity primary key,
  email         text        not null,
  name          text,
  role          text        not null default 'faculty',   -- faculty|campus_admin|viewer|cross_campus|admin
  campuses      text        not null,                      -- 'noida' | 'noida|lucknow' | 'all'
  can_generate  boolean     not null default true,
  status        text        not null default 'pending',    -- pending | promoted | rejected
  submitted_by  text,
  note          text,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now(),
  constraint mcp_faculty_intake_role_chk   check (role in ('admin','cross_campus','campus_admin','faculty','viewer')),
  constraint mcp_faculty_intake_status_chk check (status in ('pending','promoted','rejected'))
);

alter table public.mcp_faculty_intake enable row level security;
revoke all on public.mcp_faculty_intake from anon, authenticated;
grant select on public.mcp_faculty_intake to reporting_readonly;   -- admins/app can view the queue
drop policy if exists mcp_faculty_intake_ro_select on public.mcp_faculty_intake;
create policy mcp_faculty_intake_ro_select on public.mcp_faculty_intake
  for select to reporting_readonly using (true);

-- =====================================================================================
-- PROMOTE pending intake rows into the live registry (run as service_role when real data
-- lands). Converts the plain-text campuses to the jsonb shape mcp_faculty expects and marks
-- the intake rows promoted. Review the pending rows first.
-- =====================================================================================
-- insert into public.mcp_faculty (email, name, role, campuses, can_generate, active, granted_by)
-- select lower(email), name, role,
--        case when lower(trim(campuses)) = 'all' then '"all"'::jsonb
--             else to_jsonb(array(
--               select lower(trim(c))
--               from regexp_split_to_table(campuses, '[|,]') c
--               where trim(c) <> '')) end,
--        can_generate, true, 'intake-promote'
-- from public.mcp_faculty_intake
-- where status = 'pending'
-- on conflict (email) do update set
--   name = coalesce(excluded.name, public.mcp_faculty.name),
--   role = excluded.role, campuses = excluded.campuses,
--   can_generate = excluded.can_generate, active = true, updated_at = now();
--
-- update public.mcp_faculty_intake set status = 'promoted', updated_at = now()
--  where status = 'pending';
-- =====================================================================================
