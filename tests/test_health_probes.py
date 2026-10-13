"""Liveness/readiness probes (plain asserts). Calls the handlers directly and stubs the DB,
so the check is deterministic and offline.

Run:  ../moodle-agent/.venv/bin/python tests/test_health_probes.py  (from the moodle-mcp dir)
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-service-key")
for _k in ("GOOGLE_OAUTH_CLIENT_ID", "GOOGLE_OAUTH_CLIENT_SECRET", "MCP_REQUIRE_GOOGLE_OAUTH"):
    os.environ.pop(_k, None)

import security  # noqa: E402
import server  # noqa: E402
import supabase_client  # noqa: E402

PASS, FAIL = 0, 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}")


def _body(resp):
    return json.loads(bytes(resp.body))


print("\n[ HEALTH_PATHS wiring ]")
check("HEALTH_PATHS == /health,/livez,/readyz",
      set(security.HEALTH_PATHS) == {"/health", "/livez", "/readyz"})
# HostGuard and TransportGuard must both treat the probe paths as open.
hg = security.HostGuard(lambda *a: None, allowed_hosts=["moodle-mcp.rehearsal-os.app"])

print("\n[ liveness: always 200, no downstream ]")
r = asyncio.run(server.livez(None))
check("livez -> 200", r.status_code == 200)
check("livez body status=ok", _body(r)["status"] == "ok")
check("health_check -> 200", asyncio.run(server.health_check(None)).status_code == 200)

print("\n[ readiness: 503 when the data dependency is unreachable, never raises ]")
supabase_client._client = lambda: (_ for _ in ()).throw(RuntimeError("no db in test"))
ok, detail = server._readiness_probe()
check("readiness probe returns (bool, str)", isinstance(ok, bool) and isinstance(detail, str))
check("readiness probe is False without a DB", ok is False)
server._readyz_cache.update(ts=0.0, ready=False, detail="cold")  # force a fresh evaluation
r = asyncio.run(server.readyz(None))
check("readyz -> 503 when not ready", r.status_code == 503)
check("readyz body status=not_ready", _body(r)["status"] == "not_ready")

print("\n[ readiness: 200 when the dependency check passes ]")
class _OKQuery:
    def select(self, *a, **k): return self
    def limit(self, *a, **k): return self
    def execute(self): return type("R", (), {"data": [], "count": 0})()
class _OKClient:
    def table(self, *_a, **_k): return _OKQuery()
supabase_client._client = lambda: _OKClient()
server._readyz_cache.update(ts=0.0, ready=False, detail="cold")  # expire the cache
r = asyncio.run(server.readyz(None))
check("readyz -> 200 when deps ok", r.status_code == 200)
check("readyz body status=ready", _body(r)["status"] == "ready")

print("\n[ readiness result is cached (no re-check within TTL) ]")
calls = {"n": 0}
def _counting_client():
    calls["n"] += 1
    return _OKClient()
supabase_client._client = _counting_client
server._readyz_cache.update(ts=0.0, ready=False, detail="cold")
asyncio.run(server.readyz(None))      # one real check
asyncio.run(server.readyz(None))      # served from cache
asyncio.run(server.readyz(None))      # served from cache
check("cached: only one DB check across 3 probes within TTL", calls["n"] == 1)

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
