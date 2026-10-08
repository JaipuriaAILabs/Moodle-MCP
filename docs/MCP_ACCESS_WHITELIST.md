# Moodle MCP data-access whitelist

Effective 2026-10-08, Moodle MCP data access is deny-by-default and backed by the
dedicated Supabase table `public.mcp_faculty`. The server must run with:

- `MCP_RBAC_MODE=enforce`
- `OAUTH_DEFAULT_CAMPUSES=none`
- `MCP_SELF_SERVICE_ACCESS=false`

An authenticated email not listed below receives no MCP data access.

## Institution-wide access

These users have role `cross_campus`, can query all four campuses, and can
generate reports. This role does not grant access-request administration.

| Email | Campus scope |
|---|---|
| shreevats@jaipuria.ac.in | All campuses |
| shiva.kakkar@jaipuria.ac.in | All campuses |
| prabhat.pankaj@jaipuria.ac.in | All campuses |
| shailaja.sharma@jaipuria.ac.in | All campuses |
| amit.attry@jaipuria.ac.in | All campuses |
| sauresh.mehrotra@jaipuria.ac.in | All campuses |
| kanchan.rana@jaipuria.ac.in | All campuses |
| sandip.das@jaipuria.ac.in | All campuses |

## Campus-specific dean access

These users have role `campus_admin`, can query and generate reports only for
their assigned campus, and receive empty results for any other campus.

| Campus | Email |
|---|---|
| Noida | rahul.singh@jaipuria.ac.in |
| Lucknow | sushma.vishnani@jaipuria.ac.in |
| Jaipur | daneshwar.sharma@jaipuria.ac.in |
| Indore | deepankar.chakrabarti@jaipuria.ac.in |

## Change control

Apply `sql/2026-10-08_mcp_access_whitelist.sql` with a Supabase migration or
service-role credential. The migration deactivates non-whitelisted grants rather
than deleting them. All changes are captured by `mcp_faculty_history`.

The MCP runtime uses a SELECT-only database role. Registry writes must remain
restricted to a migration/service-role operator.
