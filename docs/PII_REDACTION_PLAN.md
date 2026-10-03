# PII redaction / pseudonymization for the LLM path (DPDP) — plan

**Origin:** Shiva Sir (2026-09-24): under DPDP, student/personal data should not go to LLMs
"discretely"; after the initial MCP release, implement a redaction mechanism. He noted
OpenRouter can redact PII from requests, and raised the open question: **how do we
re-insert it in the response?** Timing: do this as user inflow grows (Phase 2, post-launch).

This plan answers the reinsertion question and stages the work.

---

## 1. The core tension (why "just redact everything" doesn't work)
The Moodle MCP's purpose is reports **about a specific student's marks/attendance**. So:
- **Direct identifiers** (name, enrolment id, email, phone) → **can** be pseudonymized and put
  back. The LLM can reason about `[[S1]]` and never see "Aashna Gupta".
- **The performance payload** (78 in Marketing, 60% attendance) → **cannot** be redacted; it
  *is* the content the LLM must reason over. But marks tied only to a **token** (not a real
  identity) are pseudonymized data — materially lower DPDP risk than directly-identifiable PII.

So the mechanism is **reversible pseudonymization of direct identifiers**, not blanket redaction.

## 2. Why OpenRouter/Portkey built-in redaction is not enough
Off-the-shelf gateway PII guardrails (OpenRouter, Portkey, Patronus/Aporia) are almost always
**one-way**: `name → [REDACTED]` or a category label, for safety/logging. That **destroys the
value with no way to rehydrate** — which is exactly why "how do we reinsert it" is hard. It's
also generic ML/NER: it mis-detects and mangles Indian names, and offers no stable handle to
map back. **Conclusion:** the gateway's redaction can be a *secondary* safety net, but the
primary mechanism must be a **custom reversible token map** we control.

## 3. Our unfair advantage: we KNOW the exact PII values
Unlike a generic PII proxy that must *guess* what's PII, the MCP **returns the identifiers from
the DB**, so we know the exact strings ("Aashna Gupta", "JN25MM002", the email). That means:
- **Exact, deterministic tokenization** (string replace of known values) — near-100% precision,
  no NER guesswork.
- **Reliable reinsertion** — each identifier maps to one stable token and back.
- Best enabled by the **MCP tagging its PII fields** in tool output (mark which fields are
  identifiers), so the redaction layer tokenizes tagged values exactly, and falls back to NER
  only for free-text (the user's prompt).

## 4. The reinsertion mechanism (answering Shiva's open question)
Request-scoped, in the harness→LLM path:
1. **Inbound:** scan the outgoing LLM request (user prompt **and** MCP tool results) for PII.
   Replace each unique value with a stable opaque token — e.g. `⟦S1⟧`, `⟦ID1⟧` — and store
   `{token → original}` in a **request-scoped, in-memory vault** (keyed by request id, never
   logged, discarded after the response).
2. **Instruct the model** (system prompt) to **preserve any `⟦…⟧` placeholders verbatim** and
   never invent or translate them.
3. **Model** reasons over tokens: "⟦S1⟧ is strong in Marketing (78) but weak in Finance (45)…"
4. **Outbound:** scan the response, replace tokens → originals from the vault → the user sees
   the real name. Discard the vault.

Making reinsertion **robust** (the actual engineering):
- **Token format** must survive tokenization and be unlikely to be paraphrased — distinctive
  delimiters + short id (test against the real models). Consistent value→token within a request
  so the narrative stays coherent.
- **Rehydration misses** (model dropped/mangled a token): fall back gracefully ("the student"),
  and emit a `pii.rehydration_miss` metric so misses are visible, not silent.
- **Streaming**: the harness streams tokens; a placeholder can span chunks. Buffer on the `⟦…⟧`
  boundary before flushing, or rehydrate on a small trailing window. (This is the fiddliest part
  and the reason to prototype early.)
- **Structured alternative** (more robust than free-text echo): have the model return structured
  output referencing tokens, and rehydrate fields — good for `create_report`.

## 5. A targeted quick win for the biggest PII path — `create_report`
`create_report` produces a **templated** narrative. For that path you can keep the name out of
the LLM **entirely**: generate the narrative about `⟦S1⟧` / "the student", then the
moodle-agent stitches the real name into the final report template at render time. No general
proxy needed for the highest-volume identifier exposure — a clean, use-case-specific redaction.

## 6. Architecture — where it lives
The DPDP-sensitive hop is **harness → OpenRouter → model** (external). MCP→harness is first-party,
in-region (fine to carry real data). So the mechanism belongs in **the harness's model-call pipeline
or the gateway it calls** — Rajika's domain, tied to **AIA-1356** (Portkey guardrails).
Options, best first:
- **A. Reversible pseudonymization middleware in the harness's LLM path** (a request/response
  hook, or a thin proxy the harness points at instead of Portkey directly). Full control of the token
  vault + streaming. **Recommended.**
- **B. Portkey custom guardrail pair** (before/after hooks) — only if Portkey hooks can share a
  request-scoped store; most hook systems are stateless, so the vault is awkward. Usable as the
  secondary net (irreversible detection/alerting), not the reversible map.
- **C. In the MCP** — rejected as the primary: the MCP isn't in the LLM response loop, so it
  can't rehydrate. Its role is to **tag PII fields** (§3) to make A/B exact.

## 7. Governance layer (cheaper than engineering, do around launch)
Independent of the token mechanism, these cut DPDP risk immediately:
- **Zero-retention + no-training LLM routes** — configure OpenRouter/Portkey to only use
  providers with no-logging / no-training, and disable prompt logging at the gateway.
- **DPA + cross-border**: confirm processor agreements and DPDP-permitted transfer for the
  model providers; prefer in-region where possible.
- **Data minimization** in tool outputs — don't return more identifiers/fields than the task
  needs.
- **Notice + consent** — extend `docs/PRIVACY_NOTICE.md` to state that pseudonymized performance
  data is processed by AI providers; keep raw PII + audit in the India-region Supabase (already).

## 8. Phased plan
- **Phase 0 — now / at launch (governance, low effort):** §7 — zero-retention routes, gateway
  logging off, DPA/cross-border check, minimization, notice update. *(You + Rajika)*
- **Phase 1 — as inflow grows (the mechanism):** MCP tags PII fields (§3); build the reversible
  pseudonymization middleware in the harness's LLM path (§4 option A); ship the `create_report`
  template quick win (§5) first as the highest-value, lowest-risk slice. *(Rajika + Me for MCP
  tagging)*
- **Phase 2 — hardening:** streaming rehydration, rehydration-miss metrics + fallback, a
  **leakage eval** (assert no raw identifier reaches the model; assert reinsertion accuracy),
  and an audit line per request of *which* identifiers were tokenized (not the values). Fold the
  eval into AIA-1356. *(Me + Rajika)*

## 9. Risks / open items
- **Re-identification via quasi-identifiers**: marks + campus + trimester + rank could re-identify
  a student even without the name. Pseudonymization reduces but doesn't eliminate; document as
  residual risk. True elimination would need suppression/aggregation that breaks the reports.
- **Token mangling / streaming boundaries** — the main engineering risk (§4); prototype early.
- **Latency + cost** — two extra scan passes per request; negligible vs LLM time.
- **Coverage of the user's free-text prompt** — students may type a name; NER needed there
  (lower precision than tagged tool fields) — accept + monitor, or tokenize against the known
  cohort roster.

## 10. One-line summary
Redact by **reversibly pseudonymizing the identifiers we already know** (not the marks), with a
**request-scoped token vault** that rehydrates the response in the harness's LLM path; start with
governance (zero-retention routes + notice) at launch and the `create_report` template trick,
then generalize as inflow grows. Ties to **AIA-1356**.
