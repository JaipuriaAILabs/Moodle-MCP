"""OTel telemetry gating (plain asserts). Verifies tracing is off unless configured,
the New Relic OTLP wiring is derived correctly, setup never raises, and the span
helpers are safe no-ops whether or not a provider/SDK is present.

Run:  ../moodle-agent/.venv/bin/python tests/test_telemetry.py
from the moodle-mcp directory.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-service-key")

import security  # noqa: E402
import telemetry  # noqa: E402
from config import settings  # noqa: E402

PASS, FAIL = 0, 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}")


_ORIG = {k: getattr(settings, k) for k in
         ("new_relic_license_key", "otel_headers_raw", "otel_endpoint")}


def _set(**kw):
    for k, v in kw.items():
        setattr(settings, k, v)


print("\n[ config gating ]")
_set(new_relic_license_key="", otel_headers_raw="")
check("disabled with no key/headers", settings.otel_enabled() is False)
check("setup_telemetry() no-ops to False when disabled", telemetry.setup_telemetry(settings) is False)

_set(new_relic_license_key="NRAK-testkey")
check("enabled once a license key is set", settings.otel_enabled() is True)
check("traces endpoint appends /v1/traces",
      settings.otel_traces_endpoint() == "https://otlp.eu01.nr-data.net/v1/traces")
check("NR ingest header derived from license key",
      settings.otel_headers() == {"api-key": "NRAK-testkey"})

_set(new_relic_license_key="", otel_headers_raw="api-key=abc,x-tenant=jaipuria")
check("explicit MCP_OTEL_HEADERS parsed",
      settings.otel_headers() == {"api-key": "abc", "x-tenant": "jaipuria"})

print("\n[ setup never raises; returns a bool ]")
_set(new_relic_license_key="NRAK-testkey", otel_headers_raw="")
# Do not let an offline test install real exporters or attempt the network when the
# optional SDK is present. setup_telemetry orchestration is what this case covers;
# the signal builders have their own import/error guards in production.
_orig_builders = (telemetry._setup_traces, telemetry._setup_metrics, telemetry._setup_logs)
telemetry._setup_traces = lambda *_args: True
telemetry._setup_metrics = lambda *_args: True
telemetry._setup_logs = lambda *_args: True
res = telemetry.setup_telemetry(settings)
telemetry._setup_traces, telemetry._setup_metrics, telemetry._setup_logs = _orig_builders
check("setup_telemetry returns a bool (no exception)", isinstance(res, bool))

print("\n[ tool span helpers are safe no-ops ]")
span = security._start_tool_span("get_student")   # None, or a (non-recording) span
security._end_tool_span(span, "success", None, "noida")     # must not raise
security._end_tool_span(span, "failure", "unauthorized", None)
security._end_tool_span(None, "failure", "rate_limited", None)  # explicit None path
check("start/end tool span never raise", True)
check("get_tracer() returns something or None", telemetry.get_tracer() is not None or True)
attrs = security._authorization_attributes({
    "role": "cross_campus", "campuses": None,
    "_rbac_shadow": {"outcome": "deny"}})
check("bounded role + shadow outcome telemetry attributes",
      attrs == {"mcp.role": "cross_campus", "mcp.rbac_shadow_outcome": "deny"})
check("unknown roles collapse to bounded 'other' cardinality",
      security._authorization_attributes({"role": "invented", "campuses": []})["mcp.role"]
      == "other")

print("\n[ generic auth span helpers are safe no-ops ]")
asp = telemetry.start_span("mcp.auth")
telemetry.end_span(asp, "success", None, {"mcp.auth.mode": "oauth"})   # must not raise
telemetry.end_span(telemetry.start_span("mcp.auth"), "failure", "unauthorized",
                   {"mcp.auth.mode": "static"})
telemetry.end_span(None, "failure", "auth_error", None)               # explicit None path
telemetry.end_span(None)                                              # no-arg None path
check("start_span/end_span never raise (incl. None)", True)

print("\n[ metrics/logs endpoints + service.instance.id ]")
check("metrics endpoint appends /v1/metrics",
      settings.otel_metrics_endpoint() == "https://otlp.eu01.nr-data.net/v1/metrics")
check("logs endpoint appends /v1/logs",
      settings.otel_logs_endpoint() == "https://otlp.eu01.nr-data.net/v1/logs")
check("instance id falls back to a non-empty hostname",
      isinstance(settings.otel_instance_id(), str) and settings.otel_instance_id() != "")
_orig_instance = settings.service_instance_id
settings.service_instance_id = "render-abc123"
check("explicit MCP_SERVICE_INSTANCE_ID wins", settings.otel_instance_id() == "render-abc123")
settings.service_instance_id = _orig_instance
check("traces + metrics on by default, logs OFF (PII caution)",
      settings.otel_traces is True and settings.otel_metrics is True
      and settings.otel_logs is False)

print("\n[ metric + shutdown helpers are safe no-ops ]")
telemetry.record_tool_metric("get_student", "success", None, "noida", 0.012)  # must not raise
telemetry.record_tool_metric("whoami", "failure", "unauthorized", None, None)
telemetry.record_tool_metric("request_access", "success", None, "none", 0.001,
                             {"mcp.role": "pending",
                              "mcp.rbac_shadow_outcome": "not_applicable"})
telemetry.record_auth_metric("oauth", "success")
telemetry.record_auth_metric("static", "failure")
telemetry.shutdown_telemetry()   # installed or not — idempotent, never raises
telemetry.shutdown_telemetry()   # second call is a no-op
check("record_tool_metric / record_auth_metric / shutdown never raise", True)

print("\n[ W3C trace-context propagation is safe ]")
check("extract_context(None) is None", telemetry.extract_context(None) is None)
# A well-formed traceparent must parse (to a context when OTel present, else None) — never raise.
telemetry.extract_context(
    {"traceparent": "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"})
telemetry.extract_context({"traceparent": "garbage"})            # malformed → safe
security._start_tool_span("get_student", {"traceparent": "bogus"})  # header path must not raise
check("extract_context / span-with-headers never raise", True)

print("\n[ trace sampler: an unsampled upstream must NOT drop our spans ]")
check("otel_sample_ratio defaults to 1.0", settings.otel_sample_ratio == 1.0)
try:
    from opentelemetry.sdk.trace.sampling import Decision, ParentBased
    from opentelemetry.trace import (NonRecordingSpan, SpanContext, TraceFlags,
                                     set_span_in_context)
    smp = telemetry._build_sampler(settings)
    check("sampler is ParentBased", isinstance(smp, ParentBased))
    _parent = SpanContext(trace_id=0x0af7651916cd43dd8448eb211c80319c,
                          span_id=0xb7ad6b7169203331, is_remote=True,
                          trace_flags=TraceFlags(0x00))  # unsampled upstream
    _res = smp.should_sample(set_span_in_context(NonRecordingSpan(_parent)),
                             _parent.trace_id, "mcp.tool.whoami")
    check("unsampled remote parent -> RECORD_AND_SAMPLE (not dropped)",
          _res.decision == Decision.RECORD_AND_SAMPLE)
except ImportError:
    check("sampler behavior (OTel SDK not installed here — skipped)", True)

print("\n[ log-export feedback-loop filter ]")
import logging as _logging
_filt = telemetry._DropExporterLogs()


def _rec(name):
    return _logging.LogRecord(name, _logging.ERROR, __file__, 1, "x", None, None)


check("drops opentelemetry.* export logs", _filt.filter(_rec("opentelemetry.exporter.otlp")) is False)
check("drops urllib3 logs", _filt.filter(_rec("urllib3.connectionpool")) is False)
check("keeps app logs", _filt.filter(_rec("moodle-mcp")) is True)

_set(**_ORIG)
print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
