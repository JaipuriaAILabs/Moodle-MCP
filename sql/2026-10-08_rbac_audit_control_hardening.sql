-- RBAC + audit-ledger defense in depth (2026-10-08).
-- Project: Moodle Data (sadbfvfcmmxgtatfjfmc)
-- Apply with the Supabase migration role after the authoritative whitelist seed.
-- Idempotent: safe to re-run.

begin;

-- The access registry is readable by the MCP's least-privilege data role only.
-- All mutations remain migration/service-role operations and are audited by the
-- existing trg_mcp_faculty_history trigger.
alter table public.mcp_faculty enable row level security;
revoke all on public.mcp_faculty from public, anon, authenticated;
revoke insert, update, delete, truncate, references, trigger
  on public.mcp_faculty from reporting_readonly;
grant select on public.mcp_faculty to reporting_readonly;

drop policy if exists reporting_readonly_select on public.mcp_faculty;
create policy reporting_readonly_select on public.mcp_faculty
  for select to reporting_readonly using (true);

-- Grant-change history is security evidence, not runtime data. No web-facing
-- role, including reporting_readonly, may read or mutate it.
alter table public.mcp_faculty_history enable row level security;
revoke all on public.mcp_faculty_history
  from public, anon, authenticated, reporting_readonly;

-- PostgreSQL views use owner privileges unless security_invoker is enabled.
-- Make the analyst view obey the querying role's base-table/RLS privileges and
-- keep it ungranted until a dedicated reviewer role is explicitly approved.
alter view if exists mcp_audit.v_activity set (security_invoker = true);
revoke all on mcp_audit.v_activity from public, anon, authenticated;

-- Parent-table RLS protects normal reads through mcp_audit.tool_calls. Enabling
-- RLS on each partition also prevents a future direct-partition grant from
-- bypassing that parent boundary.
do $$
declare
  partition_name text;
  role_name text;
begin
  foreach partition_name in array array[
    'tool_calls_2026_q3',
    'tool_calls_2026_q4',
    'tool_calls_2027_h1',
    'tool_calls_default'
  ] loop
    if to_regclass('mcp_audit.' || partition_name) is not null then
      execute format('alter table mcp_audit.%I enable row level security', partition_name);
      execute format('revoke all on mcp_audit.%I from public, anon, authenticated',
                     partition_name);
      foreach role_name in array array[
        'reporting_readonly', 'mcp_oauth_writer', 'mcp_audit_writer'
      ] loop
        if exists (select 1 from pg_roles where rolname = role_name) then
          execute format('revoke all on mcp_audit.%I from %I', partition_name, role_name);
        end if;
      end loop;
    end if;
  end loop;
end $$;

-- Only the write-only audit role may execute the append RPC.
revoke all on function public.record_mcp_tool_call(
  uuid,text,text,text,text,text,integer,text,text,text,jsonb)
  from public, anon, authenticated, reporting_readonly;
grant execute on function public.record_mcp_tool_call(
  uuid,text,text,text,text,text,integer,text,text,text,jsonb)
  to mcp_audit_writer;

commit;

-- Post-apply verification: run sql/verify_mcp_audit_lockdown.sql and confirm
-- every anon/authenticated/data-reader privilege is false while
-- writer_can_write is true.
