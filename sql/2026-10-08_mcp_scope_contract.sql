-- Request-scoped RBAC contract for the Moodle MCP (stage A: no data-policy change).
--
-- Safe rollout order:
--   1. Apply this contract migration.
--   2. Deploy the MCP code that sends X-MCP-Scope on every PostgREST request.
--   3. From that deployed code, call mcp_scope_probe() and verify the exact expected
--      staff/student scope is returned. The scope never comes from the end user; it is
--      generated from the verified OAuth principal inside the MCP.
--   4. Only then apply 2026-10-08_mcp_scope_rls_enforce.sql.
--
-- This stage is additive and does not alter current table policies.

begin;

-- Parse the internal header forwarded by PostgREST. Missing, oversized, malformed,
-- unknown-version, or unknown-kind inputs return NULL so every policy fails closed.
create or replace function public.mcp_request_scope()
returns jsonb
language plpgsql
stable
set search_path = ''
as $$
declare
  request_headers jsonb;
  raw_scope text;
  parsed_scope jsonb;
begin
  begin
    request_headers := nullif(current_setting('request.headers', true), '')::jsonb;
  exception when others then
    return null;
  end;

  raw_scope := request_headers ->> 'x-mcp-scope';
  if raw_scope is null or octet_length(raw_scope) > 2048 then
    return null;
  end if;

  begin
    parsed_scope := raw_scope::jsonb;
  exception when others then
    return null;
  end;

  if parsed_scope ->> 'v' <> '1'
     or parsed_scope ->> 'kind' not in ('student', 'staff', 'deny') then
    return null;
  end if;

  -- Reject contradictory or incomplete shapes before a policy interprets them.
  -- In particular, only an explicit admin/cross-campus staff role may assert all=true.
  if parsed_scope ->> 'kind' = 'student' then
    if coalesce(parsed_scope ->> 'role', '') <> 'student'
       or parsed_scope ? 'all'
       or jsonb_typeof(parsed_scope -> 'student_ids') <> 'array'
       or jsonb_typeof(parsed_scope -> 'campuses') <> 'array'
       or jsonb_typeof(parsed_scope -> 'batches') <> 'array'
       or jsonb_array_length(parsed_scope -> 'student_ids') not between 1 and 128
       or jsonb_array_length(parsed_scope -> 'campuses') not between 1 and 16
       or jsonb_array_length(parsed_scope -> 'batches') not between 1 and 32
       or exists (
         select 1 from jsonb_array_elements(parsed_scope -> 'student_ids') value
         where jsonb_typeof(value) <> 'string' or length(trim(value #>> '{}')) not between 1 and 128
       )
       or exists (
         select 1 from jsonb_array_elements(parsed_scope -> 'campuses') value
         where jsonb_typeof(value) <> 'string' or length(trim(value #>> '{}')) not between 1 and 64
       )
       or exists (
         select 1 from jsonb_array_elements(parsed_scope -> 'batches') value
         where jsonb_typeof(value) <> 'string' or length(trim(value #>> '{}')) not between 1 and 64
       ) then
      return null;
    end if;
  elsif parsed_scope ->> 'kind' = 'staff' then
    if parsed_scope ? 'student_ids'
       or coalesce(parsed_scope ->> 'role', '') not in
          ('admin', 'cross_campus', 'campus_admin', 'faculty', 'viewer') then
      return null;
    end if;
    if parsed_scope -> 'all' = 'true'::jsonb then
      if coalesce(parsed_scope ->> 'role', '') not in ('admin', 'cross_campus')
         or parsed_scope ? 'campuses' then
        return null;
      end if;
    elsif coalesce(parsed_scope ->> 'role', '') in ('admin', 'cross_campus')
       or jsonb_typeof(parsed_scope -> 'campuses') <> 'array'
       or jsonb_array_length(parsed_scope -> 'campuses') not between 1 and 16
       or exists (
         select 1 from jsonb_array_elements(parsed_scope -> 'campuses') value
         where jsonb_typeof(value) <> 'string' or length(trim(value #>> '{}')) not between 1 and 64
       ) then
      return null;
    end if;
  end if;
  return parsed_scope;
end;
$$;

-- Student and staff decisions are intentionally mutually exclusive. A student
-- header may contain campus metadata for run selection, but campus can NEVER widen
-- a student row decision to peers in that campus.
create or replace function public.mcp_scope_allows(
  p_student_id text,
  p_campus text
) returns boolean
language plpgsql
stable
set search_path = ''
as $$
declare
  scope jsonb := public.mcp_request_scope();
  kind text;
begin
  if scope is null then
    return false;
  end if;
  kind := scope ->> 'kind';

  if kind = 'deny' then
    return false;
  elsif kind = 'student' then
    if p_student_id is null or jsonb_typeof(scope -> 'student_ids') <> 'array' then
      return false;
    end if;
    return exists (
      select 1
      from jsonb_array_elements_text(scope -> 'student_ids') value
      where value = p_student_id
    );
  elsif kind = 'staff' then
    if scope -> 'all' = 'true'::jsonb then
      return true;
    end if;
    if p_campus is null or jsonb_typeof(scope -> 'campuses') <> 'array' then
      return false;
    end if;
    return exists (
      select 1
      from jsonb_array_elements_text(scope -> 'campuses') value
      where lower(value) = lower(p_campus)
    );
  end if;
  return false;
exception when others then
  return false;
end;
$$;

-- Non-student-bearing metadata is restricted by campus for staff and by BOTH
-- campus+batch for students. A student cannot inspect another batch's runs.
create or replace function public.mcp_scope_allows_run(
  p_campus text,
  p_batch text
) returns boolean
language plpgsql
stable
set search_path = ''
as $$
declare
  scope jsonb := public.mcp_request_scope();
  kind text;
  campus_ok boolean;
begin
  if scope is null or p_campus is null then
    return false;
  end if;
  kind := scope ->> 'kind';
  if kind = 'deny' then
    return false;
  elsif kind = 'staff' then
    return (scope -> 'all' = 'true'::jsonb)
      or (
        jsonb_typeof(scope -> 'campuses') = 'array'
        and exists (
          select 1 from jsonb_array_elements_text(scope -> 'campuses') value
          where lower(value) = lower(p_campus)
        )
      );
  elsif kind = 'student' then
    if p_batch is null
       or jsonb_typeof(scope -> 'campuses') <> 'array'
       or jsonb_typeof(scope -> 'batches') <> 'array' then
      return false;
    end if;
    campus_ok := exists (
      select 1 from jsonb_array_elements_text(scope -> 'campuses') value
      where lower(value) = lower(p_campus)
    );
    return campus_ok and exists (
      select 1 from jsonb_array_elements_text(scope -> 'batches') value
      where value = p_batch
    );
  end if;
  return false;
exception when others then
  return false;
end;
$$;

-- Resolve a row whose campus lives on the students table. Matching run_id as well
-- as student_id prevents a reused enrolment id from authorizing the wrong snapshot.
create or replace function public.mcp_scope_allows_student_row(
  p_run_id text,
  p_student_id text
) returns boolean
language sql
stable
set search_path = ''
as $$
  select exists (
    select 1
    from public.students s
    where s.run_id::text = p_run_id
      and s.student_id = p_student_id
      and public.mcp_scope_allows(s.student_id, s.campus)
  );
$$;

-- Course metadata is campus-scoped for staff. For students it is further limited
-- to courses in which one of their own enrolment rows exists.
create or replace function public.mcp_scope_allows_course(
  p_run_id text,
  p_course_id text
) returns boolean
language plpgsql
stable
set search_path = ''
as $$
declare
  scope jsonb := public.mcp_request_scope();
begin
  if scope is null then
    return false;
  end if;
  if scope ->> 'kind' = 'student' then
    return exists (
      select 1
      from public.enrolments e
      where e.run_id::text = p_run_id
        and e.course_id = p_course_id
        and public.mcp_scope_allows_student_row(e.run_id, e.student_id)
    );
  elsif scope ->> 'kind' = 'staff' then
    return exists (
      select 1
      from public.extraction_runs r
      where r.run_id::text = p_run_id
        and public.mcp_scope_allows_run(r.campus, r.batch)
    );
  end if;
  return false;
exception when others then
  return false;
end;
$$;

-- Narrow identity bootstrap used before a student principal exists. This returns
-- only rows for the exact verified Google email; it does not expose marks, attendance,
-- reports, or arbitrary roster search. The MCP fails closed on any non-PGRST202 error.
create or replace function public.resolve_mcp_student_identity(p_email text)
returns table (
  student_id text,
  student_name text,
  campus text,
  batch text
)
language sql
stable
security definer
set search_path = ''
set row_security = off
as $$
  select distinct
    s.student_id::text,
    s.student_name::text,
    lower(s.campus::text),
    s.batch::text
  from public.students s
  where length(trim(p_email)) between 3 and 254
    and lower(s.student_email::text) = lower(trim(p_email))
  order by s.batch::text desc, s.student_id::text
  limit 128;
$$;

-- Deployment-only canary: proves the custom header survived the Supabase gateway
-- before fail-closed RLS is activated. It is not registered as an MCP tool.
create or replace function public.mcp_scope_probe()
returns jsonb
language sql
stable
set search_path = ''
as $$
  select public.mcp_request_scope();
$$;

revoke all on function public.mcp_request_scope() from public, anon, authenticated;
revoke all on function public.mcp_scope_allows(text,text) from public, anon, authenticated;
revoke all on function public.mcp_scope_allows_run(text,text) from public, anon, authenticated;
revoke all on function public.mcp_scope_allows_student_row(text,text)
  from public, anon, authenticated;
revoke all on function public.mcp_scope_allows_course(text,text)
  from public, anon, authenticated;
revoke all on function public.resolve_mcp_student_identity(text)
  from public, anon, authenticated, mcp_oauth_writer, mcp_audit_writer;
revoke all on function public.mcp_scope_probe() from public, anon, authenticated;

grant execute on function public.mcp_request_scope() to reporting_readonly;
grant execute on function public.mcp_scope_allows(text,text) to reporting_readonly;
grant execute on function public.mcp_scope_allows_run(text,text) to reporting_readonly;
grant execute on function public.mcp_scope_allows_student_row(text,text) to reporting_readonly;
grant execute on function public.mcp_scope_allows_course(text,text) to reporting_readonly;
grant execute on function public.resolve_mcp_student_identity(text) to reporting_readonly;
grant execute on function public.mcp_scope_probe() to reporting_readonly;

commit;
