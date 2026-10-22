-- PERF FIX for scoped-RLS enforce: make scope resolution happen ONCE per query, not per row.
--
-- PROBLEM (found running the live MCP end-to-end, 2026-10-22): with RBAC enforce on, every
-- cohort/data tool hangs >300s and crashes the container. Root cause: the policies on the big
-- tables call mcp_scope_allows_student_row(run_id, student_id) PER ROW, which runs a correlated
-- subquery into students + the non-inlinable plpgsql mcp_scope_allows() that re-parses the
-- x-mcp-scope header JSON on every call. Measured on this DB: raw cohort scan = 11 ms for 28,788
-- marks rows; the same rows through the RLS function = ~0.31 ms/row (~800x slower) -> marks is
-- 368,758 rows and attendance/enrolments each carry their own per-row policy, so a cohort query
-- compounds past 300s and OOMs the container.
--
-- FIX: the set of in-scope (run_id, student_id) pairs is CONSTANT for a request. Resolve it ONCE
-- (an uncorrelated subquery / InitPlan over the 3,680-row students table) and let each big-table
-- row do an O(1) hash-membership test, instead of a per-row plpgsql call. Semantics are IDENTICAL
-- (a marks/attendance/enrolment row is allowed iff it matches an in-scope students row, exactly as
-- mcp_scope_allows_student_row decided); only the evaluation strategy changes.
--
-- VALIDATED (EXPLAIN ANALYZE, inline equivalent, staff scope campuses={lucknow}) over the FULL
-- 368,758-row marks table: 1.24s (index-only marks_pkey lookups), vs the current policy's >300s
-- hang. Real tool queries also filter by run_id, so they land sub-second.
--
-- Apply with the Supabase migration role (SQL editor / CLI) on sadbfvfcmmxgtatfjfmc. RECOMMENDED:
-- test on a Supabase branch first and spot-check isolation (see the verification queries at the
-- bottom) before applying to prod. Rollback: re-point the policies to the retained old functions
-- (mcp_scope_allows / mcp_scope_allows_run / mcp_scope_allows_student_row) — see the ROLLBACK block.

begin;

-- ---------------------------------------------------------------------------
-- 1. Once-per-query scope accessors (GUC-only; cheap; evaluated once as InitPlans).
--    These mirror the exact comparisons in mcp_scope_allows / mcp_scope_allows_run:
--      - campuses: case-insensitive (lower both sides)
--      - student_ids, batches: exact string match
-- ---------------------------------------------------------------------------
create or replace function public.mcp_scope_kind()
  returns text language sql stable as
$$ select coalesce(public.mcp_request_scope() ->> 'kind', 'deny'); $$;

create or replace function public.mcp_scope_is_all()
  returns boolean language sql stable as
$$ select coalesce(public.mcp_request_scope() -> 'all' = 'true'::jsonb, false); $$;

create or replace function public.mcp_scope_campuses_lower()
  returns text[] language sql stable as
$$ select coalesce(array_agg(lower(value)), '{}'::text[])
   from jsonb_array_elements_text(coalesce(public.mcp_request_scope() -> 'campuses', '[]'::jsonb)) as value; $$;

create or replace function public.mcp_scope_batches()
  returns text[] language sql stable as
$$ select coalesce(array_agg(value), '{}'::text[])
   from jsonb_array_elements_text(coalesce(public.mcp_request_scope() -> 'batches', '[]'::jsonb)) as value; $$;

create or replace function public.mcp_scope_student_ids()
  returns text[] language sql stable as
$$ select coalesce(array_agg(value), '{}'::text[])
   from jsonb_array_elements_text(coalesce(public.mcp_request_scope() -> 'student_ids', '[]'::jsonb)) as value; $$;

-- The in-scope (run_id, student_id) keys, computed ONCE. SECURITY DEFINER so it reads students
-- raw (exactly like the existing DEFINER mcp_scope_allows_student_row) — no RLS recursion, no
-- double-filter. The WHERE below is the sole scope boundary and matches mcp_scope_allows():
--   student -> student_id in scope.student_ids
--   staff   -> all=true, or lower(campus) in lower(scope.campuses)
create or replace function public.mcp_allowed_student_keys()
  returns table(run_id uuid, student_id text)
  language sql stable security definer set search_path = public as
$$
  select s.run_id, s.student_id
  from public.students s
  where case (select public.mcp_scope_kind())
          when 'student' then s.student_id = any ((select public.mcp_scope_student_ids())::text[])
          when 'staff'   then (select public.mcp_scope_is_all())
                              or lower(s.campus) = any ((select public.mcp_scope_campuses_lower())::text[])
          else false
        end;
$$;

-- Least-privilege grants, matching the existing scope functions (never anon/authenticated).
revoke all on function
  public.mcp_scope_kind(), public.mcp_scope_is_all(), public.mcp_scope_campuses_lower(),
  public.mcp_scope_batches(), public.mcp_scope_student_ids(), public.mcp_allowed_student_keys()
  -- Supabase default privileges auto-GRANT execute to anon + authenticated on new public
  -- functions; `from public` alone would leave them, so the DEFINER mcp_allowed_student_keys()
  -- (which bypasses RLS) must be revoked from those roles too.
  from public, anon, authenticated;
grant execute on function
  public.mcp_scope_kind(), public.mcp_scope_is_all(), public.mcp_scope_campuses_lower(),
  public.mcp_scope_batches(), public.mcp_scope_student_ids(), public.mcp_allowed_student_keys()
  to reporting_readonly, service_role;

-- ---------------------------------------------------------------------------
-- 2. Re-point the heavy-table policies to the set-based test (identical semantics, O(1)/row).
--    (courses is left as-is: tiny table, not a bottleneck, still uses mcp_scope_allows_course.)
-- ---------------------------------------------------------------------------
alter policy mcp_marks_scope on public.marks using (
  (run_id, student_id) in (select k.run_id, k.student_id from public.mcp_allowed_student_keys() k)
);

alter policy mcp_attendance_scope on public.attendance_sessions using (
  (run_id, student_id) in (select k.run_id, k.student_id from public.mcp_allowed_student_keys() k)
);

alter policy mcp_enrolments_scope on public.enrolments using (
  (run_id, student_id) in (select k.run_id, k.student_id from public.mcp_allowed_student_keys() k)
);

-- onepager_narratives.run_id is TEXT (not uuid) -> cast the key. The table is OPTIONAL (the
-- creating migration guards it with to_regclass), so guard the ALTER too: an unconditional ALTER
-- on a missing table/policy would abort and roll back every policy update above.
do $$
begin
  if to_regclass('public.onepager_narratives') is not null then
    execute 'alter policy mcp_onepager_scope on public.onepager_narratives using (
      (run_id, student_id) in (select k.run_id::text, k.student_id from public.mcp_allowed_student_keys() k)
    )';
  end if;
end $$;

-- students / student_reports carry student_id + campus directly -> direct check (mirrors mcp_scope_allows).
alter policy mcp_students_scope on public.students using (
  case (select public.mcp_scope_kind())
    when 'student' then student_id = any ((select public.mcp_scope_student_ids())::text[])
    when 'staff'   then (select public.mcp_scope_is_all())
                        or lower(campus) = any ((select public.mcp_scope_campuses_lower())::text[])
    else false
  end
);

alter policy mcp_student_reports_scope on public.student_reports using (
  case (select public.mcp_scope_kind())
    when 'student' then student_id = any ((select public.mcp_scope_student_ids())::text[])
    when 'staff'   then (select public.mcp_scope_is_all())
                        or lower(campus) = any ((select public.mcp_scope_campuses_lower())::text[])
    else false
  end
);

-- extraction_runs carries campus + batch directly (mirrors mcp_scope_allows_run).
alter policy mcp_extraction_runs_scope on public.extraction_runs using (
  case (select public.mcp_scope_kind())
    when 'staff'   then (select public.mcp_scope_is_all())
                        or lower(campus) = any ((select public.mcp_scope_campuses_lower())::text[])
    when 'student' then lower(campus) = any ((select public.mcp_scope_campuses_lower())::text[])
                        and batch = any ((select public.mcp_scope_batches())::text[])
    else false
  end
);

commit;

-- ---------------------------------------------------------------------------
-- VERIFICATION (run after apply; set a scope header and confirm isolation + speed).
-- NOTE: `set local` + `set local role` are TRANSACTION-local, so the checks must run inside one
-- transaction (begin/rollback) — statement-by-statement under autocommit would drop the scope and
-- run the counts as the migration role, testing nothing.
--   begin;
--   select set_config('request.headers',
--     json_build_object('x-mcp-scope','{"v":1,"kind":"staff","role":"campus_admin","all":false,"campuses":["lucknow"]}')::text, true);
--   set local role reporting_readonly;
--   select count(*) from public.marks;                 -- only lucknow marks, returns fast (<2s)
--   select count(distinct campus) from public.students; -- expect 1 (lucknow only)
--   rollback;
-- Cross-scope denial: with campuses=["noida"] a lucknow-only grant must return 0 lucknow rows.
--
-- ROLLBACK (re-point policies to the retained old per-row functions):
--   alter policy mcp_marks_scope       on public.marks               using (mcp_scope_allows_student_row((run_id)::text, student_id));
--   alter policy mcp_attendance_scope  on public.attendance_sessions using (mcp_scope_allows_student_row((run_id)::text, student_id));
--   alter policy mcp_enrolments_scope  on public.enrolments          using (mcp_scope_allows_student_row((run_id)::text, student_id));
--   alter policy mcp_onepager_scope    on public.onepager_narratives using (mcp_scope_allows_student_row(run_id, student_id));
--   alter policy mcp_students_scope        on public.students        using (mcp_scope_allows(student_id, campus));
--   alter policy mcp_student_reports_scope on public.student_reports using (mcp_scope_allows(student_id, campus));
--   alter policy mcp_extraction_runs_scope on public.extraction_runs using (mcp_scope_allows_run(campus, batch));
