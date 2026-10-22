-- HARDEN: lock down the login-ledger RPC + read grant flagged by the Supabase security
-- advisor (lints 0028/0029 "public can execute SECURITY DEFINER function", plus a stray
-- SELECT grant on the ledger).
--   1. record_mcp_login is SECURITY DEFINER, so it runs with the owner's rights and bypasses
--      RLS. It was EXECUTE-able by anon + authenticated, letting anyone holding the gateway/
--      anon key forge mcp_logins rows (spoof the audit ledger). The MCP writes the ledger as
--      reporting_readonly, so EXECUTE stays for that role (and service_role / postgres); only
--      the public roles lose it.
--   2. mcp_logins carries PII (who signed in, their role + campuses). anon + authenticated held
--      a stray SELECT grant — already blocked by RLS-with-no-policy, but it has no legitimate
--      caller, so remove it (defense-in-depth: the table must never depend on RLS alone).
-- Idempotent: revoking an absent grant is a harmless no-op.
-- Apply with the Supabase migration role (SQL editor / CLI), never an application credential.
-- Project: sadbfvfcmmxgtatfjfmc

revoke execute on function public.record_mcp_login(text, text, text, jsonb) from anon, authenticated;
revoke select on table public.mcp_logins from anon, authenticated;
