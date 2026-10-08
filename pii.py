"""LLM-blind PII tokenisation (AIA-1386 / AIA-1356, MCP side).

The MCP is the authoritative tokeniser: it holds the real values, so it can map a student's
identity to a stable, opaque, deterministic token (`student_ref`) with no fuzzy NER. The client
harness feeds only tokens to the model and rehydrates them for the authorised human afterwards —
so the model never sees a real name or enrolment id, but access scope and the human's view are
unchanged (PII-blindness is orthogonal to authorisation).

This module contains both the legacy client-harness tokenisation contract and the production
in-server redactor. The legacy ``tokenise_response`` path is dormant until
``MCP_PII_TOKENIZE=true``; the zero-trust ``redact`` path is wired independently at
``GuardMiddleware`` and is mandatory when ``MCP_PII_REDACTION_MODE=enforce``. See
docs/PII_REDACTION_IMPLEMENTATION.md.
"""
import hashlib
import hmac
import json
import logging
import re

log = logging.getLogger("moodle-mcp.pii")

# Identity fields the tokeniser recognises in a record. Only treated as a student's identity
# when the record also carries a student_id (so a subject/course "name" is never redacted).
_ID_KEY = "student_id"
_NAME_KEYS = ("student_name", "student_email", "name", "full_name", "first_name")

# Generic PII patterns for the free-text LEAK GUARD (a secondary net over the exact roster-value
# sweep below). Applied only to string leaf values, after structured tokenisation. Deliberately
# conservative so marks, report URLs and our own `S_` refs are never matched. Institutional
# enrolment numbers and common Indian government identifiers are explicit leak-guard targets.
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_PHONE_RE = re.compile(r"(?<!\d)(?:\+?91[\s-]?|0)?[6-9]\d{9}(?!\d)")
_ENROLMENT_RE = re.compile(r"\bJ[A-Z]\d{2}[A-Z]{2,6}\d{3,6}\b", re.IGNORECASE)
_PAN_RE = re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b", re.IGNORECASE)
_AADHAAR_RE = re.compile(r"(?<!\d)\d{4}[\s-]?\d{4}[\s-]?\d{4}(?!\d)")


def enabled() -> bool:
    from config import settings
    return bool(settings.pii_tokenize and settings.pii_key())


def student_ref(student_id) -> str | None:
    """Stable, opaque, one-way token for an enrolment id — same id → same ref (per key), so the
    harness can correlate refs across a session. None when no key is configured."""
    from config import settings
    key = settings.pii_key()
    sid = str(student_id or "").strip().lower()
    if not key or not sid:
        return None
    return "S_" + hmac.new(key.encode(), sid.encode(), hashlib.sha256).hexdigest()[:12]


def _tokenise_record(rec: dict, idmap: dict) -> None:
    """In-place: if `rec` identifies a student, replace its identity fields with the ref and record
    ref → real identity in `idmap`. No-op for records without a student_id."""
    raw_id = rec.get(_ID_KEY)
    ref = student_ref(raw_id)
    if ref is None:
        return
    original = {_ID_KEY: raw_id}
    for k in _NAME_KEYS:
        if k in rec and rec[k] is not None:
            original[k] = rec[k]
            rec[k] = ref           # model sees the ref in the name/email slot
    rec[_ID_KEY] = ref             # and in the id slot
    idmap.setdefault(ref, original)


def tokenise(obj, idmap: dict | None = None):
    """Recursively tokenise every student-identifying record in a nested structure. Returns
    (transformed_copy, identity_map). The caller attaches the map on a side channel the harness
    strips before the model (never leave it where the model can read it in production)."""
    if idmap is None:
        idmap = {}
    if isinstance(obj, dict):
        out = {k: tokenise(v, idmap)[0] for k, v in obj.items()}
        _tokenise_record(out, idmap)
        return out, idmap
    if isinstance(obj, list):
        return [tokenise(v, idmap)[0] for v in obj], idmap
    return obj, idmap


def tokenise_response(result: dict) -> dict:
    """Tokenise a tool result and embed the reverse map under `_identity` for the harness to strip
    and use for rehydration. No-op (returns the input unchanged) unless enabled().

    NOTE: this is the PRIOR "harness-rehydration" contract (AIA-1356, docs/PII_TOKENIZATION_CONTRACT.md)
    — it emits a reverse `_identity` map and trusts the client to strip it. The in-server redaction
    path (`redact()`, wired at the GuardMiddleware chokepoint) SUPERSEDES it for the default rollout:
    it never emits a reverse map, so a non-cooperating client (e.g. a generic MCP host) can't leak
    names to its model. Both share the same deterministic `student_ref`."""
    if not enabled() or not isinstance(result, dict):
        return result
    transformed, idmap = tokenise(result)
    if idmap:
        transformed["_identity"] = idmap
    return transformed


# ─────────────────────────────── in-server redaction (zero-trust) ───────────────────────────────
# The DPDP-correct primary control: pseudonymise every student identity in a tool result BEFORE it
# leaves the server, with NO reverse map emitted to the client. Real names reappear only in the
# auth-gated, server-side report renderer — never in anything a model reads. See
# docs/PII_REDACTION_PLAN.md (plan) and the GuardMiddleware wiring in security.py.

def _identity_sweepers(idmap: dict):
    """Build exact free-text replacers from the identities the structured tokeniser already pulled
    out of THIS result — we KNOW these strings, so the sweep needs no fuzzy NER. Returns
    (name_regex, name_lookup, email_map):
      - names/ids: word-bounded, case-insensitive (a full name is also split into its parts so a
        first-name-only mention — "Rahul improved" — is caught, not just "Rahul Sharma").
      - emails: distinctive enough to replace as plain substrings."""
    names: dict[str, str] = {}
    emails: dict[str, str] = {}
    for ref, original in idmap.items():
        for k in (_ID_KEY, *_NAME_KEYS):
            val = original.get(k)
            if not isinstance(val, str):
                continue
            v = val.strip()
            if not v:
                continue
            if "@" in v:
                emails[v] = ref
                continue
            if len(v) >= 3:
                names.setdefault(v, ref)
            if k in ("student_name", "full_name", "name"):
                for part in v.split():
                    if len(part) >= 3:
                        names.setdefault(part, ref)
    name_re = None
    if names:
        keys = sorted(names, key=len, reverse=True)          # longest first: "Rahul Sharma" > "Rahul"
        name_re = re.compile(r"\b(" + "|".join(re.escape(k) for k in keys) + r")\b", re.IGNORECASE)
    name_lookup = {k.lower(): v for k, v in names.items()}
    return name_re, name_lookup, emails


def _sweep_strings(obj, name_re, name_lookup, emails, stats):
    """Recursively replace known PII substrings in every string leaf; numbers are untouched."""
    if isinstance(obj, dict):
        return {k: _sweep_strings(v, name_re, name_lookup, emails, stats) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sweep_strings(v, name_re, name_lookup, emails, stats) for v in obj]
    if isinstance(obj, str):
        s = obj
        for email, ref in emails.items():
            if email in s:
                s = s.replace(email, ref)
                stats["freetext"] += 1
        if name_re is not None:
            def _rep(m):
                stats["freetext"] += 1
                return name_lookup.get(m.group(0).lower(), m.group(0))
            s = name_re.sub(_rep, s)
        return s
    return obj


def _leak_guard(obj, stats, redact_hits):
    """Final net for PII that can be recognised without a roster identity map. Counts always;
    replaces only when ``redact_hits`` is true. Never touches opaque ``S_`` references."""
    if isinstance(obj, dict):
        return {k: _leak_guard(v, stats, redact_hits) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_leak_guard(v, stats, redact_hits) for v in obj]
    if isinstance(obj, str):
        patterns = (
            (_EMAIL_RE, "[EMAIL]"),
            (_AADHAAR_RE, "[AADHAAR]"),
            (_PAN_RE, "[PAN]"),
            (_PHONE_RE, "[PHONE]"),
            (_ENROLMENT_RE, "[STUDENT_ID]"),
        )
        for pattern, replacement in patterns:
            hits = len(pattern.findall(obj))
            if hits:
                stats["leak"] += hits
                if redact_hits:
                    obj = pattern.sub(replacement, obj)
        return obj
    return obj


def _model_fields(obj):
    """Return Pydantic v2 field names without importing FastMCP/MCP model classes here."""
    fields = getattr(type(obj), "model_fields", None)
    if isinstance(fields, dict) and callable(getattr(obj, "model_copy", None)):
        return tuple(fields)
    return ()


def _json_container(value):
    """Parse JSON text only when it contains an object/array worth traversing."""
    if not isinstance(value, str):
        return None
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, (dict, list)) else None


def _collect_identities(obj, idmap):
    """Discover structured identities across dicts and real FastMCP/Pydantic result wrappers."""
    if isinstance(obj, dict):
        tokenise(obj, idmap)  # transformed copy is intentionally discarded in this discovery pass
        for value in obj.values():
            _collect_identities(value, idmap)
        return
    if isinstance(obj, list):
        tokenise(obj, idmap)
        for value in obj:
            _collect_identities(value, idmap)
        return
    if isinstance(obj, str):
        parsed = _json_container(obj)
        if parsed is not None:
            tokenise(parsed, idmap)
        return
    for field in _model_fields(obj):
        _collect_identities(getattr(obj, field, None), idmap)


def _redact_value(obj, idmap, name_re, name_lookup, emails, stats, redact_hits,
                  *, already_tokenised=False):
    """Redact while preserving FastMCP/MCP Pydantic result and content-block types."""
    if isinstance(obj, dict):
        transformed = obj if already_tokenised else tokenise(obj, idmap)[0]
        return {
            key: _redact_value(value, idmap, name_re, name_lookup, emails, stats, redact_hits,
                               already_tokenised=True)
            for key, value in transformed.items()
        }
    if isinstance(obj, list):
        transformed = obj if already_tokenised else tokenise(obj, idmap)[0]
        return [
            _redact_value(value, idmap, name_re, name_lookup, emails, stats, redact_hits,
                          already_tokenised=True)
            for value in transformed
        ]
    if isinstance(obj, str):
        parsed = _json_container(obj)
        if parsed is not None:
            transformed, _ = tokenise(parsed, idmap)
            transformed = _sweep_strings(transformed, name_re, name_lookup, emails, stats)
            transformed = _leak_guard(transformed, stats, redact_hits)
            return json.dumps(transformed, ensure_ascii=False, separators=(",", ":"))
        transformed = _sweep_strings(obj, name_re, name_lookup, emails, stats)
        return _leak_guard(transformed, stats, redact_hits)
    fields = _model_fields(obj)
    if fields:
        updates = {
            field: _redact_value(getattr(obj, field, None), idmap, name_re, name_lookup,
                                 emails, stats, redact_hits)
            for field in fields
        }
        return obj.model_copy(update=updates, deep=True)
    return obj


def redact(result, *, redact_hits: bool = True):
    """In-server PII redaction for a tool result (zero-trust: NO `_identity` side-map is produced).

    Returns (redacted_copy, stats) where stats = {"ids", "freetext", "leak"} are COUNTS only (never
    values) for shadow-mode logging. Reuses the deterministic tokeniser, so marks/campus/numbers are
    untouched and the same student is always the same ref. `redact_hits=False` still counts leak-guard
    hits but leaves them in place (used by shadow callers that only want the stats)."""
    stats = {"ids": 0, "freetext": 0, "leak": 0}
    idmap = {}
    _collect_identities(result, idmap)
    stats["ids"] = len(idmap)
    name_re, name_lookup, emails = _identity_sweepers(idmap)
    transformed = _redact_value(result, idmap, name_re, name_lookup, emails, stats, redact_hits)
    return transformed, stats
