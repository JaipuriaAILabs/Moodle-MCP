-- Server-side cohort rollup (performance).
--
-- tools/common.cohort_rollup previously pulled EVERY marks row and EVERY attendance_sessions row
-- for a cohort's courses over PostgREST (paged ~1000/req) and aggregated in Python. For a live
-- Lucknow run that is ~29k marks + ~252k attendance rows (~280k rows / ~280 round-trips) and took
-- ~24s on a cold cache. This function does the same aggregation in one grouped query, returning one
-- object per student, so the first cohort call drops from ~24s to sub-second.
--
-- Byte-faithful to the Python rollup it replaces:
--   * population  = students that appear in marks OR attendance for the scope (a UNION);
--   * mark_pct    = round(100 * sum(obtained)/sum(max), 1) over graded rows with numeric
--                   obtained+max (null when the student has no such marks);
--   * zeros       = component labels of graded, zero-scored components (up to 8);
--   * attendance_pct = round(100 * present / total_rows, 1) over ALL of the student's attendance
--                   rows in scope (any status counts toward the denominator; null when none);
--   * low_attendance = per-course {course_id, ap} where the per-course attendance % (<75) is low.
-- Downstream (campus_performance_report / cohort_pulse) filters to students with a mark_pct, which
-- reproduces the current cohort means and buckets exactly.
--
-- ROUNDING: Postgres round() is half-away-from-zero; Python's round() is banker's rounding. A value
-- landing exactly on a .x5 boundary can therefore differ by 0.1 between this function and the old
-- path. That is immaterial for cohort means/distributions and a rare per-student edge; validate with
-- a shadow compare before relying on it for any exact-value contract.
--
-- SCOPE: enforced by the caller, exactly as the application layer does today — it passes the
-- already campus-scoped course_ids, and (for a student principal) that student's own enrolment ids
-- in p_self_student_ids (null for faculty => whole cohort within the passed courses).

begin;

create or replace function public.mcp_cohort_rollup(
  p_run_id uuid,
  p_course_ids uuid[],
  p_self_student_ids text[] default null
) returns jsonb
language sql stable security definer set search_path = '' as $$
  with mk as (
    select student_id,
           sum(obtained_score) filter (
             where graded and obtained_score is not null and max_score is not null) as obt,
           sum(max_score) filter (
             where graded and obtained_score is not null and max_score is not null) as mx,
           (array_remove(
              array_agg(component_label order by component_label) filter (
                where graded and obtained_score = 0 and max_score is not null),
              null))[1:8] as zeros
    from public.marks
    where run_id = p_run_id
      and course_id = any(p_course_ids)
      and (p_self_student_ids is null or student_id = any(p_self_student_ids))
    group by student_id
  ),
  at_course as (
    select student_id, course_id,
           count(*) as tot,
           count(*) filter (where lower(status_norm) = 'present') as pres
    from public.attendance_sessions
    where run_id = p_run_id
      and course_id = any(p_course_ids)
      and (p_self_student_ids is null or student_id = any(p_self_student_ids))
    group by student_id, course_id
  ),
  at_student as (
    select student_id,
           sum(tot) as tot,
           sum(pres) as pres,
           jsonb_agg(
             jsonb_build_object('course_id', course_id,
                                'ap', round((100.0 * pres / tot)::numeric, 1))
             order by course_id
           ) filter (where round((100.0 * pres / tot)::numeric, 1) < 75) as low_att
    from at_course
    group by student_id
  ),
  ids as (select student_id from mk union select student_id from at_student),
  nm as (
    select student_id, student_name
    from public.students
    where run_id = p_run_id
      and (p_self_student_ids is null or student_id = any(p_self_student_ids))
  )
  select coalesce(jsonb_object_agg(i.student_id, jsonb_build_object(
      'name', coalesce(nm.student_name, i.student_id),
      'mark_pct', case when mk.mx > 0 then round((100.0 * mk.obt / mk.mx)::numeric, 1) end,
      'zeros', to_jsonb(coalesce(mk.zeros, array[]::text[])),
      'attendance_pct', case when ats.tot > 0 then round((100.0 * ats.pres / ats.tot)::numeric, 1) end,
      'low_attendance', coalesce(ats.low_att, '[]'::jsonb)
    )), '{}'::jsonb)
  from ids i
  left join mk on mk.student_id = i.student_id
  left join at_student ats on ats.student_id = i.student_id
  left join nm on nm.student_id = i.student_id;
$$;

revoke all on function public.mcp_cohort_rollup(uuid, uuid[], text[]) from public, anon;
grant execute on function public.mcp_cohort_rollup(uuid, uuid[], text[])
  to service_role, authenticated, reporting_readonly;

commit;
