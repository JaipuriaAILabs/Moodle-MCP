"""Self-service access requests (RBAC Phase 2). An unprovisioned but verified account
gets a limited 'pending' session (see security._pending_principal) whose only useful
action is request_access: pick ONE campus + a scoped role, which lands in the
mcp_access_requests queue for an admin to approve out-of-band. No pre-fed roster.

Privileged roles (admin, cross_campus) are never self-requestable — an admin assigns
those directly in the registry.
"""
from pydantic import BaseModel, Field

from annotations import GENERATE_ANNOTATIONS, READONLY_ANNOTATIONS
from config import settings

_SELF_SERVICE_ROLES = ("faculty", "campus_admin", "viewer")


class AccessRequestParams(BaseModel):
    campus: str = Field(description="the campus you need access to", max_length=64)
    role: str = Field(default="faculty",
                      description="faculty | campus_admin | viewer", max_length=32)


def register(mcp, get_service):
    @mcp.tool(title="Request Access", annotations=GENERATE_ANNOTATIONS)
    async def request_access(params: AccessRequestParams) -> dict:
        """
        WHAT: Request Moodle access scoped to ONE campus, pending administrator approval.
        USE WHEN: you are signed in but have no access yet ('request access', 'I need Noida
        access', 'get me access to my campus'). Once an admin approves, your campus data
        tools start working on your next query.
        DO NOT USE WHEN: you already have access (use the data tools). Cannot grant admin or
        cross-campus access — those are assigned by an administrator.
        RETURNS: status ('pending') and what was requested.
        """
        import faculty as registry
        svc = await get_service()
        email = (svc.principal.get("email") or "").strip().lower()
        if not email:
            return {"ok": False, "note": "No verified identity on this session."}
        campus = (params.campus or "").strip().lower()
        role = (params.role or "faculty").strip().lower()
        valid = settings.campuses()
        if campus not in valid:
            return {"ok": False,
                    "note": f"Unknown campus '{campus}'. Valid campuses: {', '.join(valid)}."}
        if role not in _SELF_SERVICE_ROLES:
            return {"ok": False,
                    "note": f"Role must be one of: {', '.join(_SELF_SERVICE_ROLES)} "
                            "(admin/cross-campus are assigned by an administrator)."}
        if registry.submit_access_request(email, svc.principal.get("name"), role, [campus]):
            # The DB queue is already durable and remains authoritative. Notification
            # is best-effort and contains no requester identity or derived identifier.
            from access_notifications import notify_access_request
            await notify_access_request(role=role, campuses=[campus])
            return {"ok": True, "status": "pending",
                    "requested": {"campus": campus, "role": role},
                    "note": "Request submitted — an administrator will review it. Once approved, "
                            "your campus data is available on your next query."}
        return {"ok": False, "note": "Could not file the request right now — please retry."}

    @mcp.tool(title="List Access Requests", annotations=READONLY_ANNOTATIONS)
    async def list_access_requests() -> dict:
        """
        WHAT: (administrators only) the pending self-service access requests awaiting approval.
        USE WHEN an admin says: 'who is waiting for access', 'pending access requests',
        'the access queue'.
        DO NOT USE WHEN you are not an administrator — it is denied.
        RETURNS: pending[] (email, name, requested_role, requested_campuses, created_at).
        """
        import faculty as registry
        svc = await get_service()
        if getattr(svc, "role", None) != "admin":
            raise PermissionError("admin only")
        return {"pending": registry.list_pending_requests()}
