"""Identity resolution for LLM-blind PII (AIA-1356, MCP side).

resolve_identities maps a student NAME or enrolment id → the stable opaque `student_ref`, so the
harness can tokenise identities in a user's prompt BEFORE the model sees them (the response side is
tokenised by pii.tokenise_response). Every lookup goes through find_student, so it inherits the
caller's campus grant and a student's own-rows-only boundary — it can never resolve someone the
caller isn't allowed to see. It returns the canonical id too (the caller is authorised to see this
student), so the harness can hold ref↔id privately and feed only the ref to the model.
"""
from pydantic import BaseModel, Field

from annotations import READONLY_ANNOTATIONS


class ResolveParams(BaseModel):
    students: list[str] = Field(
        description="student names and/or enrolment ids to resolve to opaque refs",
        max_length=100)


def register(mcp, get_service):
    @mcp.tool(title="Resolve Identities", annotations=READONLY_ANNOTATIONS)
    async def resolve_identities(params: ResolveParams) -> dict:
        """
        WHAT: Map each student NAME or enrolment id to a stable opaque reference (student_ref) you
        can show the model in place of real identity, plus the canonical id for your own records.
        USE WHEN: the harness needs to tokenise identities before/around an LLM call (PII-blind mode).
        DO NOT USE WHEN: you just want a student's data — use the data tools.
        RETURNS: resolved[] — {query, resolved, student_ref, student_id, campus}. Scoped to what the
        caller may see (unknown/out-of-scope names resolve to resolved=false).
        """
        import pii
        from fastmcp.exceptions import ToolError
        from tools.common import find_student

        svc = await get_service()
        out = []
        for q in (params.students or [])[:100]:
            query = str(q or "").strip()
            if not query:
                continue
            try:
                row = find_student(svc, query)
            except ToolError:
                # Ambiguous name (several matches) — the caller should pass the exact id.
                out.append({"query": query, "resolved": False, "reason": "ambiguous"})
                continue
            if not row:
                out.append({"query": query, "resolved": False})
                continue
            out.append({"query": query, "resolved": True,
                        "student_ref": pii.student_ref(row["student_id"]),
                        "student_id": row["student_id"], "campus": row.get("campus")})
        return {"resolved": out}
