"""Privacy-safe, append-only audit delivery for authenticated MCP tool calls."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
import uuid

import httpx

from config import settings

log = logging.getLogger("moodle-mcp.audit")

_http: httpx.AsyncClient | None = None


def _subject(value: str | None) -> str | None:
    """Pseudonymise an identifier before it leaves the MCP process."""
    if not value or not settings.audit_hmac_key:
        return None
    return hmac.new(
        settings.audit_hmac_key.encode(), value.strip().lower().encode(), hashlib.sha256
    ).hexdigest()


def principal_subject(principal) -> str:
    if not isinstance(principal, dict):
        return "anonymous"
    stable = principal.get("email") or principal.get("sub") or principal.get("name")
    return _subject(str(stable)) or "unknown"


def _capped(obj, cap: int):
    """Best-effort JSON-safe value, size-capped. Returns the parsed structure when
    small enough to store verbatim; otherwise a truncated preview + byte count.
    Never raises — audit enrichment must not change a tool outcome."""
    try:
        text = json.dumps(obj, default=str, ensure_ascii=False)
    except Exception:  # noqa: BLE001
        try:
            text = str(obj)
        except Exception:  # noqa: BLE001
            return {"_error": "unserialisable"}
    raw = text.encode("utf-8", "replace")
    if len(raw) <= cap:
        try:
            return json.loads(text)
        except Exception:  # noqa: BLE001
            return text
    # Truncate on BYTES, not characters — a char slice can be ~4x the byte cap for
    # multi-byte (CJK/emoji) payloads and blow past the intended size limit.
    return {"_truncated": True, "_bytes": len(raw),
            "preview": raw[:cap].decode("utf-8", "ignore")}


def _summarise_result(result, cap: int):
    """Extract a JSON-able view of a FastMCP tool result (structured content,
    content blocks, or a plain value), size-capped. Never raises."""
    try:
        payload = None
        for attr in ("structured_content", "structuredContent", "data"):
            value = getattr(result, attr, None)
            if value is not None:
                payload = value
                break
        if payload is None:
            content = getattr(result, "content", None)
            if content is not None:
                try:
                    payload = [getattr(b, "text", None)
                               or getattr(b, "data", None) or str(b) for b in content]
                except Exception:  # noqa: BLE001
                    payload = str(content)
        if payload is None:
            payload = result if isinstance(
                result, (dict, list, str, int, float, bool)) else str(result)
        return _capped(payload, cap)
    except Exception:  # noqa: BLE001
        return {"_error": "summary_failed"}


def _authorization(identity) -> dict | None:
    """Return non-PII authorization context that is safe to retain by default.

    Email/name/student ids remain behind ``MCP_CAPTURE_IDENTITY``.  Role, effective
    campus scope, capability and self/batch-bound booleans are authorization facts,
    not raw identity, and are needed to prove that RBAC was actually applied.
    """
    if not isinstance(identity, dict):
        return None
    campuses = identity.get("campuses")
    if campuses is None:
        campus_scope: str | list[str] = "all"
    elif isinstance(campuses, (list, tuple, set)):
        campus_scope = sorted({str(c).strip().lower() for c in campuses if str(c).strip()})
    else:
        campus_scope = "none"
    role = str(identity.get("role") or (
        "cross_campus" if campuses is None else "faculty")).strip().lower()
    authorization = {
        "role": role[:32],
        "campus_scope": campus_scope,
        "can_generate": bool(identity.get("can_generate", role not in ("pending", "viewer"))),
        "student_self_bound": bool(role == "student" and identity.get("student_id")),
        "student_batch_bound": bool(role == "student" and identity.get("batch")),
    }
    shadow = identity.get("_rbac_shadow")
    if isinstance(shadow, dict):
        # Copy only the deliberately bounded fields. Never allow arbitrary principal
        # content to hitch a ride into the always-on audit metadata.
        authorization["shadow"] = {
            key: shadow[key] for key in ("outcome", "role", "campus_scope", "can_generate")
            if key in shadow
        }
    return authorization


def build_metadata(*, identity=None, arguments=None, result=None,
                   source_ip: str | None = None) -> dict:
    """Assemble the audit `metadata` blob, gated by the MCP_CAPTURE_* flags.

    With every capture flag at its default (off), this retains only the server
    version and non-PII authorization facts. Turning a flag on widens capture to
    real identity / full arguments / result payload / source IP.
    This is a pure function so the gating can be unit-tested without a network.
    """
    meta: dict = {"server_version": settings.server_version}
    authorization = _authorization(identity)
    if authorization is not None:
        meta["authorization"] = authorization
    if settings.capture_identity and isinstance(identity, dict):
        # Keep campuses even when None — None is meaningful here (all-campus grant),
        # so it must be recorded, not treated as "absent".
        meta["identity"] = {
            k: identity.get(k) for k in ("email", "name", "campuses", "role")
        }
    if settings.capture_client_ip and source_ip:
        meta["source_ip"] = str(source_ip)[:64]
    if settings.capture_arguments and arguments is not None:
        meta["arguments"] = _capped(arguments, settings.capture_args_max_bytes)
    if settings.capture_results and result is not None:
        meta["result"] = _summarise_result(result, settings.capture_result_max_bytes)
    return meta


def _client() -> httpx.AsyncClient:
    global _http
    if _http is None:
        _http = httpx.AsyncClient(timeout=3.0)
    return _http


async def record_tool_call(
    *,
    tool: str,
    principal,
    ok: bool | None,
    started: float,
    headers: dict | None = None,
    scope: str | None = None,
    error_code: str | None = None,
    arguments=None,
    result=None,
    source_ip: str | None = None,
) -> bool:
    """Insert one audit event and report whether durable delivery succeeded.

    ``ok=None`` is a pre-execution attempt record. In fail-closed audit mode that
    record must land before any data access or report generation begins.

    ``arguments`` / ``result`` / ``source_ip`` are captured into ``metadata`` only
    when the matching MCP_CAPTURE_* flag is on (all default off — see build_metadata);
    the pseudonymised user/session/client subjects are always recorded.
    """
    headers = {str(k).lower(): str(v) for k, v in (headers or {}).items()}
    user_subject = principal_subject(principal)
    duration_ms = max(0, round((time.monotonic() - started) * 1000))
    request_id = headers.get("x-request-id") or str(uuid.uuid4())
    metadata = build_metadata(identity=principal, arguments=arguments,
                              result=result, source_ip=source_ip)

    if not settings.audit_enabled():
        log.info("audit tool=%s subject=%s ok=%s scope=%s duration_ms=%s error=%s cap=%s",
                 tool, user_subject[:16], "attempt" if ok is None else int(ok),
                 scope or "-", duration_ms, error_code or "-",
                 ",".join(k for k in ("identity", "arguments", "result", "source_ip")
                          if k in metadata) or "-")
        return False

    payload = {
        "p_event_id": str(uuid.uuid4()),
        "p_user_subject": user_subject,
        "p_tool_name": tool[:128],
        "p_campus_scope": (scope or "")[:64] or None,
        "p_outcome": "attempt" if ok is None else ("success" if ok else "failure"),
        "p_error_code": (error_code or "")[:64] or None,
        "p_duration_ms": duration_ms,
        "p_request_id": request_id[:128],
        "p_session_subject": _subject(headers.get("mcp-session-id")),
        "p_client_subject": _subject(headers.get("user-agent")),
        "p_metadata": metadata,
    }
    base = settings.supabase_url.rstrip("/")
    api_key = settings.supabase_anon_key or settings.supabase_audit_key
    try:
        response = await _client().post(
            f"{base}/rest/v1/rpc/record_mcp_tool_call",
            headers={
                "apikey": api_key,
                "Authorization": f"Bearer {settings.supabase_audit_key}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
        response.raise_for_status()
        return True
    except Exception as exc:  # noqa: BLE001 — audit outage never changes tool outcome
        log.warning("audit delivery failed: %s", type(exc).__name__)
        return False
