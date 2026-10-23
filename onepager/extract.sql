with RR as (select 'fe1be2dd-85a0-4980-a665-8b78635d40da'::uuid rid),
S as (select unnest(array['JN00MB003','JN00MB005','JN00MB002','JN00MB001','JN00MB004']) sid),
t1 as (select c.course_id, c.course_name from courses c, RR where c.run_id=RR.rid and split_part(c.course_code,'_',3)='1'),
cem as (select m.course_id, m.student_id, m.component_label l, m.obtained_score o, m.max_score x from marks m, RR where m.run_id=RR.rid and m.kind='CE' and m.course_id in (select course_id from t1)),
etm as (select m.course_id, m.student_id, m.obtained_score e from marks m, RR where m.run_id=RR.rid and m.kind='ET' and m.component_label='__total__'),
cls_c as (select course_id, l, round(avg(o/x*100)::numeric,1) c from cem where x>0 and o is not null group by 1,2),
cls_et as (select course_id, round(avg(e/40*100)::numeric,1) c, count(*) n from etm group by 1),
tot as (select course_id, student_id, sum(o)+coalesce(max(e),0) t from (select cem.course_id, cem.student_id, cem.o, etm.e from cem left join etm on etm.course_id=cem.course_id and etm.student_id=cem.student_id) q where o is not null group by 1,2),
cls_t as (select course_id, round(avg(t)::numeric,1) c from tot group by 1),
att as (select a.course_id, a.student_id, 100.0*count(*) filter (where status_norm='present')/nullif(count(*) filter (where status_norm in ('present','absent','late')),0) p from attendance_sessions a, RR where a.run_id=RR.rid and a.course_id in (select course_id from t1) group by 1,2),
cls_a as (select course_id, round(avg(p)::numeric,1) c from att group by 1)
select st.student_id id, regexp_replace(trim(st.student_name),'\s+',' ','g') name, st.batch,
 (select jsonb_agg(jsonb_build_object('s', t1.course_name, 'tot', tt.t, 'ct', ct.c, 'ay', round(a.p::numeric,1), 'ac', ca.c,
    'et', (select e from etm where etm.course_id=t1.course_id and etm.student_id=st.student_id),
    'etc', (select c from cls_et where cls_et.course_id=t1.course_id),
    'cm', (select jsonb_agg(jsonb_build_array(cem.l, cem.o, cem.x, cc.c) order by cem.l) from cem left join cls_c cc on cc.course_id=cem.course_id and cc.l=cem.l where cem.course_id=t1.course_id and cem.student_id=st.student_id)) order by t1.course_name)
  from t1 join tot tt on tt.course_id=t1.course_id and tt.student_id=st.student_id join cls_t ct on ct.course_id=t1.course_id join att a on a.course_id=t1.course_id and a.student_id=st.student_id join cls_a ca on ca.course_id=t1.course_id) subs
from students st, RR where st.run_id=RR.rid and st.student_id in (select sid from S) order by st.student_id;