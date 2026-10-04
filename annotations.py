"""Shared MCP tool annotations. Every tool except create_report is read-only."""

READONLY_ANNOTATIONS = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
}

# create_report triggers generation on the report service (writes an insight cache row there;
# overwrites nothing a caller could lose). NOT idempotent, though: refresh=true forces a fresh
# regeneration + new report_url, and an omitted student_id auto-selects a *random* student — so a
# repeated call can produce a different target/output. Mark it non-idempotent so hosts don't dedupe.
GENERATE_ANNOTATIONS = {
    "readOnlyHint": False,
    "destructiveHint": False,
    "idempotentHint": False,
    "openWorldHint": False,
}
