# Moodle MCP — access model + LLM-blind PII (plan)

Extends `docs/RBAC_HARDENING_PLAN.md` with the requirements from 2026-10-01:

> 1. A student must never see another student's data.
> 2. Auth is **Google sign-in only** — nothing heavier (no MFA, no separate logins, no passwords).
> 3. The **LLM never sees** a student's name or unique id — it reasons over an opaque identifier;
>    the harness maps that back to the real person outside the model.
> 4. **Educators vs students** are different access classes.
> 5. A **director** can see all data.
> 6. **Cross/elevated faculty access** is granted on request — the backend is *pinged* to approve.

Grounding (verified): 3,145 students, all with a Google email (3,144 `@jaipuria.ac.in`); campuses
`lucknow/noida/jaipur/indore`. Educator registry = `public.mcp_faculty`. Self-service request/approval
+ campus scoping already shipped (Phases 0–2).

---

## 1. Principles (hard rules)

- **Identity comes from Google, authorization from the backend.** The only auth step is the Google
  sign-in we already have. *Who you are* = the verified Google email; *what you may see* = looked up in
  backend tables (student roster / educator registry). No extra auth is ever added (rule 2).
- **The LLM is PII-blind.** Every student identity that would reach the model — in tool results *and*
  in the user's prompt — is replaced by an opaque token. Real names/ids live only in our code and are
  re-attached for the authorised human *after* the model has produced its answer (rule 3). PII-blindness
  is orthogonal to access scope: a director's LLM still sees tokens; the director (human) sees names.
- **Least privilege by class.** Default to the narrowest scope; widen only by explicit grant (rule 4–6).

---

## 2. User classes & access matrix

| class | who | data scope | tools | how assigned |
| --- | --- | --- | --- | --- |
| **student** | email in the student roster | **only their own record** | self-only: my marks / attendance / trajectory / report | automatic (roster) |
| **faculty** | educator, one or more campuses | their campus(es), cohort-wide | all read tools + create_report | registry / self-request → approval |
| **campus_admin** | program head | their campus(es) | faculty tools (+ future admin extras) | registry |
| **director** (`cross_campus`) | leadership | **all 4 campuses** | all read tools + create_report + cross-campus compare | registry (admin-assigned) |
| **admin** | platform owner | all + manage registry/approvals | everything | registry |
| **pending** | verified, not yet provisioned educator | none (no data) | `request_access` only | automatic fallback |

Students never get cohort/other-student tools; educators never get another campus without a grant.

---

## 3. Classification at sign-in (Google-only, rule 2 + 4)

On a verified Google sign-in, resolve **class** in this precedence (all from backend data, no extra auth):

1. **Educator registry hit** (`mcp_faculty`, active) → that role + scope (faculty/campus_admin/director/admin).
   *(Precedence first, so a TA who is also in the student roster is treated as the educator they were granted.)*
2. **Student roster hit** (`students.student_email` = the Google email) → **student** class, self-scoped to
   that person's `student_id` (resolve the latest snapshot row; emails repeat across batch snapshots).
3. **Verified `@jaipuria.ac.in`, no hit** → **pending** educator (self-service request flow).
4. **Else** → deny.

This replaces today's "student → hard deny": students become self-scoped users, not rejected.

---

## 4. Student self-access (rule 1) — NEW workstream

The current scoping is **campus-based**; students need **row-owner-based** scoping (like the sibling
rehearsal "student MCP", which already uses per-user RLS). Design:

- **Self-scope key:** the caller's own `student_id` (from classification). Every student query is filtered
  to that id — never a campus set.
- **Restricted tool surface:** a student sees only self tools (`my_marks`, `my_attendance`,
  `my_trajectory`, `my_report`). All cohort/roster/other-student tools are **not available** to the student
  class (denied, not just empty). A student passing another id is ignored — the id is taken from their
  identity, never from input.
- **DB defense-in-depth:** per-student RLS so even a server bug can't return another student's rows
  (mirrors the rehearsal student MCP). This needs per-request identity context at the DB (see §7).
- **PII:** a student sees their *own* name (it's theirs) in the rehydrated output, but the LLM still gets
  only a token (rule 3 applies to everyone).

---

## 5. Educator cross/elevated access + the backend "ping" (rule 5–6)

- **Faculty default** = their campus(es) (already enforced).
- **Cross-campus / another campus / a role bump** = the educator calls `request_access` (self-service,
  already shipped) for the extra scope. Directors/admins are assigned directly by an admin.
- **The "ping":** when a request is filed, notify the approvers so it isn't only a silent queue. Options
  (pick one in build): (a) email the admin (reuse the mail pipeline — **respecting the test-recipient
  rule until go-live**), (b) a webhook/Slack to the platform channel, (c) poll `list_access_requests`.
  Recommend a webhook/email to admins on insert, with the queue as the system of record.
- **Approval stays service-role-only** (a leaked read key can't grant), as built.

---

## 6. LLM-blind PII (rule 3) — the core new architecture

**Where PII would reach the LLM:** (a) tool *results* (names, enrolment ids in returned rows), and
(b) the user's *prompt* (they type "how is Aashna doing"). Both must be tokenised.

**Token:** a stable pseudonym per person — `student_ref = "S_" + HMAC(student_id, key)[:8]` (same
HMAC technique the audit ledger already uses). Deterministic, so the same student is the same token
within a tenant; reversible only via the map we hold, never by the model.

**Division of responsibility**
- **MCP (we own):** becomes the *authoritative tokeniser*. In every LLM-facing payload it returns
  `student_ref` in place of `student_name` + `student_id`, and attaches a **side map**
  `_identity: { "S_7f3a": {"name": "...", "student_id": "..."} }` in a conventionally-named field the
  harness strips. Because the MCP has the real values, this is exact and deterministic — no fragile NER.
  Add a helper tool `resolve_identities(names|ids) -> refs` so the harness can tokenise a prompt.
- **Harness (JChat / gateway — Rajika, AIA-1356):** enforces LLM-blindness around the model —
  strips `_identity` before the model sees a tool result, substitutes prompt names→refs *before* the
  call (via `resolve_identities`), and **rehydrates** refs→names in the model's output for the authorised
  human. The MCP→harness hop is first-party/in-region; only the harness→LLM hop is the external boundary.
- **create_report** already keeps the name out of the model and stitches it at render — the proven
  pattern; generalise it to all identity fields and the raw data tools.

**Residual risk to document:** re-identification via quasi-identifiers (marks + campus + trimester +
rank) even with names tokenised — note in the privacy posture; mitigate by not returning rank+campus+
small-n together where avoidable.

---

## 7. Enforcement layers

- **App layer (built):** campus scope for educators; add owner-scope for students; class-based tool gating.
- **DB layer (defense-in-depth):** educator **campus RLS** (`RBAC_HARDENING_PLAN §7`) + student
  **per-student RLS**. Both need the caller's identity/scope carried to the DB per request
  (`SET LOCAL request.*` GUC, or a per-request scoped JWT) — the one change that touches the connection
  model. Schedule after the app-layer student path is stable.

---

## 8. Built vs new

**Built (Phases 0–2):** Google-only auth; educator campus scoping; roles faculty/campus_admin/
cross_campus(director)/admin/viewer; self-service `request_access` + approval; `mcp_faculty` hardened;
shadow mode live.

**New in this plan:**
- **S1 — Student class:** classification precedence (§3) + student principal (self-scope by `student_id`).
- **S2 — Student tool surface:** `my_*` self tools; class-gate cohort tools off for students.
- **P1 — LLM-blind PII (MCP side):** tokenise identity in LLM-facing payloads + `_identity` side map +
  `resolve_identities` tool; generalise the create_report name-omission pattern.
- **P2 — LLM-blind PII (harness side, AIA-1356/Rajika):** strip/substitute/rehydrate around the model.
- **A1 — Backend ping:** notify approvers on `request_access` insert.
- **D1 — DB RLS:** per-student + per-campus RLS (defense-in-depth, last).

Suggested order: **P1 (MCP tokenisation)** and **S1/S2 (student self-access)** first (both MCP-side, high
value), then **A1 ping**, then **P2** with Rajika, then **D1**. All ship dormant-safe behind the existing
`MCP_RBAC_MODE`/flags and the shadow→enforce rollout.

---

## 9. Open decisions

1. **Do students use this MCP directly, or a separate student surface?** (Plan assumes same MCP, student
   class + restricted tools. A separate student-facing server is the alternative.)
2. **Token reversibility scope:** per-request ephemeral map (safest, no stored name↔token) vs a stable
   stored pseudonym table (enables cross-session analytics on tokens). Recommend per-request ephemeral.
3. **Ping channel:** email vs webhook/Slack for approver notifications.
4. **Student PII in create_report:** a student's own report shows their own name (fine); confirm.
5. **Directors:** map "director" to `cross_campus` (all read + report) or full `admin` (also manages
   access)? Recommend `cross_campus` for directors; `admin` reserved for the platform owner.
