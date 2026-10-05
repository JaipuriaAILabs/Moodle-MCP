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
- **Leak guard:** a final email/phone regex net (`[EMAIL]`/`[PHONE]`). It never matches an `S_` ref
  (no `@`, not a 10-digit run).
- **No `_identity` side-map** is ever produced.

## Where it runs
`GuardMiddleware.on_call_tool` (`security.py`), immediately after `call_next`, before the audit write
and the return. Every registered tool is covered by construction — no per-tool opt-in. Redaction runs
**before** `record_tool_call(result=…)`, so the audit ledger stores tokenised results.

## Flag — `MCP_PII_REDACTION_MODE`
| value | behaviour |
|---|---|
| `off` (default) | no redaction; current behaviour |
| `shadow` | serve the **raw** result unchanged, but log `pii.shadow tool=… ids=N freetext=N leak=N` (counts only, no values) — validate coverage against real traffic |
| `enforce` | return the **redacted** result; the client (and any model) sees only refs |

- **Requires a dedicated `MCP_PII_HMAC_KEY`** when `shadow`/`enforce` (boot fails otherwise — no
  fallback to the audit key; same reasoning as `MCP_PII_TOKENIZE`).
- **Fail-open in shadow** (never perturbs traffic), **fail-closed in enforce** (a redaction bug denies
  the call rather than risk a leak).

## Rollout
1. In the Render dashboard set `MCP_PII_HMAC_KEY` (≥32 chars, dedicated) and `MCP_PII_REDACTION_MODE=shadow`.
2. Watch `pii.shadow` log counts over 2–3 days of real faculty traffic; confirm identifiers are being
   found and nothing legitimate is over-matched.
3. Flip `MCP_PII_REDACTION_MODE=enforce`. The chat surface then shows `S_…` refs; real names appear
   only via the rendered report link.

## Tests
`../tests/test_pii_redaction.py` (plain-assert; `../moodle-agent/.venv/bin/python tests/test_pii_redaction.py`):
canary (no raw identifier survives anywhere, incl. free-text), numeric integrity, determinism,
`S_`-ref-not-leak-guarded, no `_identity`, shadow vs enforce, mode normaliser, scalar/empty safety.

## Portkey (generation path)
The MCP server makes no live LLM call, so Portkey has nothing to attach to there. The narrative is
generated in `moodle-agent` (remote) and in the offline `onepager/build_report.py` (OpenRouter).
Portkey PII redaction is **one-way/irreversible**, so it is a *backstop*; the primary control is
generating about a token/pseudonym and stitching the real name at render (the `create_report` pattern).

**`onepager/build_report.py` — IMPLEMENTED (flag-gated on `PORTKEY_API_KEY`):**
- **Step 0:** when `PORTKEY_API_KEY` is set, the call routes through `https://api.portkey.ai/v1`
  (`@openrouter/<model>`, `x-portkey-api-key`, optional `x-portkey-config`, PII-free metadata). Unset
  = today's direct-OpenRouter behaviour, unchanged.
- **Step 4 (token-then-stitch):** the student's real name never enters the prompt — a fixed pseudonym
  is sent and `stitch_name()` restores the real first name after generation, before the server-side
  render. The saved `narrative.json` keeps the pseudonym (no real name on disk in Portkey mode).
- **Guardrails/config (set up in Portkey by the other session):** config **`pc-moodle-cf8b65`** runs
  four **Regex Replace** guardrails (phone `pg-moodle-fb0470`, aadhaar `pg-moodle-e7cc56`, PAN
  `pg-moodle-81e6ff`, rollno `pg-moodle-0da71f`) on both prompt and reply, Deny+Async off (redact and
  continue). **No Pro PII (name) guardrail — it is plan-locked** → token-then-stitch is *mandatory*,
  not optional. **Verify from the response** (`_log_guardrails` logs hook results + `x-portkey-*`
  headers): the Portkey Logs UI is over the org's 10k/month quota and is dropping logs.
- **To check:** the `rollno` regex `\b[A-Z]{2}\d{2}[A-Z]{2}\d{3}\b` was only validated against
  `JN25MM002` — confirm it against a real enrolment id.
- Tests: `onepager/test_portkey_redaction.py` (17/0) — routing, no-real-name-in-prompt, stitch.

**`moodle-agent` (remote report backend) — hand-off:** apply the same pattern (route its LLM call
through Portkey config `pc-moodle-cf8b65`; keep names out of the prompt). Not in a repo available here.

## Not yet built (Phase 2/3)
Redis `TokenVault` (not required — `student_ref` is deterministic, the renderer recomputes id→ref),
allowlist default-DROP field policy (needs the per-tool field inventory from shadow first), Presidio
NER, renderer rehydration + auth-gating (cross-repo, `moodle-agent`), Portkey MCP-Gateway backstop.
