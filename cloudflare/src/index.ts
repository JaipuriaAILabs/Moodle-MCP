/**
 * AIA-1391 — Moodle MCP on Cloudflare Containers.
 *
 * A thin Worker that fronts the EXISTING audited Python MCP (../Dockerfile) running in a single
 * Durable-Object-backed container. No application logic lives here — all OAuth, RBAC/campus
 * scoping, auditing and tools stay in the Python app unchanged (the whole point of the Containers
 * path: zero rewrite of security-sensitive code).
 *
 * Two deliberate behaviours:
 *  1. ONE pinned instance (`getContainer(..., "singleton")`). The app keeps FastMCP streamable-HTTP
 *     sessions and the in-process rate limiter in memory, so every request must reach the same
 *     container — the single-process model it had on Render. (OAuth client/token state is already
 *     in Supabase, so it is instance-independent.)
 *  2. Forward the real client IP. The Python app records source IP and rate-limits off the
 *     RIGHTMOST X-Forwarded-For (MCP_TRUST_PROXY_HEADERS=true). Behind the Worker→container hop the
 *     client IP is in CF-Connecting-IP, so we set X-Forwarded-For to exactly that single value.
 */
import { Container, getContainer } from "@cloudflare/containers";

// Config + secrets the Python app reads. Forwarded from the Worker env (wrangler `vars` + secrets)
// into the container's process environment at startup. Keep in sync with ../render.yaml / config.py.
const CONTAINER_ENV_KEYS = [
  // Supabase (least-privilege keys)
  "SUPABASE_URL", "SUPABASE_DATA_KEY", "SUPABASE_OAUTH_STORAGE_KEY", "SUPABASE_AUDIT_KEY", "SUPABASE_ANON_KEY",
  // Google OAuth proxy + token/DCR store encryption
  "GOOGLE_OAUTH_CLIENT_ID", "GOOGLE_OAUTH_CLIENT_SECRET", "OAUTH_JWT_SIGNING_KEY", "OAUTH_STORAGE_ENCRYPTION_KEY",
  "OAUTH_REDIRECT_HOSTS", "OAUTH_ALLOW_CROSS_CLIENT_PKCE", "OAUTH_ALLOWED_DOMAINS", "OAUTH_DEFAULT_CAMPUSES",
  // Server identity / hosts / limits
  "MCP_SERVER_BASE_URL", "MCP_ALLOWED_HOSTS", "MCP_IP_RATE_LIMIT", "MCP_RATE_LIMIT_MAX_KEYS", "MCP_REDIS_URL",
  "MCP_TRUST_PROXY_HEADERS",
  // Audit + telemetry
  "MCP_AUDIT_HMAC_KEY", "MCP_REQUIRE_AUDIT", "NEW_RELIC_LICENSE_KEY", "MCP_OTEL_ENDPOINT",
  "MCP_CAPTURE_IDENTITY", "MCP_CAPTURE_ARGUMENTS", "MCP_CAPTURE_RESULTS", "MCP_CAPTURE_CLIENT_IP",
  "MCP_CAPTURE_ARGS_MAX_BYTES", "MCP_CAPTURE_RESULT_MAX_BYTES",
  // RBAC / access
  "MCP_RBAC_MODE", "MCP_CAMPUSES", "MCP_STUDENT_SELF_ACCESS", "MCP_SELF_SERVICE_ACCESS",
  "MCP_ACCESS_REQUEST_WEBHOOK_URL", "MCP_ACCESS_REQUEST_WEBHOOK_SECRET", "MCP_FACULTY",
  // Report generation (create_report → moodle-agent)
  "AGENT_API_BASE", "AGENT_SHARED_SECRET", "AGENT_REPORT_QUEUE", "REPORT_PUBLIC_BASE_URL",
  // In-server PII redaction (shipped flag-gated)
  "MCP_PII_HMAC_KEY", "MCP_PII_REDACTION_MODE", "MCP_PII_TOKENIZE",
] as const;

interface Env {
  MOODLE_MCP_CONTAINER: DurableObjectNamespace<MoodleMcpContainer>;
  [key: string]: unknown;
}

export class MoodleMcpContainer extends Container<Env> {
  // uvicorn binds ${PORT:-8000}; CF injects no $PORT, so the app listens on 8000.
  defaultPort = 8000;
  // Keep the single stateful instance warm so cold `initialize` stays under the 2s acceptance
  // criterion. Pair with a keepalive cron hitting /health (see README) for a hard guarantee.
  sleepAfter = "6h";

  constructor(ctx: DurableObjectState<{}>, env: Env) {
    super(ctx, env);
    const vars: Record<string, string> = {};
    for (const k of CONTAINER_ENV_KEYS) {
      const v = env[k];
      if (typeof v === "string" && v !== "") vars[k] = v;
    }
    this.envVars = vars;
  }
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const clientIp = request.headers.get("CF-Connecting-IP");
    let forwarded = request;
    if (clientIp) {
      const headers = new Headers(request.headers);
      headers.set("X-Forwarded-For", clientIp); // single value → rightmost = real client
      forwarded = new Request(request, { headers });
    }
    const container = getContainer(env.MOODLE_MCP_CONTAINER, "singleton");
    return container.fetch(forwarded);
  },
} satisfies ExportedHandler<Env>;
