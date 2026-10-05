# LLM-blind PII — MCP ↔ harness contract (AIA-1386 / AIA-1356)

> **SUPERSEDED for the default rollout (2026-10-05).** This doc describes the `_identity`
> side-map model, where the MCP emits a reverse map and **trusts the client/harness to strip it**
> before the model. That trust assumption fails for a non-cooperating MCP client (e.g. a generic
> host), so the default control is now **in-server redaction** — `pii.redact()` wired at the
> `GuardMiddleware` chokepoint under `MCP_PII_REDACTION_MODE=off|shadow|enforce`, which emits **no
> reverse map at all**. See `../../moodle-mcp-pii-redaction-plan.md` §0b and `PII_REDACTION_IMPLEMENTATION.md`.
> The `student_ref` token is identical in both. **Harness impact (AIA-1356):** the harness no longer
> needs steps 1 (strip `_identity`) or 4 (rehydrate in the answer) — rehydration moves to the
> auth-gated server-side report renderer. `resolve_identities` (step 2) and ref→id rewrite on
> tool-call-in (step 3) remain useful. `tokenise_response`/`_identity` stay in the code for this
> legacy option but are not the recommended wiring.

The model must never see a student's name or enrolment id; it reasons over opaque **refs**, which the
harness rehydrates for the authorised human outside the model. The **MCP is the authoritative
tokeniser** (it has the real values → exact, no NER). This doc is the contract the harness integrates
against. PII-blindness is orthogonal to authorisation — a director's model still sees refs.

## Token
`student_ref = "S_" + HMAC_SHA256(key, lower(student_id))[:12]` — stable (same id → same ref within a
key), opaque, one-way. The MCP holds the key (`MCP_PII_HMAC_KEY`, else the audit key); the harness does
**not** need it — it gets refs from the MCP (below), never computes them.

## MCP-side surface (shipped, dormant until `MCP_PII_TOKENIZE=true`)
1. **`resolve_identities(students: [name|id,…])` tool** — maps each to
   `{query, resolved, student_ref, student_id, campus}`, scoped to what the caller may see (students
   get only themselves). Use it to tokenise the **prompt**: when the user types a name, swap it for the
   ref before the model, and keep `ref↔student_id` privately.
2. **Response side-map** — when `MCP_PII_TOKENIZE=true`, every tool result has its identity fields
   (`student_id`, `student_name`, `student_email`, `name` on student records) replaced by the ref, plus
   a top-level **`_identity`**: `{ ref: {student_id, student_name, …} }`.

## Harness responsibilities (AIA-1356, client-harness side — the part the MCP can't do)
1. **Strip `_identity`** from every tool result **before** passing the result to the model (keep it in a
   per-turn, in-memory vault keyed by ref). The model sees only refs.
2. **Prompt in:** resolve user-typed names → refs (via `resolve_identities`); send refs to the model.
3. **Tool calls out:** when the model calls a tool with a ref, rewrite ref → real `student_id` (from the
   vault) before invoking the MCP.
4. **Answer out:** rehydrate refs → names in the model's output for the authorised human.
5. Discard the vault at end of turn (per-request ephemeral map — nothing stored).

## Flags
- `MCP_PII_TOKENIZE` (default **false**) — turns on response tokenisation. Keep OFF until the harness
  strips `_identity` and rehydrates, or clients would show refs and the map would reach the model.
- `MCP_PII_HMAC_KEY` (optional; falls back to `MCP_AUDIT_HMAC_KEY`).

## Rollout
`resolve_identities` + the tokeniser are live-but-dormant now (flag off, zero impact). Flip
`MCP_PII_TOKENIZE=true` **only after** the harness implements steps 1–5, together, in a shadow test.
`create_report` already keeps the name out of the model and stitches it at render — the proven pattern
this generalises.

## Residual risk
Re-identification via quasi-identifiers (marks + campus + trimester + small cohort) survives name
tokenisation — mitigate by not returning rank + campus + small-n together where avoidable; document in
the privacy posture.
