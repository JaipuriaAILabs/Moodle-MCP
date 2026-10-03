# Moodle MCP — OWASP Top 10 for LLM Applications (2026) security review

Reviewed against the OWASP GenAI **LLM Top 10 2026** (skill `owasp-llm-top10-2026`).
Scope: the Moodle MCP server (`moodle-mcp`) + its one LLM-touching path (`create_report`
→ `moodle-agent` → OpenRouter). The MCP is a **tool provider**, not an agent: it holds no
model except via `create_report`. Grounded in the code as of 2026-09-25.

**Core stance:** authority lives in deterministic code (campus AND-filter, faculty deny,
budget cap), model output is untrusted, hidden context is assumed public. Findings are
"fixed" only by controls that **bound blast radius**, not ones that merely reduce attack success.

## Verdict
No critical chain. The MCP's read surface is deterministically scoped and parameterized;
the single write path is non-destructive, HMAC-proxied and budget-capped. The most
dangerous *live* gap is **operational, not code**: recording is OFF (no who-accessed-whom
audit trail) under an accepted all-access model where any verified account can read any
student — so misuse is currently untraceable. The main *data* exposure is student marks
flowing to an external LLM in `create_report` (DPDP), tracked in AIA-1386.

## Findings (highest severity first)

### [MEDIUM] Recording off → no accountability under all-access
- OWASP: LLM02:2026 Sensitive Information Disclosure (accountability) · CWE-778 (insufficient logging)
- Where: prod env — `SUPABASE_AUDIT_KEY` unset; `mcp_audit.tool_calls` = 0 rows
- Attack: any verified Jaipuria account reads any student's marks/attendance across all campuses; with the ledger off there is no record of who accessed whom — undetectable misuse.
- Fix: set `SUPABASE_AUDIT_KEY` (+ `MCP_CAPTURE_*`) — backend RPC/RLS already fixed + proven. Control: **detection** (the load-bearing control for the accepted G1 model).

### [MEDIUM] Student marks sent to an external LLM (create_report)
- OWASP: LLM02:2026 Sensitive Information Disclosure · CWE-200
- Where: `moodle-agent/app/onepager/build.py` `llm_narrative()` → `openrouter.ai`
- Attack: marks + campus + batch + trimester leave to OpenRouter → underlying provider; retained/trained-on unless the route forbids it; quasi-identifiers can re-identify even without the name.
- Fix: (1) zero-retention/no-training OpenRouter routes + gateway logging off; (2) AIA-1386 reversible pseudonymization — **name already removed from the prompt** (commit `15f55c9`, staged), extend to minimize fields; (3) DPA/cross-border. Control: **reduces success + governance**.

### [MEDIUM — ACCEPTED (G1)] Excessive read agency across all campuses
- OWASP: LLM03:2026 Excessive Agency · CWE-732/284
- Where: `security.principal_from_claims` grants every `jaipuria.ac.in` account all campuses
- Attack: a student account (Phase 2) reads any other student's full record; `create_report` for anyone.
- Bounded by: read-only surface (all tools except `create_report`), `reporting_readonly` least-priv DB identity, campus AND-filter (`apply_campus`), the faculty/student-roster deny, `create_report` per-principal cost cap, no destructive/email actions. **Data-owner has formally accepted this** (see GAP_ANALYSIS G1). Residual controls that must hold: audit (finding #1) + privacy notice. Control: **bounds blast radius** (mostly in place; audit is the gap).

### [LOW→MED] Untrusted Moodle free-text into the report prompt
- OWASP: LLM01:2026 Prompt Injection (indirect) · CWE-1427
- Where: `build.py` FACTS block interpolates student/subject fields sourced from Moodle
- Attack: a crafted student name / section label carries instructions into the narrative LLM (low likelihood — institutional data — but no invisible-unicode strip or provenance marking).
- Fix: strip invisible Unicode (U+E0000–E007F, U+200B–200D, U+FE00–FE0F) at ingest; keep FACTS in a clearly-labelled data section (spotlighting). The existing number-validator already bounds fabricated numbers. Control: **reduces success** (+ validator bounds).

### [LOW] Rate/cost limits are per-instance until Redis is set
- OWASP: LLM06:2026 Unbounded Consumption · CWE-770
- Where: `security.SharedRateLimiter` + `_enforce_report_budget`; `MCP_REDIS_URL` unset
- Attack: horizontal scaling makes per-principal/IP limits and the create_report budget diverge across instances → limits become per-instance.
- Fix: set `MCP_REDIS_URL` before running >1 instance (fine on a single instance today). Control: **bounds blast radius**.

### [LOW] Supply chain not fully pinned
- OWASP: LLM04:2026 Supply Chain · CWE-1104
- Where: `requirements.txt` (`>=` ranges except `fastmcp==2.14.7`), `Dockerfile` base `python:3.12-slim` (tag, not digest); no SBOM
- Fix: pin remaining deps + base image by digest; generate an SBOM/AIBOM; CI already byte-compiles + tests. Control: **reduces success**.

### [LOW] LLM-generated narrative beyond numbers is unverified
- OWASP: LLM07:2026 Misinformation · CWE-1426
- Where: `create_report` narrative
- Mitigated: `validate()` enforces "every number copied from FACTS" (a real Claim→Check for numbers) + attendance-direction check. Prose claims aren't independently verified. Fix: AIA-1380 faithfulness-panel hardening. Control: **reduces success** (number validator is load-bearing).

## Pass / N/A
- **LLM08 Hidden Context Exposure — PASS.** No secrets in prompts/tool descriptions; authz is enforced in deterministic code (`principal_from_claims`, campus filter, roster deny), never by prompt text.
- **LLM10 Improper Output Handling — PASS (MCP).** All DB access is parameterized (Supabase query builder; no raw SQL/`eval`/`exec`/`subprocess`). The report HTML renderer (`moodle-agent`) uses `esc()` + `jdump` (escapes `</`) + `Cache-Control: no-store` + no-script CSP (verified, GAP G5). *Client-side note:* the calling client harness must sanitize markdown / disable auto-fetching image renderers (LLM10 exfil via URL) — a client item, not the MCP's.
- **LLM05 Data & Model Poisoning — N/A.** No training/fine-tuning; data from trusted Moodle snapshots.
- **LLM09 Vector & Embedding Weaknesses — N/A.** No vector DB / embeddings in the MCP.

## Coverage table
| Risk | Result |
|---|---|
| LLM01 Prompt Injection | Finding (Low→Med) |
| LLM02 Sensitive Info Disclosure | Finding (Medium ×2) |
| LLM03 Excessive Agency | Finding (Medium — accepted G1) |
| LLM04 Supply Chain | Finding (Low) |
| LLM05 Data/Model Poisoning | N/A |
| LLM06 Unbounded Consumption | Finding (Low) |
| LLM07 Misinformation | Finding (Low — mitigated) |
| LLM08 Hidden Context Exposure | Pass |
| LLM09 Vector/Embedding | N/A |
| LLM10 Improper Output Handling | Pass (MCP); client note |

## Top 3 fixes (priority; blast-radius first)
1. **Turn on recording** (`SUPABASE_AUDIT_KEY`) — the accountability control the accepted all-access model depends on. *(ops)*
2. **Zero-retention OpenRouter routes + AIA-1386 pseudonymization** — the student-data-to-LLM exposure. *(ops + the staged create_report change)*
3. **Cloudflare WAF skip for `/mcp`** (availability/DoS-adjacent to LLM06) **+ set `MCP_REDIS_URL`** before horizontal scale. *(ops)*

> Also relevant beyond this list: the MCP sits behind Cloudflare in front of a Render origin — a bot/managed-challenge rule on `/mcp` can silently break connector traffic (see RENDER_GO_LIVE.md §0). Migrating to a first-party CF Worker (AIA-1391) dissolves it.
