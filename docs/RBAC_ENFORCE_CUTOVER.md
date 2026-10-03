# RBAC enforce cutover — runbook

Flips the Moodle MCP from **shadow** (all-access, logging only) to **enforce** (campus/role-scoped
from the `mcp_faculty` registry; unlisted users get a self-service *pending* session). One env change
+ a few verifications. Fully reversible.

Prereqs (already true): Phase 0–2 deployed; `MCP_RBAC_MODE=shadow` currently set on the service.

---

## Step 0 (recommended) — pre-approve the known faculty first
So most people never hit the request flow. Pick ONE:

**A. Paste the lists to me** — "Noida: a@…, b@… · Lucknow: … · central/leadership: rajika@…, shiva@…"
and I load them into `mcp_faculty` (validated) before you flip.

**B. Do it yourself** — fill `docs/faculty_roster_template.csv`, then:
```bash
SUPABASE_URL=https://sadbfvfcmmxgtatfjfmc.supabase.co \
SUPABASE_SERVICE_ROLE_KEY='<service-role JWT from Supabase → Settings → API>' \
python3 scripts/seed_faculty_roster.py your_roster.csv --dry-run   # preview
# then drop --dry-run to load
```

**C. Skip** — go straight to enforce; everyone self-onboards via `request_access` → you approve.

---

## Step 1 — flip the mode (Render)
1. https://dashboard.render.com → **Moodle-MCP** → **Environment**.
2. Find **`MCP_RBAC_MODE`** (currently `shadow`) → click **Edit** → change value to **`enforce`**.
3. While there, confirm:
   - **`OAUTH_DEFAULT_CAMPUSES`** is `none` or **not present** (must NOT be `all`/a list, or unlisted
     users would get a default grant instead of pending). Default is none — only act if it's set.
   - **`MCP_SELF_SERVICE_ACCESS`** — leave unset/`true` for graceful pending (recommended). Set
     `false` only if you want unlisted users hard-denied instead of pending.
4. **Save, rebuild, and deploy.** ~1–2 min.

## Step 2 — verify the boot
Render → **Logs** → search `RBAC`. Expect:
`per-campus RBAC mode: enforce …` and `RBAC ENFORCE — access is role/campus-scoped …`.

## Step 3 — verify behaviour (via your connector: Claude.ai / Jaipuria OS)
- **You (admin):** `whoami` → `role: admin`, `campuses: all`; a data query works.
- **A provisioned campus faculty:** sees only their campus; a query for another campus returns nothing.
- **An unprovisioned user:** `whoami` → `role: pending` + a note; data tools say access denied;
  `request_access` with their campus returns `status: pending`.

## Step 4 — work the approval queue
As requests arrive:
- See them: admin `list_access_requests` (via your connector), **or**
  ```sql
  select email, name, requested_role, requested_campuses, created_at
  from public.mcp_access_requests where status='pending' order by created_at;
  ```
- Approve one (Supabase SQL editor, service role — NOT the server key):
  ```sql
  -- set :email and :admin_email
  with r as (select * from public.mcp_access_requests where email = :'email' and status='pending')
  insert into public.mcp_faculty (email, name, role, campuses, can_generate, active, granted_by)
  select email, name, requested_role, requested_campuses,
         (requested_role <> 'viewer'), true, :'admin_email' from r
  on conflict (email) do update set role=excluded.role, campuses=excluded.campuses,
     can_generate=excluded.can_generate, active=true, updated_at=now();
  update public.mcp_access_requests set status='approved', decided_by=:'admin_email',
     decided_at=now(), updated_at=now() where email = :'email';
  ```
  (Or just tell me who to approve and I'll do it.) The approved user gets access on their next query
  (grants are cached ~60s).

## Step 5 — privacy notice
Update the AIA-1392 notice line "every verified account can query across campuses" → "access is
role-based and campus-scoped." (I can edit the draft.)

---

## Rollback (instant)
Render → Environment → `MCP_RBAC_MODE` → back to **`shadow`** → Save. Returns to all-access immediately;
no data change. (Seeded `mcp_faculty` rows are harmless in shadow.)
