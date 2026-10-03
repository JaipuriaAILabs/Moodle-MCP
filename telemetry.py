"""OpenTelemetry traces + metrics + logs → New Relic (EU), gated on configuration.

Enabled only when NEW_RELIC_LICENSE_KEY (or an explicit MCP_OTEL_HEADERS override) is
set. When disabled — or when the OTel SDK isn't installed — setup is a no-op and the
server behaves exactly as before. Instrumentation elsewhere uses the OTel *API* only
(a no-op tracer/meter when no provider is installed), so it is always safe to call.

Three signals, each independently toggleable (MCP_OTEL_TRACES/_METRICS/_LOGS) but all
inert unless the key is set:

* **Traces** — one SERVER span per tool call, ``mcp.tool.<name>``, carrying
  ``mcp.tool`` / ``mcp.outcome`` / ``mcp.error_code`` / ``mcp.campus_scope`` and an
  ERROR status on failure; plus an ``mcp.auth`` span per authentication. New Relic
  derives duration.ms, error, throughput and percentiles from these. Incoming W3C
  ``traceparent`` is honoured so a JChat/Claude client span stitches to ours.
* **Metrics** — host + process gauges (CPU, memory, RSS, open FDs …) via the system-
  metrics instrumentation, plus app instruments: a tool-call counter (by outcome) and
  a tool-duration histogram. These give capacity/saturation signal that spans alone
  don't, and aren't subject to trace sampling.
* **Logs** — OFF by default; opt in with MCP_OTEL_LOGS=true to forward app logs.

Every signal carries ``service.name`` / ``service.version`` / ``service.instance.id``
so one instance is distinguishable from another once horizontally scaled.
See monitoring/README.md for the alert NRQL.
"""
import logging

log = logging.getLogger("moodle-mcp.telemetry")

_INSTRUMENTATION = "moodle-mcp"

# Providers + app instruments, kept module-global so shutdown_telemetry() can force-flush
# them on a clean shutdown (see server.py lifespan) and so the metric helpers can reach
# the instruments. All None until setup_telemetry() installs them.
_tracer_provider = None
_meter_provider = None
_logger_provider = None
_logging_handler = None
_tool_calls = None       # Counter
_tool_duration = None    # Histogram (seconds)
_auth_calls = None       # Counter


def _resource(settings):
    from opentelemetry.sdk.resources import Resource
    return Resource.create({
        "service.name": settings.server_name,
        "service.version": settings.server_version,
        "service.instance.id": settings.otel_instance_id(),
    })


def _build_sampler(settings):
    """A sampler that never lets an UNSAMPLED upstream traceparent drop our own SERVER
    spans, while still honoring a SAMPLED upstream so distributed traces stay whole.

    Rationale: the MCP server's spans are the primary operational signal — losing them
    because a client (or proxy) propagated a `-00` traceparent would be silent blindness
    (OTel's default ParentBased inherits that "don't sample"). So:
      * root (no parent)          -> our ratio (default 1.0 = keep all)
      * remote parent SAMPLED     -> always keep (complete cross-service trace)
      * remote parent NOT sampled -> our ratio (override the upstream 'no'; never demoted)
    MCP_OTEL_SAMPLE_RATIO dials the ratio down for volume/cost at scale; error/throughput
    RATES still survive in the mcp.tool.* metrics, which ignore trace sampling.
    """
    from opentelemetry.sdk.trace.sampling import (
        ALWAYS_ON, ParentBased, TraceIdRatioBased)
    ratio = settings.otel_sample_ratio
    ratio = 0.0 if ratio < 0 else (1.0 if ratio > 1 else ratio)
    base = ALWAYS_ON if ratio >= 1.0 else TraceIdRatioBased(ratio)
    return ParentBased(
        root=base,
        remote_parent_sampled=ALWAYS_ON,
        remote_parent_not_sampled=base,
        local_parent_sampled=ALWAYS_ON,
        local_parent_not_sampled=base,
    )


def _setup_traces(settings, resource) -> bool:
    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    global _tracer_provider
    provider = TracerProvider(resource=resource, sampler=_build_sampler(settings))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(
        endpoint=settings.otel_traces_endpoint(),
        headers=settings.otel_headers(),
    )))
    trace.set_tracer_provider(provider)
    _tracer_provider = provider
    log.info("OTel traces enabled → %s (sample_ratio=%s, upstream-unsampled never drops our spans)",
             settings.otel_traces_endpoint(), settings.otel_sample_ratio)
    return True


def _setup_metrics(settings, resource) -> bool:
    from opentelemetry import metrics
    from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
    global _meter_provider, _tool_calls, _tool_duration, _auth_calls
    reader = PeriodicExportingMetricReader(
        OTLPMetricExporter(endpoint=settings.otel_metrics_endpoint(),
                           headers=settings.otel_headers()),
        export_interval_millis=settings.otel_metric_interval_ms,
    )
    provider = MeterProvider(resource=resource, metric_readers=[reader])
    metrics.set_meter_provider(provider)
    _meter_provider = provider

    # Host + process gauges (CPU/mem/RSS/fds). Optional dependency — if it isn't
    # installed we still export the app instruments below.
    try:
        from opentelemetry.instrumentation.system_metrics import SystemMetricsInstrumentor
        if not SystemMetricsInstrumentor().is_instrumented_by_opentelemetry:
            SystemMetricsInstrumentor().instrument(meter_provider=provider)
        log.info("OTel host/process metrics enabled (system-metrics instrumentation)")
    except ImportError:
        log.warning("host/process metrics unavailable "
                    "(pip install opentelemetry-instrumentation-system-metrics) "
                    "— exporting app metrics only")
    except Exception:  # noqa: BLE001 — never let metric setup break boot
        log.warning("system-metrics instrumentation failed", exc_info=True)

    meter = provider.get_meter(_INSTRUMENTATION)
    _tool_calls = meter.create_counter(
        "mcp.tool.calls", unit="1", description="MCP tool invocations by outcome")
    # Milliseconds, not seconds: OTel's default explicit histogram buckets
    # (0,5,10,25,50,75,100,250,500,…) are ms-scaled, so recording seconds would collapse
    # every real call (~10–3000 ms) into the first bucket and make percentiles useless.
    _tool_duration = meter.create_histogram(
        "mcp.tool.duration", unit="ms", description="MCP tool call duration in milliseconds")
    _auth_calls = meter.create_counter(
        "mcp.auth.calls", unit="1", description="MCP authentications by outcome")
    log.info("OTel metrics enabled → %s", settings.otel_metrics_endpoint())
    return True


# Loggers whose records must NOT be shipped through the OTLP log handler: the exporter
# stack itself. Otherwise an export failure logs an error → the root handler captures it
# → tries to export it → fails → logs again … a feedback loop that amplifies under an
# outage (exactly when NR is unreachable). Their records still reach stderr as normal.
_LOG_EXPORT_LOOP_SOURCES = ("opentelemetry", "urllib3", "httpcore", "httpx")


class _DropExporterLogs(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return not record.name.startswith(_LOG_EXPORT_LOOP_SOURCES)


def _setup_logs(settings, resource) -> bool:
    from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
    from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
    from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
    global _logger_provider, _logging_handler
    provider = LoggerProvider(resource=resource)
    provider.add_log_record_processor(BatchLogRecordProcessor(
        OTLPLogExporter(endpoint=settings.otel_logs_endpoint(),
                        headers=settings.otel_headers())))
    handler = LoggingHandler(level=logging.INFO, logger_provider=provider)
    handler.addFilter(_DropExporterLogs())  # break the export-error feedback loop
    logging.getLogger().addHandler(handler)  # ship root-logger records
    _logger_provider = provider
    _logging_handler = handler
    log.info("OTel logs enabled → %s", settings.otel_logs_endpoint())
    return True


def setup_telemetry(settings) -> bool:
    """Install the OTel SDK + New Relic OTLP HTTP exporters for the enabled signals.
    Returns True if ANY signal was enabled, False otherwise. Never raises — a telemetry
    problem must not stop boot."""
    if not settings.otel_enabled():
        log.info("OTel disabled (no NEW_RELIC_LICENSE_KEY / MCP_OTEL_HEADERS)")
        return False
    try:
        # Probe the core SDK once so a missing install degrades cleanly to a no-op
        # rather than half-installing one signal.
        import opentelemetry.sdk.resources  # noqa: F401
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (  # noqa: F401
            OTLPSpanExporter)
    except ImportError:
        log.warning("OTel enabled but the SDK/exporter is not installed "
                    "(pip install opentelemetry-sdk opentelemetry-exporter-otlp-proto-http) "
                    "— telemetry disabled")
        return False

    resource = _resource(settings)
    any_on = False
    for name, flag, fn in (("traces", settings.otel_traces, _setup_traces),
                           ("metrics", settings.otel_metrics, _setup_metrics),
                           ("logs", settings.otel_logs, _setup_logs)):
        if not flag:
            continue
        try:
            any_on = fn(settings, resource) or any_on
        except Exception:  # noqa: BLE001 — one signal failing must not sink the others
            log.warning("OTel %s setup failed — continuing without it", name, exc_info=True)

    if any_on:
        # BatchSpanProcessor / PeriodicExportingMetricReader buffer; flush on process
        # exit so a redeploy / SIGTERM (routine on Render) doesn't silently drop the
        # last batch. atexit is the belt; server.py adds an ASGI-shutdown braces hook
        # because atexit doesn't fire on all container-kill paths.
        import atexit
        atexit.register(shutdown_telemetry)
        log.info("OTel enabled (service=%s instance=%s)",
                 settings.server_name, settings.otel_instance_id())
    return any_on


def shutdown_telemetry() -> None:
    """Force-flush and shut down every installed provider. Idempotent, never raises.
    Called both from the ASGI shutdown lifespan and atexit."""
    global _tracer_provider, _meter_provider, _logger_provider, _logging_handler
    global _tool_calls, _tool_duration, _auth_calls
    if _logging_handler is not None:
        try:
            logging.getLogger().removeHandler(_logging_handler)
        except Exception:  # noqa: BLE001
            pass
    for provider in (_tracer_provider, _meter_provider, _logger_provider):
        if provider is None:
            continue
        # Bound the flush: with New Relic unreachable at shutdown, an unbounded
        # force_flush would block the ASGI shutdown path up to the exporter's ~30s
        # default (then the platform SIGKILLs anyway). 3s is enough to drain a healthy
        # endpoint without stalling a redeploy. Some providers don't accept the kwarg —
        # fall back to a plain call.
        try:
            provider.force_flush(timeout_millis=3000)
        except TypeError:
            try:
                provider.force_flush()
            except Exception:  # noqa: BLE001
                pass
        except Exception:  # noqa: BLE001
            pass
        try:
            provider.shutdown()
        except Exception:  # noqa: BLE001
            pass
    _tracer_provider = _meter_provider = _logger_provider = _logging_handler = None
    _tool_calls = _tool_duration = _auth_calls = None


def extract_context(headers):
    """Parse an incoming W3C ``traceparent`` (case-insensitive dict of request headers)
    into an OTel Context so a tool span becomes a child of the caller's (JChat/Claude)
    span — stitching a distributed trace. None/unparseable → default context. Never
    raises."""
    if not headers:
        return None
    try:
        from opentelemetry.trace.propagation.tracecontext import (
            TraceContextTextMapPropagator)
        return TraceContextTextMapPropagator().extract(dict(headers))
    except Exception:  # noqa: BLE001
        return None


def get_tracer():
    """The OTel API tracer (no-op when no provider is installed). None if the API isn't
    importable, so callers can cheaply skip span work."""
    try:
        from opentelemetry import trace
        return trace.get_tracer(_INSTRUMENTATION)
    except Exception:  # noqa: BLE001
        return None


def start_span(name: str, headers=None):
    """Start a SERVER span, or None when OTel isn't importable / a provider isn't set.
    When ``headers`` carry a W3C traceparent, the span is parented to it. Pair with
    end_span(). Never raises."""
    try:
        from opentelemetry import trace
        ctx = extract_context(headers) if headers else None
        return trace.get_tracer(_INSTRUMENTATION).start_span(
            name, kind=trace.SpanKind.SERVER, context=ctx)
    except Exception:  # noqa: BLE001
        return None


def end_span(span, outcome: str | None = None, error_code=None, attributes=None) -> None:
    """Finish a span from start_span(): stamp outcome/error_code/attributes, mark ERROR
    on failure, and end it. Safe to call with None (no-op). Never raises."""
    if span is None:
        return
    try:
        from opentelemetry.trace import StatusCode
        for key, value in (attributes or {}).items():
            span.set_attribute(key, value)
        if outcome:
            span.set_attribute("mcp.outcome", outcome)
        if error_code:
            span.set_attribute("mcp.error_code", error_code)
        if outcome == "failure":
            span.set_status(StatusCode.ERROR, error_code or "error")
    except Exception:  # noqa: BLE001
        pass
    finally:
        try:
            span.end()
        except Exception:  # noqa: BLE001
            pass


def record_tool_metric(tool: str, outcome: str, error_code=None,
                       scope=None, duration_s: float | None = None,
                       authorization: dict | None = None) -> None:
    """Emit the tool-call counter + duration histogram. No-op when metrics are off.
    Never raises. Attribute cardinality is deliberately bounded (tool + outcome +
    error_code + all/scoped/none + bounded role/shadow outcome) so New Relic
    metric cost stays flat at 5k users."""
    # Disabled hot path (SDK installed but no provider): _end_tool_span calls this on
    # every tool call, so skip building the attrs dict when no instrument exists.
    if _tool_calls is None and _tool_duration is None:
        return
    try:
        scope_class = "all" if not scope or scope == "all" else (
            "none" if scope == "none" else "scoped")
        attrs = {"mcp.tool": tool, "mcp.outcome": outcome,
                 "mcp.error_code": error_code or "none",
                 "mcp.campus_scope": scope_class}
        attrs.update(authorization or {})
        if _tool_calls is not None:
            _tool_calls.add(1, attrs)
        if _tool_duration is not None and duration_s is not None:
            _tool_duration.record(duration_s * 1000.0, attrs)  # seconds → ms
    except Exception:  # noqa: BLE001
        pass


def record_auth_metric(mode: str, outcome: str) -> None:
    """Emit the auth counter (mode = oauth/static, outcome = success/failure).
    No-op when metrics are off. Never raises."""
    try:
        if _auth_calls is not None:
            _auth_calls.add(1, {"mcp.auth.mode": mode, "mcp.outcome": outcome})
    except Exception:  # noqa: BLE001
        pass
