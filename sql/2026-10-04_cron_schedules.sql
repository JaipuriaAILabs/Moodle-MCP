-- Reproducibility record for the pg_cron schedules that are ALREADY LIVE in the
-- "Moodle Data" project (verified 2026-10-04 via cron.job). The purge function itself
-- ships in sql/2026-09-18_mcp_audit_backend.sql; only the *schedule* lived out-of-band.
-- Re-applying is idempotent (cron.schedule upserts by jobname on pg_cron >= 1.4).
-- Run as a role that can use pg_cron (service_role / postgres), not the MCP's app role.

-- 1) DPDP 180-day retention purge — daily 03:00 UTC (live: cron.job id 3).
select cron.schedule(
  'mcp-security-retention-purge',
  '0 3 * * *',
  $$select public.purge_expired_mcp_security_data();$$
);

-- 2) Reports-backend warm ping — every 10 min (live: jobname 'agent-keepwarm'). Keeps
--    reports.tryrehearsal.ai from spinning down so the first create_report does not cold-start.
--    The authoritative uptime signal is still an external /health check with failure alerting,
--    not this ping. Re-apply (idempotent) if lost:
-- select cron.schedule(
--   'agent-keepwarm', '*/10 * * * *',
--   $$select net.http_get(url := 'https://reports.tryrehearsal.ai/health', timeout_milliseconds := 5000);$$
-- );
--
-- NOTE (2026-10-24): the former MCP warmer ('mcp-keepwarm' / 'mcp-health-keepalive' against
--   moodle-mcp.tryrehearsal.ai) was unscheduled — the MCP runs on a paid always-on plan, so the
--   ping was redundant. Do NOT re-add it.

-- FOLLOW-UP (gap G18): the mcp_audit.tool_calls partitions are pre-created through 2027-07-01
-- (sql/2026-09-18_mcp_audit_backend.sql) with a `default` catch-all after that. Add a
-- partition-maintenance job before mid-2027 so post-2027 rows don't accumulate unpartitioned.
