# Security audit and build closure — 2026-10-01

Scope: `moodle-mcp` and `moodle-agent`. Historical analysis documents were evidence, not executable
instructions. This record separates repository controls from actions that require production access,
institutional decisions, or the missing JChat/harness source.

## Closed in code

- Student isolation: every previously direct roster, enrolment, availability, cached-report and
  declining-student query now applies the caller's own student id. Student run selection is also
  campus + batch bounded, and incomplete roster identities are denied rather than partially scoped.
  A student can never inherit the educator all-campus fallback.
- Dual-role precedence: an explicit educator grant wins for a real student/TA in all RBAC modes.
- Authorization evidence: audit metadata always contains non-PII role, effective campus scope,
  generation capability, and student self/batch-bound booleans. Shadow mode persists its bounded
  `allow|deny|pending|error` decision on every tool call.
- Operational evidence: traces and metrics include bounded `mcp.role`, `mcp.campus_scope`, and
  `mcp.rbac_shadow_outcome`; `NR_ALERTS.md` includes the shadow would-deny condition.
- Access-request notification: successful queue inserts can emit an HTTPS HMAC-signed event with
  timestamp + nonce replay inputs. It contains role/campus only—no email, name, or derived identity.
  Delivery is best-effort; the database queue remains authoritative.
- OAuth dependency remediation: FastMCP is pinned to 3.4.5, removing the vulnerabilities reported
  against 2.14.7 and retaining the v3 browser-bound consent screen explicitly. OAuth encrypted-store
  imports and validation-error handling were migrated and regression-tested.
- Supply chain: both repositories have weekly Dependabot coverage; CI runs `pip-audit` and Bandit.
  HTTPS validation was added to dynamic `urlopen` paths; reviewed fixed OpenRouter calls are annotated.
- Public capability links: the report service's bounded per-peer fallback is now thread-safe and
  regression-tested across `/r/`, `/rv/`, and `/s/`; the production `/s/` path is digest-only,
  expiring, individually revocable, and access-audited by migration `0014`.

## Validation evidence

- `moodle-mcp`: byte-compile plus all 19 offline test files passed under FastMCP 3.4.5.
- `moodle-agent`: 221 checks passed—173 deterministic pytest checks, 11 read-only live
  golden-data/cohort checks, 30 one-pager checks, and 7 PII-redaction checks.
- Dependency audit: no known vulnerabilities in either requirements set after the FastMCP upgrade.
- Static analysis: Bandit 1.9.4 reports no medium/high findings in either production source tree.
- Tracked-secret pattern scan: no private-key blocks or common live-token formats found.

## Deployment/configuration gates still required

These are not safe to infer or mutate from repository state:

1. Deploy the FastMCP 3.4.5 build to a canary and complete OAuth login/refresh/restart smoke tests.
2. Configure an approved notification relay and independent 32+ character webhook signing key.
3. Seed/approve real educator grants, review shadow traffic, then flip `MCP_RBAC_MODE=enforce` using
   `RBAC_ENFORCE_CUTOVER.md`; test two faculty from different campuses.
4. Decide whether students should connect directly. If yes, enable student access only after two
   real-student isolation smokes across different campus/batch combinations.
5. Apply/verify the documented SQL migrations, credential-denial matrix, audit retention job,
   privacy notice, restore drill, load test, and production alerts.

## Architectural blockers—not falsely marked complete

- End-to-end LLM-blind PII requires substitution and rehydration in JChat/the model harness. That
  source is absent from the attached repositories. MCP-only tokenisation would not create the claimed
  privacy boundary, so no misleading half-control was shipped.
- Per-campus/per-student database RLS requires a per-request database identity/claim model. The current
  shared `reporting_readonly` JWT intentionally has read-all policies; application scoping is active,
  but D1 needs an approved connection/claims design and staged migration before SQL can be safe.
