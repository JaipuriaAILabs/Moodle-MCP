"""DB-backed educator grants and student-roster classification.

In ENFORCE mode, ``mcp_faculty`` is the authoritative educator allowlist for every
domain. OFF/SHADOW retain the historical all-campus Jaipuria policy, except that a
roster student is always self-scoped (when enabled) or denied. ``MCP_FACULTY`` is the
break-glass override.

Threat model notes:
* An explicit educator grant is checked before student classification, so an admin-
  approved dual-role student/TA receives the educator grant.
* Everything fails CLOSED: a DB error, a malformed row, an inactive row, or an
  unparseable campuses value all resolve to "no access" (with a stale-cache
  grace for transient DB blips, so a 2-second Supabase hiccup doesn't kick out
  500 signed-in users mid-session).
* Lookups are cached (bounded TTL caches) so steady-state cost is ~1 small
  SELECT per user per minute, regardless of tool-call volume.

The MCP's DB credential is reporting_readonly: it can SELECT this table (RLS
policy) and cannot write it. Roster changes happen out-of-band with the service
role — see OPERATIONS.md for the bulk-load runbook.
"""
import hashlib
import logging

from cache import TTLCache

log = logging.getLogger("moodle-mcp.faculty")

_GRANT_TTL = 60.0     # how long a grant/denial is believed before re-checking
_STUDENT_TTL = 600.0  # roster membership changes rarely
_MISS = "__no_grant__"

_grants = TTLCache(maxsize=4096, ttl=_GRANT_TTL)
_stale_grants = TTLCache(maxsize=4096, ttl=3600.0)  # last-known-good, outage grace
_students = TTLCache(maxsize=8192, ttl=_STUDENT_TTL)
_stale_students = TTLCache(maxsize=8192, ttl=3600.0)  # last-known-good, outage grace
_student_ids = TTLCache(maxsize=8192, ttl=_STUDENT_TTL)
_stale_student_ids = TTLCache(maxsize=8192, ttl=3600.0)
_ID_MISS = "__no_student__"


def _subject(email: str) -> str:
    """Stable diagnostic identifier that does not put a faculty email in logs."""
    return hashlib.sha256(email.encode()).hexdigest()[:12]


def _sb():
    from supabase_client import _client
    return _client()


# --- raw fetches (module-level so tests can monkeypatch them) ----------------
def _fetch_faculty_row(email: str):
    rows = (_sb().table("mcp_faculty")
            .select("name,campuses,active,role,can_generate,expires_at")
            .eq("email", email).limit(1).execute()).data
    return rows[0] if rows else None


def _fetch_student_hit(email: str) -> bool:
    rows = (_sb().table("students").select("student_id")
            .ilike("student_email", email).limit(1).execute()).data
    return bool(rows)


def _fetch_student_identity(email: str):
    # Emails repeat across batch snapshots; take the most recent batch's row. student_id is
    # the stable per-person key that bounds every student query.
    rows = (_sb().table("students").select("student_id,campus,batch")
            .ilike("student_email", email).order("batch", desc=True).limit(1).execute()).data
    return rows[0] if rows else None


def student_identity(email: str):
    """For a student email -> {'student_id','campus','batch'} self-scope, else None.
    Fail-closed on error (None) WITH last-known-good grace, so a roster-DB blip neither
    invents access nor needlessly drops a known student mid-session."""
    email = (email or "").strip().lower()
    if not email:
        return None
    hit = _student_ids.get(email)
    if hit is not None:
        return None if hit == _ID_MISS else dict(hit)
    try:
        row = _fetch_student_identity(email)
    except Exception:  # noqa: BLE001
        stale = _stale_student_ids.get(email)
        if stale is not None:
            return dict(stale)
        log.warning("student identity lookup failed for subject=%s — fail-closed", _subject(email))
        return None
    if not row or not row.get("student_id"):
        _student_ids.set(email, _ID_MISS)
        return None
    ident = {"student_id": row["student_id"],
             "campus": (row.get("campus") or "").strip().lower(),
             "batch": (row.get("batch") or "").strip()}
    _student_ids.set(email, ident)
    _stale_student_ids.set(email, ident)
    return dict(ident)


# --- public API ---------------------------------------------------------------
_VALID_ROLES = ("admin", "cross_campus", "campus_admin", "faculty", "viewer")


def _expired(ts) -> bool:
    """True when an ISO `expires_at` timestamp is in the past. Unparseable values are
    treated as NOT expired at runtime (the DB CHECK guarantees the format; a runtime
    parse error must not lock a valid grant out and cause an outage)."""
    if not ts:
        return False
    try:
        from datetime import datetime, timezone
        dt = datetime.fromisoformat(str(ts).strip().replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) >= dt
    except Exception:  # noqa: BLE001
        return False


def normalize_role(v) -> str:
    """mcp_faculty.role -> a known role string, defaulting to 'faculty' for a missing/
    unknown value (never widens: 'faculty' is scoped, and campuses still gate breadth)."""
    r = str(v or "faculty").strip().lower()
    return r if r in _VALID_ROLES else "faculty"


def normalize_campuses(v):
    """mcp_faculty.campuses -> principal 'campuses' value: None for "all", a
    non-empty list of lowercase strings for a scoped grant, or the sentinel
    'invalid' for anything else (deny — a malformed row must never widen access)."""
    if isinstance(v, str) and v.strip().lower() == "all":
        return None
    if isinstance(v, list) and v and all(isinstance(x, str) and x.strip() for x in v):
        return [x.strip().lower() for x in v]
    return "invalid"


def faculty_grant(email: str):
    """Active mcp_faculty row -> {'name': ..., 'campuses': None|[...]}, else None.
    Fail-closed on any error, with last-known-good grace for transient DB blips."""
    email = (email or "").strip().lower()
    if not email:
        return None
    hit = _grants.get(email)
    if hit is not None:
        return None if hit == _MISS else dict(hit)
    try:
        row = _fetch_faculty_row(email)
    except Exception:  # noqa: BLE001 — DB down != access granted
        stale = _stale_grants.get(email)
        if stale is not None:
            log.warning("faculty lookup failed for subject=%s — serving last-known-good grant",
                        _subject(email))
            return dict(stale)
        log.warning("faculty lookup failed for subject=%s — fail-closed deny",
                    _subject(email), exc_info=True)
        return None
    if not row or row.get("active") is not True:
        _grants.set(email, _MISS)
        return None
    if _expired(row.get("expires_at")):
        log.warning("mcp_faculty row for subject=%s is expired — denying", _subject(email))
        _grants.set(email, _MISS)
        return None
    campuses = normalize_campuses(row.get("campuses"))
    if campuses == "invalid":
        log.error("mcp_faculty row for subject=%s has malformed campuses %r — denying",
                  _subject(email), row.get("campuses"))
        _grants.set(email, _MISS)
        return None
    can_generate = row.get("can_generate")
    grant = {"name": row.get("name"), "campuses": campuses,
             "role": normalize_role(row.get("role")),
             "can_generate": True if can_generate is None else bool(can_generate)}
    _grants.set(email, grant)
    _stale_grants.set(email, grant)
    return dict(grant)


def submit_access_request(email: str, name, role: str, campuses) -> bool:
    """File/refresh a pending self-service access request via the SECURITY DEFINER RPC
    (the read-only DB role has execute on it but no write grant on the inbox). Fail-safe:
    returns False on any error so the tool can tell the user to retry."""
    email = (email or "").strip().lower()
    if not email:
        return False
    try:
        _sb().rpc("request_mcp_access", {
            "p_email": email, "p_name": name or "",
            "p_role": role, "p_campuses": campuses}).execute()
        return True
    except Exception:  # noqa: BLE001
        log.warning("access request submit failed for subject=%s", _subject(email), exc_info=True)
        return False


def list_pending_requests(limit: int = 200):
    """Pending self-service requests (admin view). Empty list on any error."""
    try:
        rows = (_sb().table("mcp_access_requests")
                .select("email,name,requested_role,requested_campuses,status,created_at")
                .eq("status", "pending").order("created_at").limit(limit).execute()).data
        return rows or []
    except Exception:  # noqa: BLE001
        log.warning("listing access requests failed", exc_info=True)
        return []


def is_student(email: str) -> bool:
    """True when the email appears in the student roster.

    The caller decides whether that means a self-scoped student session or denial;
    an explicit educator grant is checked first. On a DB error this returns True so
    an unknown identity can never fall through to a broader default grant. The env
    ``MCP_FACULTY`` override is resolved before this check for break-glass access.
    """
    email = (email or "").strip().lower()
    if not email:
        return True
    hit = _students.get(email)
    if hit is not None:
        return hit
    try:
        found = _fetch_student_hit(email)
    except Exception:  # noqa: BLE001
        stale = _stale_students.get(email)
        if stale is not None:
            # Last-known-good: a previously-seen faculty (stale False) is not kicked out by a
            # transient roster-DB blip; a previously-seen student stays denied. Only a
            # never-seen email with the DB down fails closed (deny) below.
            log.warning("student-roster check failed for subject=%s — serving last-known-good",
                        _subject(email))
            return stale
        log.warning("student-roster check failed for subject=%s — fail-closed deny",
                    _subject(email))
        return True
    _students.set(email, found)
    _stale_students.set(email, found)
    if found:
        log.info("student roster match: subject=%s", _subject(email))
    return found
