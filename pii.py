"""LLM-blind PII tokenisation (AIA-1386 / AIA-1356, MCP side).

The MCP is the authoritative tokeniser: it holds the real values, so it can map a student's
identity to a stable, opaque, deterministic token (`student_ref`) with no fuzzy NER. The client
harness feeds only tokens to the model and rehydrates them for the authorised human afterwards —
so the model never sees a real name or enrolment id, but access scope and the human's view are
unchanged (PII-blindness is orthogonal to authorisation).

This module provides the primitives + a response transform. It is DORMANT until
`MCP_PII_TOKENIZE=true` AND a key is set; `enabled()` gates callers. The response-path WIRING is
done together with the harness rollout — these primitives + the resolve_identities tool are the
contract the harness integrates against. See docs/PII_TOKENIZATION_CONTRACT.md.
"""
import hashlib
import hmac
import logging

log = logging.getLogger("moodle-mcp.pii")

# Identity fields the tokeniser recognises in a record. Only treated as a student's identity
# when the record also carries a student_id (so a subject/course "name" is never redacted).
_ID_KEY = "student_id"
_NAME_KEYS = ("student_name", "student_email", "name", "full_name")


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
    and use for rehydration. No-op (returns the input unchanged) unless enabled()."""
    if not enabled() or not isinstance(result, dict):
        return result
    transformed, idmap = tokenise(result)
    if idmap:
        transformed["_identity"] = idmap
    return transformed
