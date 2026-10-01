-- =====================================================================================
-- RBAC Phase 0 — per-campus role-based access for the Moodle MCP
-- Project: Moodle Data (sadbfvfcmmxgtatfjfmc)  |  Table: public.mcp_faculty
--
-- Additive + idempotent. Deployed alongside code with MCP_RBAC_MODE=off, so this changes
-- NO runtime behavior on its own — it only (a) extends the registry with role/capability/
-- lifecycle columns, (b) closes the anon/authenticated write grants on the access-control
-- table, and (c) adds an audit trail on grant changes. See docs/RBAC_HARDENING_PLAN.md.
--
-- Order matters: add columns → backfill the existing 'all' admin row → THEN add the
-- role/scope CHECK constraints (so they don't fail on pre-existing data).
-- Run in a transaction.
-- =====================================================================================
begin;

-- 1. Extend the registry -----------------------------------------------------------------
alter table public.mcp_faculty
  add column if not exists role         text        not null default 'faculty',
  add column if not exists can_generate boolean     not null default true,
  add column if not exists expires_at   timestamptz,
  add column if not exists granted_by   text;

-- 2. Backfill: the seeded all-campus row(s) are admins, not scoped faculty, so they must
--    satisfy the scope constraint added below (role in admin/cross_campus <=> campuses='all').
update public.mcp_faculty
   set role = 'admin'
 where campuses = '"all"'::jsonb
   and role = 'faculty';   -- only touch rows still on the default

-- 3. Constraints (added AFTER backfill) --------------------------------------------------
alter table public.mcp_faculty drop constraint if exists mcp_faculty_role_chk;
alter table public.mcp_faculty
  add constraint mcp_faculty_role_chk
  check (role in ('admin','cross_campus','campus_admin','faculty','viewer'));

-- 'all' scope only for admin/cross_campus; scoped roles must carry a non-empty campus array.
alter table public.mcp_faculty drop constraint if exists mcp_faculty_scope_chk;
alter table public.mcp_faculty
  add constraint mcp_faculty_scope_chk check (
    (role in ('admin','cross_campus') and campuses = '"all"'::jsonb)
    or (role in ('campus_admin','faculty','viewer')
        and jsonb_typeof(campuses) = 'array' and jsonb_array_length(campuses) > 0)
  );

-- 4. Lock the access-control table down --------------------------------------------------
-- The registry decides who can read student data — it must never be writable by the
-- gateway/anon role. RLS already blocks these (no permissive policy for anon/authenticated),
-- but the dangling table grants are removed so the table is safe even if a policy is ever
-- added or RLS is toggled. The server reads grants as reporting_readonly; writes are
-- service-role/admin only.
revoke all on public.mcp_faculty from anon, authenticated;
grant  select on public.mcp_faculty to reporting_readonly;   -- idempotent; server read path
-- RLS stays ON with only the existing reporting_readonly_select policy.

-- 5. Audit trail on grant changes (access grants are security events) ---------------------
create table if not exists public.mcp_faculty_history (
  id          bigint generated always as identity primary key,
  email       text        not null,
  action      text        not null,               -- INSERT | UPDATE | DELETE
  old_row     jsonb,
  new_row     jsonb,
  changed_by  text        not null default current_user,
  changed_at  timestamptz not null default now()
);
alter table public.mcp_faculty_history enable row level security;
revoke all on public.mcp_faculty_history from anon, authenticated;
-- (admins read the history via service_role / dashboard; no anon/authenticated/reporting_readonly access)

create or replace function public.log_mcp_faculty_change()
returns trigger language plpgsql security definer set search_path = public as $$
begin
  insert into public.mcp_faculty_history(email, action, old_row, new_row)
  values (coalesce(new.email, old.email), tg_op, to_jsonb(old), to_jsonb(new));
  return coalesce(new, old);
end $$;
-- A trigger function runs as the table owner regardless of EXECUTE grants; revoke direct
-- execute so it is NOT callable via PostgREST RPC by anon/authenticated (advisor 0028/0029).
revoke execute on function public.log_mcp_faculty_change() from anon, authenticated, public;

drop trigger if exists trg_mcp_faculty_history on public.mcp_faculty;
create trigger trg_mcp_faculty_history
  after insert or update or delete on public.mcp_faculty
  for each row execute function public.log_mcp_faculty_change();

commit;

-- =====================================================================================
-- Post-apply sanity (run separately, expect: admin row present, no anon/authenticated grants):
--   select email, role, campuses, can_generate, active from public.mcp_faculty order by email;
--   select grantee, string_agg(privilege_type,',') from information_schema.role_table_grants
--     where table_schema='public' and table_name='mcp_faculty' group by grantee;
-- Seeding real faculty (Phase 2) — example:
--   insert into public.mcp_faculty (email, name, role, campuses, can_generate, active, granted_by)
--   values ('prof.noida@jaipuria.ac.in','Prof Noida','faculty','["noida"]'::jsonb,true,true,'mansi.gambhir@jaipuria.ac.in')
--   on conflict (email) do update set role=excluded.role, campuses=excluded.campuses,
--     can_generate=excluded.can_generate, active=excluded.active, updated_at=now();
-- =====================================================================================
