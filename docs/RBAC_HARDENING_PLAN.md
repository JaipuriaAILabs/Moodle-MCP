# Moodle MCP — per-campus RBAC + security hardening plan

**Goal:** move the Moodle MCP from *"every verified `@jaipuria.ac.in` account sees all 4 campuses"*
(the current **G1 all-access** policy) to **least-privilege, role-based, campus-scoped access** — a
Lucknow faculty member sees only Lucknow, a central leader sees all, unlisted accounts are denied —
enforced consistently and logged per campus. Ties to the DPDP data-minimization ask (Shiva Sir,
AIA-1392 / AIA-1386).

Grounded in the live system (verified 2026-09-30): code at `moodle-mcp/`, data in Supabase
`sadbfvfcmmxgtatfjfmc` ("Moodle Data").

---

## 0. The 4 campuses (authoritative — exact `campus` codes)

`campus` is a plain lowercase **text** column on `students` / `courses` (no enum in code today).

| code (canonical) | students | batches |
| --- | --- | --- |
| `lucknow` | 1,408 | 3 |
| `noida` | 910 | 3 |
| `jaipur` | 648 | 2 |
| `indore` | 179 | 1 |

Action: add a canonical list `MCP_CAMPUSES="noida,lucknow,jaipur,indore"` to config so grants can be
validated against known campuses instead of accepting free-form strings.

---

## 1. Current state (what exists, what's missing)

**Auth:** OAuth via Google (`TolerantGoogleProvider`, `server.py:72-103`); identity = verified
`email` from token claims (`security.resolve_oauth_principal`, `security.py:160-171`). A static-token
mode exists only when OAuth is off.

**The choke point (all RBAC changes land here):** `security.principal_from_claims`
(`security.py:96-157`). Today, for any `jaipuria.ac.in` (or subdomain) email it returns
`{"campuses": None}` = **all campuses**, *before* any registry lookup (`security.py:116-118`). For
non-Jaipuria emails only, a 5-step ladder already consults the `mcp_faculty` table, a `students`
deny-list, `MCP_FACULTY` env override, a domain gate, and `OAUTH_DEFAULT_CAMPUSES` (default `none`).

**Enforcement (already sound — do not rebuild):** `MoodleService.campus_scope()` +
`apply_campus()` (`supabase_client.py:57-73`) inject a PostgREST `.in_("campus", scope)` filter;
`None` grant ⇒ no filter (all), a list ⇒ scoped, empty ⇒ zero rows. Every tool guards up front with
`if svc.campus_scope(p.campus) == []: deny`, and `latest_run`/`roster_member` refuse out-of-scope
runs as defense-in-depth (the DB key can bypass RLS). `compare_campuses` (`analytics.py:80-108`)
derives its campus set but still routes each through the scope gate, so it is safe for scoped callers.

**The existing registry:** table **`public.mcp_faculty`** — `email` (PK), `name`, `campuses` (jsonb:
`"all"` or a list), `active`, `note`, timestamps. **1 row today** (`mansi.gambhir@jaipuria.ac.in →
"all"`, admin). `faculty.normalize_campuses` maps `"all"`→None, list→scoped, else deny.

**Gaps to close:**
- **No role concept.** Principals carry only `name/email/campuses`. Every authenticated caller can
  call **every** tool, including the write/cost tool `create_report` (gated only by a rate budget +
  campus scope, `actions.py:39-65,281`).
- **G1 branch bypasses the registry** for Jaipuria users → the registry is inert for the people who
  actually use the system.
- **`mcp_faculty` write grants to `anon`/`authenticated`** (masked by RLS today, but must be revoked).
- **RBAC is app-layer only** — one shared `reporting_readonly` DB key for all callers; no per-caller
  RLS. Acceptable for launch; DB-layer campus RLS is a later hardening phase.

---

## 2. Target model — roles × campuses

Extend the principal with **`role`** and a **`can_generate`** capability. Scope (`campuses`) stays the
breadth dimension.

| role | campuses | read tools | `create_report` | cross-campus tools | manage registry |
| --- | --- | --- | --- | --- | --- |
| `faculty` | one or a list | own campus(es) only | yes (own) | own campus(es) only | no |
| `campus_admin` | one or a list | own campus(es) | yes | own campus(es) | no |
| `cross_campus` | `all` | all 4 | yes | all | no |
| `admin` | `all` | all 4 | yes | all | **yes** |
| `viewer` | one or a list | own campus(es) | **no** (`can_generate=false`) | own | no |
| *(unlisted / inactive / expired)* | — | **DENY** | **DENY** | **DENY** | — |

For **v1** the minimal set is `faculty` + `cross_campus` + `admin`; `campus_admin` and `viewer` are
ready-made extension points (same scoping, different capabilities) — include them in the schema now,
use them as needed. `role` drives *breadth + rights*; `campuses` drives *which campuses*;
`can_generate` gates the one paid/action tool.

> **"Logging in differently per campus":** one connector URL, one Google login — the *session* is
> scoped by the registry lookup at auth time. (A per-campus connector URL is possible but adds 4×
> the OAuth/config surface for no security gain; not recommended.)

---

## 3. Identity → campus resolution (the policy flip)

We **cannot** infer campus from an `@jaipuria.ac.in` email (single domain, no reliable per-campus
subdomain). Campus **must** come from the registry (explicit provisioning). So:

**Change `principal_from_claims`:** remove the blanket Jaipuria→all-campus return; run the registry
lookup for **all** emails:
1. `MCP_FACULTY` env override (break-glass, any domain) — keep.
2. Deny if email is in the `students` roster (`faculty.is_student`) — keep.
3. **`mcp_faculty` lookup** → `{role, campuses, can_generate}` if `active` and not expired.
4. No active row ⇒ **deny** (default-deny) — the reversal of G1.
5. `MCP_FACULTY`/`OAUTH_DEFAULT_CAMPUSES` remain as controlled fallbacks (default deny).

Because default-deny across 4 campuses risks locking out everyone at launch if the roster isn't
populated, gate the whole thing behind a mode flag and roll out in phases (§6).

---

## 4. Data model — harden + extend `mcp_faculty`

DDL (apply via migration):
```sql
-- 4a. Extend the registry with role + capability + lifecycle
alter table public.mcp_faculty
  add column if not exists role text not null default 'faculty',
  add column if not exists can_generate boolean not null default true,
  add column if not exists expires_at timestamptz,
  add column if not exists granted_by text;

alter table public.mcp_faculty
  add constraint mcp_faculty_role_chk
  check (role in ('admin','cross_campus','campus_admin','faculty','viewer'));

-- 'all' only for admin/cross_campus; scoped roles must list valid campuses
alter table public.mcp_faculty
  add constraint mcp_faculty_scope_chk check (
    (role in ('admin','cross_campus') and campuses = '"all"'::jsonb)
    or (role in ('campus_admin','faculty','viewer')
        and jsonb_typeof(campuses) = 'array' and jsonb_array_length(campuses) > 0)
  );

-- 4b. Lock the access-control table down (remove the anon/authenticated write grants)
revoke all on public.mcp_faculty from anon, authenticated;
grant select on public.mcp_faculty to reporting_readonly;   -- server reads grants with this role
-- RLS stays ON; only the reporting_readonly SELECT policy remains. Writes = service_role/admin only.

-- 4c. Audit trail on access changes (security-relevant)
-- trigger → append (email, action, old/new role+campuses, changed_by, at) into mcp_audit or a
-- dedicated mcp_faculty_history table.
```
Notes:
- Application-side, validate `campuses` values against `MCP_CAMPUSES` (reject typos like "delhi").
- `expires_at` enables time-boxed / visiting-faculty access; the resolver treats expired as inactive.
- Registry writes go through **admin only** (service_role via a small admin path or the Supabase
  dashboard) — never the gateway/anon key.

---

## 5. Enforcement changes (small, localized)

1. **`security.principal_from_claims`** — replace the G1 branch with the registry lookup; attach
   `role` + `can_generate` to the principal (behind the mode flag, §6).
2. **`MoodleService` principal** (`supabase_client.py:48-55`) — carry `role` + `can_generate`
   alongside `allowed_campuses`. Scoping itself is unchanged (already correct).
3. **`create_report`** (`tools/actions.py`) — add `if not svc.can_generate: deny` before the budget
   check. Keep the existing campus-scope + rate-budget gates.
4. **Cross-campus tools** (`compare_campuses`) — already auto-scope safely; optionally hide from
   single-campus roles (advisory, not a security fix).
5. **New config:** `MCP_RBAC_MODE` (`off|shadow|enforce`), `MCP_CAMPUSES`, and a deny message
   template ("Access to the Moodle assistant is campus-scoped — ask your campus admin to be added.").

No tool bodies change — the choke point + capability flag do the work.

---

## 6. Rollout (reversing G1 without a launch outage)

| Phase | What | Behaviour | Exit criteria |
| --- | --- | --- | --- |
| **P0** | Apply §4 DDL (role/columns, grant lockdown, audit trigger). Add `MCP_RBAC_MODE`, `MCP_CAMPUSES`. Deploy with `MCP_RBAC_MODE=off`. | No change | migration applied, advisor clean |
| **P1 — shadow** | Resolver computes the *would-be* {role, campus_scope, would_deny} from the registry for every caller and **logs it** (into `mcp_audit` + NR `mcp.campus_scope`), but still serves all-access. `MCP_RBAC_MODE=shadow`. | No change to users; full visibility | ~1–2 weeks of real traffic; review who would be denied/mis-scoped |
| **P2 — seed roster** | Import real faculty per campus into `mcp_faculty` (needs each campus academic office's email list). Bulk upsert; set roles + campuses. | No change | 4 campus rosters loaded + spot-checked against shadow logs |
| **P3 — enforce** | `MCP_RBAC_MODE=enforce`. Unlisted → clean "request access" denial; scoped users see only their campus. | RBAC live | 2 faculty from different campuses verified isolated; admins unaffected |
| **P4 — DB defense-in-depth (optional)** | Per-request campus RLS on student tables (§7). | No functional change | server bug can't leak cross-campus |

**Break-glass:** the `MCP_FACULTY` env override grants access without a deploy or DB write — keep it
for emergencies. **Rollback:** flip `MCP_RBAC_MODE` back to `shadow`/`off`.

---

## 7. DB-layer campus RLS (Phase 4, defense-in-depth — defer past launch)

App-layer scoping is primary and solid; this makes the database itself refuse cross-campus reads even
if the server has a bug. Because all callers share one `reporting_readonly` key, add per-request
context, then RLS keyed to it:
```sql
-- policy sketch on students/courses (join-carried to marks/enrolments/attendance):
using (
  current_setting('request.campus_scope', true) = 'all'
  or campus = any(string_to_array(current_setting('request.campus_scope', true), ','))
)
```
The data layer sets `request.campus_scope` per request (transaction-scoped `SET LOCAL`, or a
PostgREST request GUC). This is the only change that touches the connection model — hence deferred;
scope it once RBAC is stable in production.

---

## 8. Audit & observability

- [x] Record `role` + resolved `campus_scope` (+ bounded shadow outcome) on every call — extends the
  existing `mcp_audit.tool_calls` and the New Relic `mcp.campus_scope` dimension (see
  `docs/NR_ALERTS.md`). The shadow would-deny NRQL alert is documented there.
- Audit every `mcp_faculty` change (§4c) — grant changes are security events.

## 9. DPDP alignment

Campus-scoped, role-based, default-deny is textbook **least privilege + purpose limitation** — exactly
Shiva Sir's data-minimization ask. **Dependency:** the privacy notice (AIA-1392) currently says *"every
verified account can query across campuses"* — that line must change to "access is role-based and
campus-scoped" when P3 lands. Complements the PII pseudonymization plan (AIA-1386).

## 10. Testing / acceptance

- **Unit** (`principal_from_claims`): admin→all; cross_campus→all; noida faculty→["noida"]; unlisted
  Jaipuria→deny; student email→deny; inactive/expired→deny; viewer→`can_generate=false`.
- **Integration:** a Noida faculty gets **zero** Lucknow rows; `compare_campuses` shows only Noida;
  `create_report` denied for a viewer and for out-of-campus students; unlisted user gets the clean
  "request access" error; admin unaffected.
- Matches the AIA-1391 criterion: "per-user faculty and campus scoping — test 2 faculty, different
  campuses."

## 11. Open decisions (need your call)

1. **Default-deny for unlisted Jaipuria users** — confirm (this *is* the G1 reversal). *Recommended:
   yes, via the phased shadow→enforce rollout.*
2. **Roster source** — who supplies each campus's faculty email list? This is the one hard operational
   dependency (P2). Options: academic-office rosters (best), Google Workspace OU/group sync (future
   automation), or self-request + admin approval.
3. **Role set for v1** — minimal `{faculty, cross_campus, admin}`, or ship `campus_admin`/`viewer` too?
   *(Schema includes all; cost is only in seeding.)*
4. **Multi-campus faculty** — anyone teaching across campuses? Supported via a `campuses` list; confirm
   if any exist so P2 captures them.
5. **DB-layer RLS (§7)** — schedule for Phase 4 or skip? *Recommended: schedule post-launch.*

---

## Appendix — immediate hardening items (independent of RBAC)

- [x] **Revoke `anon`/`authenticated` grants on `mcp_faculty`** (§4b) — shipped in
      `sql/2026-09-30_rbac_phase0.sql`; still verify the migration is applied in each environment.
- [ ] Carry over from `RENDER_GO_LIVE.md`: CF bot/WAF skip (AIA-1391, Rajika), privacy notice publish
      (AIA-1392, Shiva), NR alerts (`docs/NR_ALERTS.md`).
- [x] Already done: audit `mcp_audit` RLS lockdown; `report_accuracy` RLS + purge-RPC fix (advisor
      clear); OWASP LLM review; recording live; telemetry (traces+metrics) live; MCP on paid Starter.
