# Portkey PII redaction — `moodle-agent` hand-off (AIA-1356 / DPDP)

**For:** whoever owns `moodle-agent` (the report backend at `reports.tryrehearsal.ai`). That repo is not
available in the Moodle-MCP workspace, so this is a spec to apply there, not a patch. It mirrors the
working reference already shipped in **`onepager/build_report.py`** (see `docs/PII_REDACTION_IMPLEMENTATION.md`
and the test `onepager/test_portkey_redaction.py`).

## Why
`moodle-agent` generates the `create_report` narrative with an LLM, and today the student's real name
(and possibly enrolment id) goes into that prompt. Under DPDP (Shiva Sir) student PII must not reach
LLMs. The fix is two moves, both flag-gated so "off" = today's behaviour:

1. **Route the LLM call through Portkey** so the redaction guardrails run (config `pc-moodle-cf8b65`).
2. **Token-then-stitch:** never put a real identifier in the prompt — send a token/pseudonym, stitch the
   real name back when the report is rendered (server-side, not an LLM input).

> **Portkey's name (Pro PII) guardrail is plan-locked** on this account, so the gateway catches only
> phone/Aadhaar/PAN/roll-no (the four regex guardrails). It will **not** catch names. So step 2 is
> **mandatory** — Portkey alone does not protect names, and its redaction is one-way anyway.

## The Portkey setup that already exists (don't recreate)
Config **`pc-moodle-cf8b65`** runs these on both prompt and reply, Deny+Async off (redact-and-continue):

| Guardrail | ID | Replaces |
|---|---|---|
| phone | `pg-moodle-fb0470` | `[PHONE]` |
| aadhaar | `pg-moodle-e7cc56` | `[GOVT_ID]` |
| PAN | `pg-moodle-81e6ff` | `[GOVT_ID]` |
| roll-no | `pg-moodle-0da71f` | `[STUDENT_ID]` |

## Step 0 — route the generator's LLM call through Portkey
Flag-gate on the presence of `PORTKEY_API_KEY`. When set:
- **Base URL** → `https://api.portkey.ai/v1` (instead of `https://openrouter.ai/api/v1`).
- **Model** → prefix the current model with `@openrouter/`, e.g. `@openrouter/anthropic/claude-sonnet-4.5`.
- **Headers** → `x-portkey-api-key: $PORTKEY_API_KEY`, `x-portkey-config: pc-moodle-cf8b65` (only if set),
  `x-portkey-metadata: {"app":"moodle-agent"}` (**never** names/emails/ids in metadata).
- **Remove the OpenRouter `Authorization` header** — Portkey holds the OpenRouter key (added once in
  Portkey → Model Catalog → `@openrouter`).

OpenAI-SDK shape:
```python
from openai import OpenAI
import os

use_portkey = bool(os.getenv("PORTKEY_API_KEY"))
if use_portkey:
    client = OpenAI(
        api_key="unused",                      # Portkey uses its stored OpenRouter key
        base_url="https://api.portkey.ai/v1",
        default_headers={
            "x-portkey-api-key": os.environ["PORTKEY_API_KEY"],
            **({"x-portkey-config": os.environ["PORTKEY_CONFIG_ID"]} if os.getenv("PORTKEY_CONFIG_ID") else {}),
            "x-portkey-metadata": '{"app":"moodle-agent"}',
        },
    )
    model = f"@openrouter/{model}"
else:
    client = OpenAI(api_key=os.environ["OPENROUTER_API_KEY"], base_url="https://openrouter.ai/api/v1")
resp = client.chat.completions.create(model=model, messages=[...])
```
Env: `PORTKEY_API_KEY=pk-…` (copy from Portkey → API Keys **yourself**; do not paste keys into forms or
commit them), `PORTKEY_CONFIG_ID=pc-moodle-cf8b65`. You can deploy with the config id empty — the
guardrails switch on the moment it's set.

## Step 4 — token-then-stitch (the part that actually protects names)
The real name/id must **not** appear in the prompt. Pattern (identical to `onepager`):
1. Before building the prompt, pick a **fixed pseudonym** for the student (a distinctive, name-shaped
   placeholder, e.g. `"Aarav"`), and keep the real values aside.
2. Build the prompt with the pseudonym everywhere the name would go. Do **not** include the enrolment id,
   email, or phone in the prompt at all (the report doesn't need them in the narrative).
3. Call the model (now through Portkey).
4. **Stitch** the pseudonym back to the real first name in the returned narrative — word-bounded
   (`\bAarav\b` → real name) so `"Aarav's"` works and other words aren't corrupted — **before** rendering.
5. The real name appears only in the **rendered report** (HTML/PDF / the `reports.tryrehearsal.ai/s/…`
   page) — that is server-side output, not an LLM input, so it's fine. Keep any stored intermediate
   (a saved narrative JSON) in the **pseudonymised** form.

Reference implementation (copy the shape): `onepager/build_report.py` → `PSEUDONYM`, `stitch_name()`, the
`first = PSEUDONYM if _portkey_enabled() else real_first` line, and the `_call()` routing block.

Direct identifiers to keep out of every prompt: **student name, enrolment/roll id, email, phone.**
Marks / attendance / campus / batch / subject names stay (they are the content the model reasons over;
tied only to a pseudonym they are pseudonymised data).

## Step 5 — verify (Logs UI is over quota)
The org is over its 10k-logs/month Portkey limit and is dropping logs, so **don't rely on the Logs UI**.
Verify from the response instead: log Portkey's `hook_results` (in the response body) and the
`x-portkey-*` response headers (verdicts/counts only — never log the redacted values). Confirm a test
generation shows the guardrails ran and that no raw name/phone/roll-no is in the outbound prompt.

## Tests
Port `onepager/test_portkey_redaction.py`: mock the HTTP client, assert (a) in Portkey mode the request
goes to `api.portkey.ai`, model is `@openrouter/…`, the `x-portkey-config` header is set, and the real
name is **absent** from the request body while the pseudonym is present; (b) `stitch_name` restores the
real name (incl. possessive) without corrupting other words; (c) direct mode is unchanged.

## Acceptance
- [ ] `create_report` narratives generate with `PORTKEY_API_KEY` set and `PORTKEY_CONFIG_ID=pc-moodle-cf8b65`.
- [ ] The outbound prompt contains no student name/id/email/phone (shown by the test + a spot check).
- [ ] The rendered report still shows the correct real name (stitched server-side).
- [ ] Guardrail results are visible in the response; no PII in logs/metadata.
- [ ] `PORTKEY_API_KEY` is a secret, not committed; unset = unchanged direct-OpenRouter behaviour.

## One thing to re-check
The roll-no regex `\b[A-Z]{2}\d{2}[A-Z]{2}\d{3}\b` was only validated against `JN25MM002`. Confirm it
matches the real enrolment-id format before relying on that guardrail.
