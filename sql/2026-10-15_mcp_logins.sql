-- mcp_logins — login ledger (AIA observability): every authenticated identity that
-- connects to the MCP, with the role/campuses it resolved to, a login count and last_seen.
-- Roles stay AUTHORITATIVE in mcp_faculty; this is an append/upsert observability ledger.
--
-- Written by the read-only MCP role (reporting_readonly) ONLY via the SECURITY DEFINER RPC
-- below (it has EXECUTE on the function but no write grant on the table). The MCP calls it
-- fire-and-forget + throttled from the on_initialize (connect) middleware (security.py
-- _record_login_bg → faculty.record_login), so a ledger write never blocks or fails a request.
--
-- Idempotent: safe to apply twice.

create table if not exists public.mcp_logins (
  email        text primary key,
  name         text,
  role         text,
  campuses     jsonb,
  login_count  integer not null default 1,
  first_seen   timestamptz not null default now(),
  last_seen    timestamptz not null default now()
);

create or replace function public.record_mcp_login(
  p_email text, p_name text, p_role text, p_campuses jsonb
) returns void
language plpgsql security definer set search_path = public as $$
begin
  if p_email is null or btrim(p_email) = '' then
    return;
  end if;
  insert into public.mcp_logins (email, name, role, campuses)
  values (lower(btrim(p_email)), p_name, p_role, p_campuses)
  on conflict (email) do update set
    name        = excluded.name,
    role        = excluded.role,
    campuses    = excluded.campuses,
    login_count = public.mcp_logins.login_count + 1,
    last_seen   = now();
end;
$$;

revoke all on function public.record_mcp_login(text, text, text, jsonb) from public;
grant execute on function public.record_mcp_login(text, text, text, jsonb) to reporting_readonly;

-- Deny-by-default: direct table access is service_role only; reporting_readonly writes
-- exclusively through the SECURITY DEFINER RPC above.
alter table public.mcp_logins enable row level security;
