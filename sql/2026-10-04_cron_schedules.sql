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

-- 2) External /health keepalive — every 10 min (live: cron.job id 1). Documented here so
--    it isn't silently lost; the authoritative uptime signal is the New Relic Synthetics
--    monitor (monitoring/newrelic_uptime_alert.sh), not this ping.
-- select cron.schedule(
--   'mcp-health-keepalive', '*/10 * * * *',
--   $$select net.http_get(url := 'https://moodle-mcp.tryrehearsal.ai/health', timeout_milliseconds := 5000);$$
-- );

-- FOLLOW-UP (gap G18): the mcp_audit.tool_calls partitions are pre-created through 2027-07-01
-- (sql/2026-09-18_mcp_audit_backend.sql) with a `default` catch-all after that. Add a
-- partition-maintenance job before mid-2027 so post-2027 rows don't accumulate unpartitioned.
