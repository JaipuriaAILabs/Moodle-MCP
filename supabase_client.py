"""Read-only, principal-scoped data-access layer for the Moodle Reports MCP.

Unlike the student MCP (per-user RLS), report data is institutional, so a single bounded
supabase client is reused process-wide; the tenant boundary is the token's allowed-campus set,
applied by every tool. Each request builder also carries an internal ``X-MCP-Scope`` header;
the database RLS migration consumes the same scope as a defense-in-depth boundary. The DB
credential stays server-side and is never exposed to the host.
"""
import json
import logging

from supabase import Client, create_client

from cache import TTLCache
from config import settings

log = logging.getLogger(__name__)

_run_cache = TTLCache(maxsize=64, ttl=300)
_client_singleton: Client | None = None
_SCOPE_HEADER = "X-MCP-Scope"
_MAX_SCOPE_HEADER_BYTES = 2048


def _client() -> Client:
    """One process-wide client. When an anon/publishable key is configured, use it
    as the gateway `apikey` and attach the DB key as the bearer so PostgREST runs
    as the DB key's role. This is required for a least-privilege custom-role JWT
    (e.g. reporting_readonly): Supabase's gateway only accepts the registered
    anon/service key as `apikey`, so a custom JWT must ride in the Authorization
    header, never as the apikey. With no anon key set we keep the old behaviour
    (DB key used for both) so a service_role deployment is unchanged."""
    global _client_singleton
    if _client_singleton is None:
        db_key = settings.data_key()
        anon = settings.supabase_anon_key
        if anon:
            c = create_client(settings.supabase_url, anon)
            try:
                c.postgrest.auth(db_key)   # bearer -> PostgREST runs as this role
            except AttributeError as exc:  # pragma: no cover - very old supabase-py
                raise RuntimeError(
                    "supabase-py lacks postgrest.auth — upgrade to >=2.5.0") from exc
            _client_singleton = c
        else:
            _client_singleton = create_client(settings.supabase_url, db_key)
    return _client_singleton


def db_scope_for_principal(principal: dict) -> dict:
    """Return the compact, non-PII scope contract sent to PostgREST.

    The database branches on ``kind``: student scopes are matched by enrolment ID
    only, while staff scopes are matched by campus. Keeping those branches mutually
    exclusive prevents the classic bug where a student's campus claim accidentally
    authorizes every peer in that campus.
    """
    role = str(principal.get("role") or "").strip().lower()
    if role == "student":
        ids = sorted({str(v).strip() for v in (principal.get("student_ids") or [])
                      if str(v).strip()})
        if not ids and principal.get("student_id"):
            ids = [str(principal["student_id"]).strip()]
        campuses = sorted({str(v).strip().lower() for v in (principal.get("campuses") or [])
                           if str(v).strip()})
        batches = sorted({str(v).strip() for v in (principal.get("batches") or [])
                          if str(v).strip()})
        if ids and campuses and batches:
            return {"v": 1, "kind": "student", "role": "student",
                    "student_ids": ids, "campuses": campuses, "batches": batches}
        return {"v": 1, "kind": "deny", "role": "student"}

    if role == "pending":
        return {"v": 1, "kind": "deny", "role": "pending"}

    campuses = principal.get("campuses")
    effective_role = role or ("cross_campus" if campuses is None else "faculty")
    staff_roles = {"admin", "cross_campus", "campus_admin", "faculty", "viewer"}
    if effective_role not in staff_roles:
        return {"v": 1, "kind": "deny", "role": effective_role or "unknown"}
    if campuses is None:
        if effective_role not in {"admin", "cross_campus"}:
            return {"v": 1, "kind": "deny", "role": effective_role}
        return {"v": 1, "kind": "staff", "role": effective_role, "all": True}
    allowed = sorted({str(v).strip().lower() for v in campuses if str(v).strip()})
    if effective_role in {"admin", "cross_campus"}:
        return {"v": 1, "kind": "deny", "role": effective_role}
    if not allowed:
        return {"v": 1, "kind": "deny", "role": effective_role}
    return {"v": 1, "kind": "staff", "role": effective_role, "campuses": allowed}


class _ScopedClient:
    """Per-principal view over the shared Supabase client.

    ``table()`` returns a fresh request builder, so adding the scope header there
    cannot race with another principal or mutate the process-wide client's headers.
    The underlying HTTP session remains shared for connection pooling.
    """

    def __init__(self, raw: Client, scope: dict):
        self._raw = raw
        self.scope = dict(scope)
        self.scope_header = json.dumps(scope, separators=(",", ":"), sort_keys=True)
        if len(self.scope_header.encode("utf-8")) > _MAX_SCOPE_HEADER_BYTES:
            raise ValueError("principal database scope is too large")

    def table(self, name: str):
        builder = self._raw.table(name)
        headers = dict(getattr(builder, "headers", {}) or {})
        headers[_SCOPE_HEADER] = self.scope_header
        builder.headers = headers
        return builder

    def rpc(self, name: str, params=None):
        builder = self._raw.rpc(name, params or {})
        request = getattr(builder, "request", None)
        if request is None:
            raise RuntimeError("Supabase RPC builder does not expose request headers")
        request.headers[_SCOPE_HEADER] = self.scope_header
        return builder


class MoodleService:
    """Per-request handle: the shared client + this caller's campus scope."""

    def __init__(self, client: Client, principal: dict):
        self.principal = principal
        campuses = principal.get("campuses")
        self.allowed_campuses = (
            None if campuses is None else
            [str(campus).strip().lower() for campus in campuses]
        )
        # RBAC attributes (present on registry-resolved principals; safe defaults keep
        # every legacy/static-token/all-access principal fully capable).
        self.role = principal.get("role") or ("cross_campus" if campuses is None else "faculty")
        self.can_generate = bool(principal.get("can_generate", True))
        # Student self-access boundary: when set, EVERY student-identifiable query is hard-filtered
        # to this person's OWN enrolment id(s) — a student can only ever see their own rows (across
        # all their enrolment ids), never another student's.
        is_student = principal.get("role") == "student"
        raw_ids = principal.get("student_ids") or (
            [principal["student_id"]] if principal.get("student_id") else [])
        self.self_student_ids = (
            [str(i).strip() for i in raw_ids if str(i).strip()] if is_student and raw_ids else None
        )
        # Primary id (latest enrolment): create_report target + per-student cache-key segregation.
        self.self_student_id = self.self_student_ids[0] if self.self_student_ids else None
        raw_batches = principal.get("batches") or (
            [principal["batch"]] if principal.get("batch") else [])
        self.self_batches = (
            {str(b).strip() for b in raw_batches if str(b).strip()} if self.self_student_ids else None
        )
        # Primary batch (latest): the default batch filter when a student names none.
        self.self_batch = (
            (str(principal.get("batch") or "").strip() or None) if self.self_student_ids else None
        )
        # Every PostgREST query now carries the exact same effective scope used by
        # the application filters. Before the RLS migration this header is inert;
        # after cutover, a missing/malformed header denies at the database boundary.
        self.db_scope = db_scope_for_principal(principal)
        self.client = _ScopedClient(client, self.db_scope)

    def apply_student(self, query, col: str = "student_id"):
        """Bound a query to the caller's own enrolment id(s) when they are a student; no-op
        otherwise. This is the per-student boundary — applied in every data helper so no tool can
        bypass it. A single id uses .eq (unchanged for the common case); multiple use .in_."""
        if not self.self_student_ids:
            return query
        if len(self.self_student_ids) == 1:
            return query.eq(col, self.self_student_ids[0])
        return query.in_(col, self.self_student_ids)

    def student_batch_denied(self, requested) -> bool:
        """True when a student explicitly requests a batch that is not one of their own."""
        return bool(self.self_batches) and requested is not None \
            and str(requested).strip() not in self.self_batches

    # --- campus scoping ---------------------------------------------------
    def campus_scope(self, requested: str | None):
        """Return the campus list to filter on (None == all), intersecting the request with the
        token's grant. Returns [] when the requested campus is outside the grant (empty results)."""
        requested = requested.strip().lower() if isinstance(requested, str) else requested
        if self.allowed_campuses is None:
            return [requested] if requested else None
        allowed = list(self.allowed_campuses)
        if not requested:
            return allowed
        return [requested] if requested in allowed else []

    def apply_campus(self, query, col: str = "campus", requested: str | None = None):
        scope = self.campus_scope(requested)
        if scope is None:
            return query
        return query.in_(col, scope)

    # --- pinned run -------------------------------------------------------
    def latest_run(self, campus: str, batch: str, purpose: str | None = None):
        # Defense-in-depth: the service-role key bypasses RLS, so a run_id is the
        # only thing binding downstream (run_id-keyed) queries to a campus. Refuse
        # to resolve a run for a campus outside the caller's grant, so a tool that
        # forgets the explicit campus_scope() guard can never leak another campus.
        if self.campus_scope(campus) == []:
            return None
        if self.self_batches is not None and str(batch).strip() not in self.self_batches:
            return None
        purpose = purpose or settings.report_purpose
        key = (campus, batch, purpose)
        hit = _run_cache.get(key)
        if hit is not None:
            return hit or None
        res = (self.client.table("extraction_runs")
               .select("run_id,finished_at")
               .eq("campus", campus).eq("batch", batch)
               .eq("status", "completed").eq("purpose", purpose)
               .not_.is_("finished_at", "null")
               .order("finished_at", desc=True).limit(1).execute()).data
        rid = res[0]["run_id"] if res else None
        _run_cache.set(key, rid or "")
        return rid


def create_service(principal: dict) -> MoodleService:
    return MoodleService(_client(), principal)
