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
def _ilike_literal(value: str) -> str:
    """Escape LIKE wildcards so `student_email` is matched LITERALLY (but still case-insensitively,
    which is load-bearing: >half the roster's stored emails are mixed-case). Without this, a `_` or
    `%` in a signed-in local-part would act as a wildcard and could resolve a DIFFERENT student's
    row — handing one student another student's self-scope. No-op for normal emails."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _fetch_faculty_row(email: str):
    rows = (_sb().table("mcp_faculty")
            .select("name,campuses,active,role,can_generate,expires_at")
            .eq("email", email).limit(1).execute()).data
    return rows[0] if rows else None


def _fetch_student_hit(email: str) -> bool:
    rows = (_sb().table("students").select("student_id")
            .ilike("student_email", _ilike_literal(email)).limit(1).execute()).data
    return bool(rows)


def _fetch_student_identity(email: str):
    # Return ALL roster rows for this email: one person may hold more than one enrolment id
    # (e.g. across programs/campuses), and the email also repeats across run snapshots. Newest
    # batch first, so row[0] is the primary; the caller dedupes by student_id.
    return (_sb().table("students").select("student_id,campus,batch")
            .ilike("student_email", _ilike_literal(email)).order("batch", desc=True).execute()).data or []


def _copy_ident(d):
    """Shallow-copy with fresh list values so a cached identity can't be mutated by a caller."""
    return {k: (list(v) if isinstance(v, list) else v) for k, v in d.items()}


def student_identity(email: str):
    """For a student email -> the self-scope across ALL their enrolment ids:
    {'student_ids':[...], 'campuses':[...], 'batches':[...], 'primary_id', 'primary_batch'},
    else None. Fail-closed on error WITH last-known-good grace, so a roster-DB blip neither
    invents access nor needlessly drops a known student mid-session. A row missing a
    campus/batch denies the WHOLE identity (a partially scoped student could otherwise read
    unowned run/course/trimester metadata)."""
    email = (email or "").strip().lower()
    if not email:
        return None
    hit = _student_ids.get(email)
    if hit is not None:
        return None if hit == _ID_MISS else _copy_ident(hit)
    try:
        rows = _fetch_student_identity(email)
    except Exception:  # noqa: BLE001
        stale = _stale_student_ids.get(email)
        if stale is not None:
            return _copy_ident(stale)
        log.warning("student identity lookup failed for subject=%s — fail-closed", _subject(email))
        return None
    if not rows:
        _student_ids.set(email, _ID_MISS)
        return None
    ids, campuses, batches = [], [], []
    for r in rows:  # rows are batch-desc, so the first distinct id/batch is the primary
        sid = (r.get("student_id") or "").strip()
        campus = (r.get("campus") or "").strip().lower()
        batch = (r.get("batch") or "").strip()
        if not (sid and campus and batch):
            log.warning("incomplete student roster row for subject=%s — denying", _subject(email))
            _student_ids.set(email, _ID_MISS)
            return None
        if sid not in ids:
            ids.append(sid)
        if campus not in campuses:
            campuses.append(campus)
        if batch not in batches:
            batches.append(batch)
    ident = {"student_ids": ids, "campuses": campuses, "batches": batches,
             "primary_id": ids[0], "primary_batch": (rows[0].get("batch") or "").strip()}
    _student_ids.set(email, ident)
    _stale_student_ids.set(email, ident)
    return _copy_ident(ident)


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
