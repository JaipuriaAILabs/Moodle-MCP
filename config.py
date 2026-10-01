"""Configuration for the Moodle Reports MCP (pydantic-settings, .env + env vars)."""
import json
import logging
import os
from urllib.parse import urlsplit

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

log = logging.getLogger(__name__)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Supabase (read-only service role — server-side only, never exposed to the host)
    supabase_url: str = Field(default="", alias="SUPABASE_URL")
    supabase_service_role_key: str = Field(default="", alias="SUPABASE_SERVICE_ROLE_KEY")
    # New deployments use separate credentials for each trust boundary.  The legacy
    # SUPABASE_SERVICE_ROLE_KEY remains a temporary data-reader fallback so an
    # existing deployment can roll forward without an outage.
    supabase_data_key: str = Field(default="", alias="SUPABASE_DATA_KEY")
    supabase_oauth_storage_key: str = Field(default="", alias="SUPABASE_OAUTH_STORAGE_KEY")
    supabase_audit_key: str = Field(default="", alias="SUPABASE_AUDIT_KEY")
    # Anon/publishable key. When set, it is used as the API-gateway `apikey` while
    # SUPABASE_DATA_KEY is attached as the bearer — so the DB key can be a
    # least-privilege custom-role JWT (e.g. reporting_readonly) that the gateway
    # would otherwise reject as an apikey. Leave empty to use the DB key for both.
    supabase_anon_key: str = Field(default="", alias="SUPABASE_ANON_KEY")

    # Access: a single admin token (all campuses) and/or a JSON map token -> {name, campuses}
    mcp_admin_token: str = Field(default="", alias="MCP_ADMIN_TOKEN")
    mcp_tokens_raw: str = Field(default="", alias="MCP_TOKENS")

    # Google OAuth sign-in via the Jaipuria Google Workspace.
    # When both client id and secret are set, the server exposes the full MCP OAuth flow
    # (discovery metadata, dynamic client registration, Google consent) so hosts like
    # Claude.ai onboard each user via their jaipuria.ac.in Google account — no manual
    # bearer token. Static MCP_TOKENS/MCP_ADMIN_TOKEN stay as a headless fallback only
    # when OAuth is NOT configured.
    google_oauth_client_id: str = Field(default="", alias="GOOGLE_OAUTH_CLIENT_ID")
    google_oauth_client_secret: str = Field(default="", alias="GOOGLE_OAUTH_CLIENT_SECRET")
    # Comma-separated allowed email domains for signed-in users (server-side gate,
    # in addition to marking the Google OAuth app "Internal" to the Workspace).
    oauth_allowed_domains_raw: str = Field(default="jaipuria.ac.in",
                                           alias="OAUTH_ALLOWED_DOMAINS")
    # Default for OTHER allowed domains with no explicit grant. Verified Jaipuria
    # IDs always receive all campuses, regardless of this setting.
    oauth_default_campuses_raw: str = Field(default="none", alias="OAUTH_DEFAULT_CAMPUSES")
    # Optional per-email overrides: JSON map email -> {name?, campuses} (null = all).
    mcp_faculty_raw: str = Field(default="", alias="MCP_FACULTY")
    # Optional stable key so issued OAuth tokens survive a restart/redeploy.
    oauth_jwt_signing_key: str = Field(default="", alias="OAUTH_JWT_SIGNING_KEY")
    oauth_storage_encryption_key: str = Field(
        default="", alias="OAUTH_STORAGE_ENCRYPTION_KEY")
    oauth_redirect_hosts_raw: str = Field(default="", alias="OAUTH_REDIRECT_HOSTS")
    oauth_allow_cross_client_pkce: bool = Field(
        default=False, alias="OAUTH_ALLOW_CROSS_CLIENT_PKCE")

    # Report generation (create_report tool): the moodle-agent report service.
    # Production queues use a dedicated HMAC key. Legacy synchronous rollback uses
    # Basic credentials. Unset -> the tool reports itself unavailable.
    # The DB credential here stays SELECT-only; all writes happen in the agent.
    agent_api_base: str = Field(default="", alias="AGENT_API_BASE")
    agent_admin_user: str = Field(default="", alias="AGENT_ADMIN_USER")
    agent_admin_pass: str = Field(default="", alias="AGENT_ADMIN_PASS")
    agent_shared_secret: str = Field(default="", alias="AGENT_SHARED_SECRET")
    agent_report_queue: bool = Field(default=True, alias="AGENT_REPORT_QUEUE")

    # Identity / reports
    server_name: str = Field(default="jaipuria-moodle-mcp", alias="MCP_SERVER_NAME")
    server_version: str = Field(default="1.0.0", alias="MCP_SERVER_VERSION")
    server_base_url: str = Field(default="", alias="MCP_SERVER_BASE_URL")

    @field_validator("server_base_url")
    @classmethod
    def _no_trailing_slash(cls, v: str) -> str:
        # RFC 8414 §3.3: the OAuth issuer must be an EXACT string. A trailing slash makes the
        # published issuer "https://…/", which fails strict issuer checks (e.g. the Cloudflare
        # OS gatekeeper's @modelcontextprotocol/client) and the AIA-1390/1391 acceptance
        # criteria. Normalise here so the value drives GoogleProvider's issuer without a slash,
        # regardless of how MCP_SERVER_BASE_URL is entered in the deploy env.
        return v.rstrip("/") if v else v
    report_public_base_url: str = Field(
        default="https://reports.tryrehearsal.ai", alias="REPORT_PUBLIC_BASE_URL")
    storage_bucket: str = Field(default="student-reports", alias="STORAGE_BUCKET")
    report_purpose: str = Field(default="final", alias="REPORT_PURPOSE")

    # Rate limiting — tool calls per token per window (bounded, in-process)
    rate_limit: int = Field(default=90, alias="MCP_RATE_LIMIT")
    rate_window_seconds: int = Field(default=60, alias="MCP_RATE_WINDOW_SECONDS")
    # Dedicated per-principal budget for create_report — the one tool that spends money
    # (an LLM generation on a cache miss) and, under the open-access model, can be
    # invoked for ANY student by ANY verified user. Bounds worst-case model cost/load
    # well below the generic 90/min tool limit. Generous enough for a professor to
    # generate a whole class in one sitting; low enough to stop runaway/abusive loops.
    create_report_limit: int = Field(default=60, alias="MCP_CREATE_REPORT_LIMIT")
    create_report_window_seconds: int = Field(
        default=3600, alias="MCP_CREATE_REPORT_WINDOW_SECONDS")
    # Transport hardening: max /mcp request body, and a per-IP pre-auth request cap
    # per window (blunts unauthenticated floods / token-guessing before auth).
    max_body_bytes: int = Field(default=262144, alias="MCP_MAX_BODY_BYTES")
    # 1200/min default: hundreds of faculty on a campus share ONE egress IP (NAT),
    # so a lower cap throttles legitimate use. Still a real flood brake (20 rps),
    # and the per-principal limit above bounds each individual account.
    ip_rate_limit: int = Field(default=1200, alias="MCP_IP_RATE_LIMIT")
    rate_limit_max_keys: int = Field(default=16384, alias="MCP_RATE_LIMIT_MAX_KEYS")
    redis_url: str = Field(default="", alias="MCP_REDIS_URL")
    trust_proxy_headers: bool = Field(default=False, alias="MCP_TRUST_PROXY_HEADERS")
    allowed_hosts_raw: str = Field(default="", alias="MCP_ALLOWED_HOSTS")
    audit_hmac_key: str = Field(default="", alias="MCP_AUDIT_HMAC_KEY")
    require_audit: bool = Field(default=False, alias="MCP_REQUIRE_AUDIT")
    # Activity capture (AIA-1012 telemetry). Each flag WIDENS the audit metadata
    # beyond the privacy-safe default. All default OFF, so enabling audit alone
    # changes nothing; turning these on records real identity / full tool
    # arguments / result payloads / source IP into mcp_audit.tool_calls.metadata.
    # These hold student PII + who-viewed-whom — keep the audit store locked-down,
    # set a retention/consent posture, and never mirror them to New Relic in clear.
    capture_identity: bool = Field(default=False, alias="MCP_CAPTURE_IDENTITY")
    capture_arguments: bool = Field(default=False, alias="MCP_CAPTURE_ARGUMENTS")
    capture_results: bool = Field(default=False, alias="MCP_CAPTURE_RESULTS")
    capture_client_ip: bool = Field(default=False, alias="MCP_CAPTURE_CLIENT_IP")
    # Generous defaults so a realistic tool call (a full student record, a 50-row
    # roster, a cohort rollup) is captured COMPLETELY rather than truncated; only a
    # pathological payload trips the cap (then a size-marked preview is stored).
    capture_args_max_bytes: int = Field(default=32768, alias="MCP_CAPTURE_ARGS_MAX_BYTES")
    capture_result_max_bytes: int = Field(default=262144, alias="MCP_CAPTURE_RESULT_MAX_BYTES")

    # OpenTelemetry tracing -> New Relic (EU). Enabled only when NEW_RELIC_LICENSE_KEY
    # (or an explicit MCP_OTEL_HEADERS override) is set; otherwise the server runs with
    # zero telemetry and no OTel SDK is loaded. Endpoint defaults to New Relic's EU OTLP.
    new_relic_license_key: str = Field(default="", alias="NEW_RELIC_LICENSE_KEY")
    otel_endpoint: str = Field(default="https://otlp.eu01.nr-data.net", alias="MCP_OTEL_ENDPOINT")
    otel_headers_raw: str = Field(default="", alias="MCP_OTEL_HEADERS")
    # Signal toggles (all inert unless otel_enabled()). Traces + metrics on by default;
    # logs OFF by default because app logs can carry operational detail we deliberately
    # keep out of New Relic — opt in with MCP_OTEL_LOGS=true only after confirming the
    # log stream is PII-free (httpx access-token URLs are already quieted at boot).
    otel_traces: bool = Field(default=True, alias="MCP_OTEL_TRACES")
    otel_metrics: bool = Field(default=True, alias="MCP_OTEL_METRICS")
    otel_logs: bool = Field(default=False, alias="MCP_OTEL_LOGS")
    # Metric export cadence (ms). New Relic recommends ≥ 5s; 60s is plenty for
    # host/process gauges + tool counters and keeps ingest/cost low at 5k users.
    otel_metric_interval_ms: int = Field(default=60000, alias="MCP_OTEL_METRIC_INTERVAL_MS")
    # Head sampling ratio for our SERVER entry spans (0..1, default 1.0 = keep all).
    # Our spans are the primary operational signal, so we NEVER let an upstream's
    # "unsampled" traceparent demote them below this ratio (see telemetry._build_sampler);
    # a sampled upstream is always honored so distributed traces stay complete. Dial down
    # (e.g. 0.2) only if span volume/cost bites at 5k — error/throughput RATES survive in
    # the mcp.tool.* metrics regardless, since metrics ignore trace sampling.
    otel_sample_ratio: float = Field(default=1.0, alias="MCP_OTEL_SAMPLE_RATIO")
    # Distinguishes one running instance from another once horizontally scaled — set to
    # the platform's instance/dyno id, else the hostname is used. Attached as
    # service.instance.id on every span/metric/log so a single bad node is isolable.
    service_instance_id: str = Field(default="", alias="MCP_SERVICE_INSTANCE_ID")
    # Reject access tokens shorter than this at boot (set ALLOW_WEAK_TOKENS to skip).
    allow_weak_tokens: bool = Field(default=False, alias="ALLOW_WEAK_TOKENS")
    # Bypass resistance (AIA-1013 #4): when GATEWAY_ENFORCED, /mcp only accepts requests
    # carrying the gateway's shared secret — reachable only THROUGH the MCP gateway.
    # OAuth is unchanged (an additional gate). Defaults OFF — inert until cutover.
    gateway_enforced: bool = Field(default=False, alias="GATEWAY_ENFORCED")
    gateway_shared_secret: str = Field(default="", alias="GATEWAY_SHARED_SECRET")

    # --- per-campus RBAC (AIA campus scoping). Default OFF preserves today's behavior.
    #   off     : every verified jaipuria.ac.in account keeps all-campus access (the
    #             historical "G1 all-access" policy) — the mcp_faculty registry is not
    #             consulted for Jaipuria emails, so this path is unchanged and DB-free.
    #   shadow  : SERVE all-access unchanged, but for each Jaipuria caller also compute
    #             and LOG (prefix "rbac.shadow") the grant that ENFORCE would apply —
    #             role + campus scope, or a would-deny — so the roster can be validated
    #             against real traffic before flipping.
    #   enforce : the mcp_faculty registry is authoritative for every account — role +
    #             campus scope per email; unlisted / inactive / expired / student -> deny.
    rbac_mode_raw: str = Field(default="off", alias="MCP_RBAC_MODE")
    # Canonical campus codes (lowercase), used to validate grants against known campuses.
    campuses_raw: str = Field(default="noida,lucknow,jaipur,indore", alias="MCP_CAMPUSES")

    def gateway_secrets(self) -> list[str]:
        return [s.strip() for s in self.gateway_shared_secret.split(",") if s.strip()]

    def tokens(self) -> dict:
        """token -> {name, campuses(None=all)}. Admin token grants all campuses."""
        out = {}
        if self.mcp_tokens_raw.strip():
            try:
                out.update(json.loads(self.mcp_tokens_raw))
            except json.JSONDecodeError:
                log.warning("MCP_TOKENS is not valid JSON; ignoring")
        if self.mcp_admin_token:
            out[self.mcp_admin_token] = {"name": "admin", "campuses": None}
        return out

    def oauth_enabled(self) -> bool:
        return bool(self.google_oauth_client_id and self.google_oauth_client_secret)

    def report_generation_enabled(self) -> bool:
        if not self.agent_api_base:
            return False
        if self.agent_report_queue:
            return bool(self.agent_shared_secret)
        return bool(self.agent_admin_user and self.agent_admin_pass)

    def data_key(self) -> str:
        return self.supabase_data_key or self.supabase_service_role_key

    def oauth_storage_key(self) -> str:
        return self.supabase_oauth_storage_key

    def audit_enabled(self) -> bool:
        return bool(self.supabase_audit_key)

    def otel_enabled(self) -> bool:
        return bool(self.new_relic_license_key.strip() or self.otel_headers_raw.strip())

    def otel_traces_endpoint(self) -> str:
        return self.otel_endpoint.rstrip("/") + "/v1/traces"

    def otel_metrics_endpoint(self) -> str:
        return self.otel_endpoint.rstrip("/") + "/v1/metrics"

    def otel_logs_endpoint(self) -> str:
        return self.otel_endpoint.rstrip("/") + "/v1/logs"

    def otel_instance_id(self) -> str:
        """Stable per-instance id for service.instance.id. Explicit override wins;
        otherwise the hostname (container/dyno id on most platforms)."""
        if self.service_instance_id.strip():
            return self.service_instance_id.strip()
        try:
            import socket
            return socket.gethostname() or "unknown"
        except Exception:  # noqa: BLE001
            return "unknown"

    def otel_headers(self) -> dict:
        """OTLP exporter headers. An explicit MCP_OTEL_HEADERS ('k=v,k2=v2') wins;
        otherwise New Relic's ingest header (api-key: <license key>)."""
        if self.otel_headers_raw.strip():
            return {k.strip(): v.strip() for k, v in
                    (kv.split("=", 1) for kv in self.otel_headers_raw.split(",") if "=" in kv)}
        key = self.new_relic_license_key.strip()
        return {"api-key": key} if key else {}

    def oauth_redirect_hosts(self) -> list[str]:
        return [host.strip().lower() for host in self.oauth_redirect_hosts_raw.split(",")
                if host.strip()]

    def allowed_hosts(self) -> list[str]:
        configured = [host.strip().lower() for host in self.allowed_hosts_raw.split(",")
                      if host.strip()]
        if configured:
            return configured
        host = urlsplit(self.server_base_url).hostname
        return [host.lower()] if host else []

    def oauth_allowed_domains(self) -> list:
        """Lower-cased email domains allowed to sign in (empty = deny everyone)."""
        return [d.strip().lower() for d in self.oauth_allowed_domains_raw.split(",")
                if d.strip()]

    def faculty(self) -> dict:
        """email (lower) -> {name?, campuses} overrides. Malformed JSON -> {} (boot validates)."""
        if not self.mcp_faculty_raw.strip():
            return {}
        try:
            parsed = json.loads(self.mcp_faculty_raw)
            return {str(k).strip().lower(): v for k, v in parsed.items()} \
                if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            log.warning("MCP_FACULTY is not valid JSON; ignoring")
            return {}

    def rbac_mode(self) -> str:
        """Normalised per-campus RBAC mode: 'off' | 'shadow' | 'enforce' (bad value -> 'off')."""
        mode = self.rbac_mode_raw.strip().lower()
        return mode if mode in ("off", "shadow", "enforce") else "off"

    def campuses(self) -> list[str]:
        """Canonical lowercase campus codes for grant validation (e.g. noida/lucknow/jaipur/indore)."""
        return [c.strip().lower() for c in self.campuses_raw.split(",") if c.strip()]

    def oauth_default_campuses(self):
        """Default grant for a domain-verified email with no MCP_FACULTY entry.
        Returns None (all campuses), a list, or the sentinel string 'deny'."""
        raw = self.oauth_default_campuses_raw.strip()
        if raw.lower() in ("all", "null"):
            return None
        if raw.lower() in ("", "none"):
            return "deny"
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list) and all(
                    isinstance(campus, str) and campus.strip() for campus in parsed):
                return list(dict.fromkeys(campus.strip().lower() for campus in parsed))
            return "deny"
        except json.JSONDecodeError:
            return "deny"


settings = Settings()


def _valid_expires(exp) -> bool:
    """True if `exp` parses as an ISO date or datetime."""
    try:
        from datetime import datetime
        datetime.fromisoformat(str(exp).strip().replace("Z", "+00:00"))
        return True
    except Exception:
        return False


def _valid_campuses(campuses) -> bool:
    """A campus grant is either explicit all (None) or a list of non-empty names."""
    return campuses is None or (
        isinstance(campuses, list)
        and all(isinstance(campus, str) and campus.strip() for campus in campuses)
    )


def key_role(key: str) -> str | None:
    """Best-effort read of a Supabase key's JWT `role` claim (payload only, no
    signature check — used purely to warn when the RLS-bypassing service_role key
    is in use). Returns None for a non-JWT / opaque key."""
    try:
        import base64
        payload = key.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload)).get("role")
    except Exception:
        return None


def validate_config() -> None:
    """Fail-closed boot check: required Supabase vars, and a well-formed access-token config.
    Malformed MCP_TOKENS raises at boot (visible) rather than silently locking everyone out."""
    missing = [k for k, v in {
        "SUPABASE_URL": settings.supabase_url,
        "SUPABASE_DATA_KEY or SUPABASE_SERVICE_ROLE_KEY": settings.data_key(),
    }.items() if not v]
    if missing:
        raise RuntimeError(f"missing required config: {', '.join(missing)}")

    # Per-campus RBAC mode must be a known value (a typo must fail loudly at boot, not
    # silently fall back to 'off' and leave the operator believing scoping is on).
    if settings.rbac_mode_raw.strip().lower() not in ("", "off", "shadow", "enforce"):
        raise RuntimeError("MCP_RBAC_MODE must be one of: off, shadow, enforce")

    # Strict, fail-closed validation of MCP_TOKENS shape.
    if settings.mcp_tokens_raw.strip():
        try:
            parsed = json.loads(settings.mcp_tokens_raw)
        except json.JSONDecodeError as e:
            raise RuntimeError(f"MCP_TOKENS is not valid JSON: {e}") from e
        if not isinstance(parsed, dict):
            raise RuntimeError("MCP_TOKENS must be a JSON object of token -> {name, campuses}")
        for tok, pr in parsed.items():
            if not isinstance(pr, dict) or "campuses" not in pr:
                raise RuntimeError("each MCP_TOKENS entry must be an object with a 'campuses' key")
            campuses = pr.get("campuses")
            if not _valid_campuses(campuses):
                raise RuntimeError("MCP_TOKENS 'campuses' must be null (all) or a list "
                                   "of non-empty campus names")
            if len(tok) < 24 and not settings.allow_weak_tokens:
                raise RuntimeError("an MCP token is short (<24 chars) — use "
                                   "`secrets.token_urlsafe(24)`, or set ALLOW_WEAK_TOKENS=true")
            exp = pr.get("expires")
            if exp is not None and not _valid_expires(exp):
                raise RuntimeError("MCP_TOKENS 'expires' must be an ISO date/datetime "
                                   "(e.g. '2026-12-31' or '2026-12-31T23:59:59Z')")

    if settings.mcp_admin_token and len(settings.mcp_admin_token) < 24 \
            and not settings.allow_weak_tokens:
        raise RuntimeError("MCP_ADMIN_TOKEN is short (<24 chars) — use a high-entropy "
                           "value, or set ALLOW_WEAK_TOKENS=true")

    # Least-privilege DB credential: warn when the RLS-bypassing service_role key
    # is configured. Prefer a scoped, SELECT-only role.
    role = key_role(settings.data_key())
    if role == "service_role":
        log.warning("SUPABASE_SERVICE_ROLE_KEY is a full service_role key (bypasses RLS and can "
                    "write). Prefer a SELECT-only 'reporting_readonly' JWT — see "
                    "sql/2026-08-26_reporting_readonly_role.sql and the README.")
    elif role:
        log.info("DB key role: %s (non-service_role, least-privilege)", role)

    # OAuth mode: fail-closed on a half-configured setup.
    if settings.google_oauth_client_id or settings.google_oauth_client_secret:
        if not settings.oauth_enabled():
            raise RuntimeError("Google OAuth is half-configured: set BOTH "
                               "GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET")
        if not settings.server_base_url.startswith("https://"):
            raise RuntimeError("OAuth requires MCP_SERVER_BASE_URL to be the server's "
                               "public https URL (used for OAuth discovery + the "
                               "Google redirect URI)")
        if not settings.oauth_allowed_domains():
            raise RuntimeError("OAUTH_ALLOWED_DOMAINS is empty — every sign-in would "
                               "be rejected; set it (default: jaipuria.ac.in)")
        if settings.mcp_faculty_raw.strip():
            try:
                parsed = json.loads(settings.mcp_faculty_raw)
            except json.JSONDecodeError as e:
                raise RuntimeError(f"MCP_FACULTY is not valid JSON: {e}") from e
            if not isinstance(parsed, dict):
                raise RuntimeError("MCP_FACULTY must be a JSON object of "
                                   "email -> {name, campuses}")
            for email, pr in parsed.items():
                if not isinstance(pr, dict):
                    raise RuntimeError("each MCP_FACULTY entry must be an object")
                if "campuses" not in pr:
                    raise RuntimeError("each MCP_FACULTY entry must include a 'campuses' key; "
                                       "use null only for an intentional all-campus grant")
                campuses = pr.get("campuses")
                if not _valid_campuses(campuses):
                    raise RuntimeError("MCP_FACULTY 'campuses' must be null (all) or a list "
                                       "of non-empty campus names")
        raw_default = settings.oauth_default_campuses_raw.strip()
        if raw_default.lower() not in ("", "all", "null", "none"):
            try:
                parsed_default = json.loads(raw_default)
            except json.JSONDecodeError as e:
                raise RuntimeError("OAUTH_DEFAULT_CAMPUSES must be 'none', 'all', or a JSON "
                                   "list of campus names") from e
            if not isinstance(parsed_default, list) or not _valid_campuses(parsed_default):
                raise RuntimeError("OAUTH_DEFAULT_CAMPUSES must be 'none', 'all', or a JSON "
                                   "list of non-empty campus names")
        mode = settings.rbac_mode()
        log.info("per-campus RBAC mode: %s (campuses=%s)", mode, ",".join(settings.campuses()))
        if mode == "off":
            log.info("RBAC off — every verified jaipuria.ac.in Google account has all-campus access")
        elif mode == "shadow":
            log.info("RBAC shadow — serving all-campus access; logging would-be grants "
                     "(prefix 'rbac.shadow') for roster validation, no user-facing change")
        else:
            log.warning("RBAC ENFORCE — access is role/campus-scoped from the mcp_faculty "
                        "registry; unlisted/inactive/student jaipuria.ac.in accounts are DENIED")
            if settings.oauth_default_campuses() != "deny":
                log.warning("RBAC enforce with OAUTH_DEFAULT_CAMPUSES != none: any allowed-domain "
                            "account WITHOUT an mcp_faculty row still receives the default grant. "
                            "Set OAUTH_DEFAULT_CAMPUSES=none for strict registry-only access.")
        if not settings.oauth_jwt_signing_key:
            log.warning("OAUTH_JWT_SIGNING_KEY not set — issued OAuth tokens are "
                        "invalidated on every restart/redeploy (users must re-login)")
        if settings.supabase_oauth_storage_key and not settings.oauth_storage_encryption_key:
            raise RuntimeError("SUPABASE_OAUTH_STORAGE_KEY requires an independent "
                               "OAUTH_STORAGE_ENCRYPTION_KEY")
        if settings.oauth_allow_cross_client_pkce and not settings.oauth_redirect_hosts():
            raise RuntimeError("OAUTH_ALLOW_CROSS_CLIENT_PKCE requires OAUTH_REDIRECT_HOSTS "
                               "so the compatibility exception is bounded")

    if settings.audit_enabled() and not settings.audit_hmac_key:
        raise RuntimeError("SUPABASE_AUDIT_KEY requires MCP_AUDIT_HMAC_KEY so identities "
                           "and network attributes are pseudonymised before storage")
    if settings.require_audit and not settings.audit_enabled():
        raise RuntimeError("MCP_REQUIRE_AUDIT is enabled but SUPABASE_AUDIT_KEY is missing")

    # create_report backend: fail-closed on a half-configured setup, and require https
    # so the Basic credentials never travel in the clear.
    agent_bits = (settings.agent_api_base, settings.agent_admin_user,
                  settings.agent_admin_pass, settings.agent_shared_secret)
    if any(agent_bits) and not settings.agent_api_base:
        raise RuntimeError("report generation is half-configured: AGENT_API_BASE is required")
    if settings.agent_api_base and settings.agent_report_queue:
        if not settings.agent_shared_secret:
            raise RuntimeError("AGENT_SHARED_SECRET is required when AGENT_REPORT_QUEUE=true")
        if len(settings.agent_shared_secret) < 32:
            raise RuntimeError("AGENT_SHARED_SECRET must be at least 32 characters")
    if settings.agent_api_base and not settings.agent_report_queue \
            and not (settings.agent_admin_user and settings.agent_admin_pass):
        raise RuntimeError("legacy synchronous report generation requires both "
                           "AGENT_ADMIN_USER and AGENT_ADMIN_PASS")
    if settings.agent_api_base and not settings.agent_api_base.startswith("https://"):
        raise RuntimeError("AGENT_API_BASE must be an https URL")

    if not settings.tokens() and not settings.oauth_enabled():
        log.warning("no auth configured (Google OAuth or MCP_ADMIN_TOKEN / MCP_TOKENS) — "
                    "server will reject every request until one is set")
