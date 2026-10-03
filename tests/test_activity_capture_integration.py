"""Integration tests for activity capture — real FastMCP result shapes, the full
record_tool_call → Supabase RPC payload (httpx mocked), and the live GuardMiddleware
wiring (success + error paths). Complements the pure-helper unit test.

Run:  ../moodle-agent/.venv/bin/python tests/test_activity_capture_integration.py
from the moodle-mcp directory.
"""
import asyncio
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-service-key")

import audit_store  # noqa: E402
import security  # noqa: E402
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


def _set(**flags):
    for k, v in flags.items():
        setattr(settings, k, v)


from fastmcp.exceptions import ToolError  # noqa: E402
from fastmcp.tools.tool import ToolResult  # noqa: E402
from mcp.types import TextContent  # noqa: E402

# ==========================================================================
print("\n[ 1. _summarise_result against REAL fastmcp ToolResult ]")
_set(capture_result_max_bytes=8192)

r_struct = ToolResult(structured_content={"student_id": "JN25MM002",
                                          "attendance_pct": 91, "from_cache": True})
s = audit_store._summarise_result(r_struct, 8192)
check("structured_content extracted from real ToolResult",
      isinstance(s, dict) and s.get("student_id") == "JN25MM002")
check("structured fields preserved (from_cache)", s.get("from_cache") is True)

r_text = ToolResult(content=[TextContent(type="text", text="AASHNA  GUPTA report")])
s2 = audit_store._summarise_result(r_text, 8192)
check("content-block text extracted", "AASHNA  GUPTA report" in json.dumps(s2))

r_big = ToolResult(structured_content={"rows": ["x" * 200 for _ in range(200)]})
s3 = audit_store._summarise_result(r_big, 512)
check("oversize real result truncated (not dropped)",
      isinstance(s3, dict) and s3.get("_truncated") is True and s3.get("_bytes", 0) > 512)


# ==========================================================================
print("\n[ 2. record_tool_call -> Supabase RPC payload (httpx mocked) ]")
CAPTURED = {}


class _Resp:
    def raise_for_status(self):
        return None


class _MockClient:
    async def post(self, url, headers=None, json=None):
        CAPTURED["url"] = url
        CAPTURED["payload"] = json
        return _Resp()


audit_store._http = _MockClient()
_set(supabase_audit_key="audit-writer-key", audit_hmac_key="hmac-secret-for-subjects",
     capture_identity=True, capture_arguments=True, capture_results=True,
     capture_client_ip=True)
settings.supabase_url = "https://example.supabase.co"
settings.supabase_anon_key = ""

PRIN = {"email": "faculty@jaipuria.ac.in", "name": "A Faculty", "campuses": None,
        "role": "cross_campus", "can_generate": True}
HDRS = {"user-agent": "JChat/1.0", "mcp-session-id": "sess-abc", "x-request-id": "req-1"}
ARGS = {"params": {"student_id": "Aashna Gupta", "campus": "Jaipur"}}

ok = asyncio.run(audit_store.record_tool_call(
    tool="create_report", principal=PRIN, ok=True, started=time.monotonic(),
    headers=HDRS, scope="Jaipur", arguments=ARGS, result=r_struct, source_ip="10.0.0.9"))
p = CAPTURED.get("payload", {})
check("delivery reported success", ok is True)
check("RPC endpoint is record_mcp_tool_call", "record_mcp_tool_call" in CAPTURED.get("url", ""))
check("outcome=success", p.get("p_outcome") == "success")
check("user_subject is HMAC, NOT the raw email",
      p.get("p_user_subject") != "faculty@jaipuria.ac.in" and len(p.get("p_user_subject", "")) == 64)
check("session + client pseudonymised", bool(p.get("p_session_subject")) and bool(p.get("p_client_subject")))
md = p.get("p_metadata", {})
check("metadata.identity has real email", md.get("identity", {}).get("email") == "faculty@jaipuria.ac.in")
check("metadata.identity keeps campuses=None (all)", "campuses" in md.get("identity", {}))
check("metadata.arguments captured", md.get("arguments", {}).get("params", {}).get("student_id") == "Aashna Gupta")
check("metadata.result captured", md.get("result", {}).get("student_id") == "JN25MM002")
check("metadata.source_ip captured", md.get("source_ip") == "10.0.0.9")

print("\n[ 2b. flags OFF -> payload metadata minimal even when args/result passed ]")
_set(capture_identity=False, capture_arguments=False, capture_results=False, capture_client_ip=False)
asyncio.run(audit_store.record_tool_call(
    tool="create_report", principal=PRIN, ok=True, started=time.monotonic(),
    headers=HDRS, scope="Jaipur", arguments=ARGS, result=r_struct, source_ip="10.0.0.9"))
p = CAPTURED["payload"]
check("flags off -> metadata keeps only version + non-PII authorization",
      set(p["p_metadata"]) == {"server_version", "authorization"})
check("flags off -> authorization role/scope retained",
      p["p_metadata"]["authorization"]["role"] == "cross_campus"
      and p["p_metadata"]["authorization"]["campus_scope"] == "all")
check("flags off -> user still pseudonymised", len(p["p_user_subject"]) == 64)
check("flags off -> raw email absent from payload", "faculty@jaipuria.ac.in" not in json.dumps(p))


# ==========================================================================
print("\n[ 3. live GuardMiddleware wiring (patched deps) ]")
import fastmcp.server.dependencies as _deps  # noqa: E402

# get_http_headers is imported *inside* build_middleware at call time, so patch first.
_deps.get_http_headers = lambda: {"user-agent": "JChat/1.0", "x-request-id": "req-9",
                                  "x-forwarded-for": "9.9.9.9, 10.0.0.1"}
security.resolve_oauth_principal = lambda: PRIN

CALLS = []


async def _fake_record(**kw):
    CALLS.append(kw)
    return True


audit_store.record_tool_call = _fake_record  # local `from audit_store import` picks this up
_set(require_audit=False, capture_identity=True, capture_arguments=True,
     capture_results=True, capture_client_ip=True)

mw = security.build_middleware(90, 60)


class _Msg:
    name = "create_report"
    arguments = {"params": {"student_id": "Aashna Gupta", "campus": "Jaipur"}}


class _Ctx:
    message = _Msg()


async def _ok_next(ctx):
    return ToolResult(structured_content={"student_id": "JN25MM002"})

res = asyncio.run(mw.on_call_tool(_Ctx(), _ok_next))
succ = [c for c in CALLS if c.get("ok") is True]
check("middleware recorded exactly one success", len(succ) == 1)
check("middleware forwarded arguments", succ[0]["arguments"]["params"]["student_id"] == "Aashna Gupta")
check("middleware forwarded the ToolResult object", isinstance(succ[0].get("result"), ToolResult))
check("middleware records resolved campus scope", succ[0].get("scope") == "jaipur")
# X-Forwarded-For "9.9.9.9, 10.0.0.1": the LAST hop (10.0.0.1) is the trusted
# proxy-appended value; the leftmost (9.9.9.9) is client-supplied/spoofable.
check("middleware forwards the TRUSTED (rightmost) X-Forwarded-For hop",
      succ[0].get("source_ip") == "10.0.0.1")
check("middleware returned the tool result unchanged",
      isinstance(res, ToolResult) and res.structured_content.get("student_id") == "JN25MM002")

print("\n[ 3b. error path — call_next raises, result NOT captured, args still are ]")
CALLS.clear()


async def _err_next(ctx):
    raise ToolError("downstream failed")

raised = False
try:
    asyncio.run(mw.on_call_tool(_Ctx(), _err_next))
except ToolError:
    raised = True
check("ToolError re-raised to caller", raised)
err = [c for c in CALLS if c.get("error_code") == "tool_error"]
check("middleware recorded a tool_error event", len(err) == 1)
check("error event carries args but no result",
      err[0].get("arguments") is not None and err[0].get("result") is None)

print("\n[ 3c. require_audit -> pre-execution attempt recorded before the result ]")
CALLS.clear()
_set(require_audit=True)
asyncio.run(mw.on_call_tool(_Ctx(), _ok_next))
outcomes = [c.get("ok") for c in CALLS]
check("attempt (ok=None) recorded before success (ok=True)", outcomes[:2] == [None, True])
check("attempt record has no result yet", CALLS[0].get("result") is None)
_set(require_audit=False)

print("\n[ 4. on_initialize records a 'connect' event (AIA-1210 connection counts) ]")
CALLS.clear()


async def _init_next(ctx):
    return {"protocolVersion": "2025-06-18"}   # a normal initialize result

init_res = asyncio.run(mw.on_initialize(_Ctx(), _init_next))
conn = [c for c in CALLS if c.get("tool") == "connect"]
check("a connect event is recorded on initialize", len(conn) == 1 and conn[0].get("ok") is True)
check("connect event carries identity + source_ip (capture on)",
      conn[0].get("principal", {}).get("email") == "faculty@jaipuria.ac.in"
      and conn[0].get("source_ip") == "10.0.0.1")
check("connect never carries args/result", conn[0].get("arguments") is None and conn[0].get("result") is None)
check("initialize result returned unchanged", init_res == {"protocolVersion": "2025-06-18"})

print("\n[ 4b. a failing audit never breaks the connect ]")
CALLS.clear()


async def _boom_record(**kw):
    raise RuntimeError("audit down")

audit_store.record_tool_call = _boom_record
ok = True
try:
    r = asyncio.run(mw.on_initialize(_Ctx(), _init_next))
    ok = (r == {"protocolVersion": "2025-06-18"})
except Exception:
    ok = False
audit_store.record_tool_call = _fake_record   # restore
check("connect succeeds even if the audit write raises", ok)

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
