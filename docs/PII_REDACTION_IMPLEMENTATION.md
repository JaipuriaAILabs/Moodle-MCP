# In-server PII redaction — implementation reference

The DPDP primary control for the LLM path. Pseudonymises every student identity in a tool result to
a deterministic, opaque `student_ref` **before it leaves the server**, with **no reverse map emitted
to the client**. Real names reappear only in the auth-gated, server-side report renderer — never in
anything a model reads. Design + rationale: `../../moodle-mcp-pii-redaction-plan.md` (see §0b for how
it reconciles with this repo). Supersedes the `_identity` harness-rehydration contract
(`PII_TOKENIZATION_CONTRACT.md`).

## What it does
- **Token:** `student_ref = "S_" + HMAC_SHA256(MCP_PII_HMAC_KEY, lower(student_id))[:12]`
  (`pii.student_ref`). Deterministic (same student → same ref), opaque, one-way.
- **Structured fields:** identity fields on any record that *also* carries a `student_id` —
  `student_id, student_name, student_email, name, full_name, first_name` — are replaced by the ref.
  A `name` with no `student_id` (course/subject names) is left alone. Numbers (marks, attendance,
  campus) are untouched.
- **Free-text sweep (exact, no NER):** because the tokeniser knows the exact names/ids/emails in the
  result, it word-boundary-replaces them inside free-text strings (e.g. a narrative that says
  "Rahul Sharma … Rahul improved") with the ref — full names and their individual parts.
- **Leak guard:** a final regex net for email, Indian phone, Jaipuria enrolment number, PAN and
  Aadhaar (`[EMAIL]`, `[PHONE]`, `[STUDENT_ID]`, `[PAN]`, `[AADHAAR]`).
- **No `_identity` side-map** is ever produced.
- **Framework wrapper coverage:** real FastMCP `ToolResult` values retain their runtime types while
  `structured_content`, serialized `TextContent`, embedded text resources and `meta` are redacted.

## Where it runs
`GuardMiddleware.on_call_tool` (`security.py`), immediately after `call_next`, before the audit write
and the return. Every registered tool is covered by construction — no per-tool opt-in. Redaction runs
**before** `record_tool_call(result=…)`, so the audit ledger stores tokenised results.

## Flag — `MCP_PII_REDACTION_MODE`
| value | behaviour |
|---|---|
| `off` (library/dev default) | no redaction |
| `shadow` | serve the **raw** result unchanged, but log `pii.shadow tool=… ids=N freetext=N leak=N` (counts only, no values) — validate coverage against real traffic |
| `enforce` | return the **redacted** result; the client (and any model) sees only refs |

- **Requires a dedicated `MCP_PII_HMAC_KEY`** when `shadow`/`enforce` (boot fails otherwise — no
  fallback to the audit key; same reasoning as `MCP_PII_TOKENIZE`).
- **Fail-open in shadow** (never perturbs traffic), **fail-closed in enforce** (a redaction bug denies
  the call rather than risk a leak).
- Production Render and Cloudflare manifests set `enforce`; startup fails unless the dedicated key
  exists and is at least 32 characters.

## Rollout
1. Set a stable, dedicated `MCP_PII_HMAC_KEY` (≥32 chars) in Render and Cloudflare secrets before
   deploying the manifests.
2. Run the synthetic + real-`ToolResult` suite and staging RBAC × PII canary.
3. Deploy with `enforce`. Use `shadow` only as an explicitly time-bounded diagnostic rollback; it
   serves raw data and is not an acceptable steady-state production privacy control.

## Tests
`../tests/test_pii_redaction.py` (plain-assert; `../moodle-agent/.venv/bin/python tests/test_pii_redaction.py`):
canary (no raw identifier survives anywhere, incl. free-text), numeric integrity, determinism,
real `ToolResult`/content/resource/meta coverage, generic Indian identifier patterns, no `_identity`,
shadow vs enforce, mode normaliser and scalar/empty safety. The activity-capture integration test
also proves middleware serves and audits the redacted wrapper.

## TrueFoundry (generation + managed MCP defence)

The MCP server itself makes no model call; `create_report` delegates to `moodle-agent`. The production
agent now uses `[[FIRST]]` in every report-generation request/retry, rehydrates the name only after
validation, and anonymises validation-panel inputs. Every LLM client routes through TrueFoundry with
a dedicated Virtual Account Token, bilateral PII guardrails, non-streaming responses and gateway body
logging disabled. Production fails closed when the gateway or guardrail contract is missing. Direct
provider fallback is development-only.

The next tenant-admin step is to register Moodle MCP in TrueFoundry MCP Gateway and attach the same PII
mutation plus Secrets Detection to MCP post-tool results. Add Prompt Injection to LLM input and
Cedar/OPA authorization to MCP pre-tool as defence in depth. The full resource inventory, rollout and
acceptance gates are in `PII_PRODUCTION_HARDENING_PLAN.md`.

The older Portkey/offline one-pager path remains a reference and rollback artifact; it is not the
production `create_report` execution path and must not be treated as the production PII boundary.
