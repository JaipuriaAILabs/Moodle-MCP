-- =====================================================================================
-- RBAC self-service access requests (Phase 2) — applied to prod 2026-10-01
-- Project: Moodle Data (sadbfvfcmmxgtatfjfmc)
--
-- An unprovisioned, verified account gets a limited "pending" session (see
-- security._pending_principal) whose only useful action is request_access: it files a
-- row here for an admin to approve out-of-band (service_role). The read-only server role
-- files requests via a SECURITY DEFINER RPC, so it needs no write grant on the inbox or
-- the registry. Privileged roles (admin, cross_campus) are NEVER self-requestable.
-- Behaviour-neutral until MCP_RBAC_MODE=enforce.
-- =====================================================================================
begin;

create table if not exists public.mcp_access_requests (
  email              text        primary key,
  name               text,
  requested_role     text        not null default 'faculty',
  requested_campuses jsonb       not null,
  status             text        not null default 'pending',
  created_at         timestamptz not null default now(),
  updated_at         timestamptz not null default now(),
  decided_by         text,
  decided_at         timestamptz,
  note               text,
  constraint mcp_access_requests_role_chk   check (requested_role in ('faculty','campus_admin','viewer')),
  constraint mcp_access_requests_status_chk check (status in ('pending','approved','rejected')),
  constraint mcp_access_requests_camp_chk   check (jsonb_typeof(requested_campuses)='array'
                                                   and jsonb_array_length(requested_campuses) > 0)
);

alter table public.mcp_access_requests enable row level security;
revoke all on public.mcp_access_requests from anon, authenticated;
grant select on public.mcp_access_requests to reporting_readonly;   -- admin list (app-gated to role=admin)
drop policy if exists mcp_access_requests_ro_select on public.mcp_access_requests;
create policy mcp_access_requests_ro_select on public.mcp_access_requests
  for select to reporting_readonly using (true);

create or replace function public.request_mcp_access(
  p_email text, p_name text, p_role text, p_campuses jsonb)
returns text language plpgsql security definer set search_path = public as $$
begin
  insert into public.mcp_access_requests(email, name, requested_role, requested_campuses, status, updated_at)
  values (lower(p_email), nullif(p_name,''), p_role, p_campuses, 'pending', now())
  on conflict (email) do update
    set name = coalesce(excluded.name, public.mcp_access_requests.name),
        requested_role = excluded.requested_role,
        requested_campuses = excluded.requested_campuses,
        status = 'pending',
        updated_at = now()
    where public.mcp_access_requests.status <> 'approved';
  return 'pending';
end $$;
-- Not callable via PostgREST RPC by anon/authenticated; only the read-only server role.
revoke all on function public.request_mcp_access(text,text,text,jsonb) from public, anon, authenticated;
grant execute on function public.request_mcp_access(text,text,text,jsonb) to reporting_readonly;

commit;

-- =====================================================================================
-- APPROVE a request (admin, service_role / dashboard — NOT granted to the server role,
-- so a leaked read-only key cannot grant access). Run with a service_role connection:
--
--   with r as (select * from public.mcp_access_requests where email = :email and status='pending')
--   insert into public.mcp_faculty (email, name, role, campuses, can_generate, active, granted_by)
--   select email, name, requested_role, requested_campuses,
--          (requested_role <> 'viewer'), true, :admin_email from r
--   on conflict (email) do update set role=excluded.role, campuses=excluded.campuses,
--      can_generate=excluded.can_generate, active=true, updated_at=now();
--   update public.mcp_access_requests set status='approved', decided_by=:admin_email,
--      decided_at=now(), updated_at=now() where email=:email;
-- =====================================================================================
