"""Per-campus RBAC modes (off / shadow / enforce) — plain asserts, no pytest, offline.

Run:  ../moodle-agent/.venv/bin/python tests/test_rbac_modes.py
from the moodle-mcp directory. The registry's DB fetches are monkeypatched, so this
exercises pure authorization logic across MCP_RBAC_MODE.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-service-key")
os.environ["OAUTH_ALLOWED_DOMAINS"] = "jaipuria.ac.in"
os.environ["OAUTH_DEFAULT_CAMPUSES"] = "none"

import config  # noqa: E402
import faculty  # noqa: E402
from security import principal_from_claims  # noqa: E402
from supabase_client import MoodleService  # noqa: E402

PASS, FAIL = 0, 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}")


def set_mode(mode):
    config.settings.rbac_mode_raw = mode


def reset(rows=None, students=()):
    faculty._grants = faculty.TTLCache(maxsize=64, ttl=60)
    faculty._stale_grants = faculty.TTLCache(maxsize=64, ttl=3600)
    faculty._students = faculty.TTLCache(maxsize=64, ttl=60)
    calls = {"faculty": 0, "student": 0}

    def fetch_row(email):
        calls["faculty"] += 1
        return (rows or {}).get(email)

    def fetch_student(email):
        calls["student"] += 1
        return email in students

    faculty._fetch_faculty_row = fetch_row
    faculty._fetch_student_hit = fetch_student
    return calls


def claims(email, name="Prof X"):
    return {"email": email, "email_verified": True, "name": name}


print("MODE off — historical all-access for Jaipuria, registry NOT consulted")
set_mode("off")
calls = reset(rows={"prof@jaipuria.ac.in": {"name": "Prof", "campuses": ["noida"], "active": True}})
p = principal_from_claims(claims("prof@jaipuria.ac.in"))
check("off: Jaipuria faculty gets all campuses", p is not None and p["campuses"] is None)
calls = reset(students={"kid@jaipuria.ac.in"})
check("off: STUDENT denied the all-access grant (no cross-student data)",
      principal_from_claims(claims("kid@jaipuria.ac.in")) is None)

print("MODE shadow — served access unchanged, but registry IS evaluated (for logging)")
set_mode("shadow")
calls = reset(rows={"prof@jaipuria.ac.in": {"name": "Prof", "campuses": ["noida"], "active": True}})
p = principal_from_claims(claims("prof@jaipuria.ac.in"))
check("shadow: faculty served all-access", p is not None and p["campuses"] is None)
check("shadow: registry consulted (would-be grant computed)", calls["faculty"] >= 1)
reset(students={"kid@jaipuria.ac.in"})
check("shadow: STUDENT denied the all-access grant (no cross-student data)",
      principal_from_claims(claims("kid@jaipuria.ac.in")) is None)

print("MODE enforce — mcp_faculty is authoritative")
set_mode("enforce")
config.settings.self_service_access = False  # this section tests pure registry resolution (hard deny)

reset(rows={"noida.prof@jaipuria.ac.in":
            {"name": "N", "campuses": ["noida"], "active": True, "role": "faculty",
             "can_generate": True}})
p = principal_from_claims(claims("noida.prof@jaipuria.ac.in"))
check("enforce: campus faculty scoped to their campus",
      p is not None and p["campuses"] == ["noida"] and p["role"] == "faculty")

reset(rows={"dean@jaipuria.ac.in":
            {"name": "Dean", "campuses": "all", "active": True, "role": "cross_campus",
             "can_generate": True}})
p = principal_from_claims(claims("dean@jaipuria.ac.in"))
check("enforce: cross_campus gets all campuses",
      p is not None and p["campuses"] is None and p["role"] == "cross_campus")

reset(rows={})
check("enforce: unlisted Jaipuria account DENIED",
      principal_from_claims(claims("nobody@jaipuria.ac.in")) is None)

reset(rows={}, students={"kid@jaipuria.ac.in"})
check("enforce: student account DENIED",
      principal_from_claims(claims("kid@jaipuria.ac.in")) is None)

reset(rows={"stale@jaipuria.ac.in":
            {"name": "S", "campuses": ["jaipur"], "active": False, "role": "faculty"}})
check("enforce: inactive row DENIED",
      principal_from_claims(claims("stale@jaipuria.ac.in")) is None)

reset(rows={"gone@jaipuria.ac.in":
            {"name": "G", "campuses": ["indore"], "active": True, "role": "faculty",
             "expires_at": "2020-01-01T00:00:00Z"}})
check("enforce: expired grant DENIED",
      principal_from_claims(claims("gone@jaipuria.ac.in")) is None)

print("Capability gate — can_generate")
reset(rows={"viewer@jaipuria.ac.in":
            {"name": "V", "campuses": ["lucknow"], "active": True, "role": "viewer",
             "can_generate": False}})
p = principal_from_claims(claims("viewer@jaipuria.ac.in"))
svc = MoodleService(None, p)
check("enforce: viewer principal resolves scoped", p is not None and p["campuses"] == ["lucknow"])
check("enforce: viewer cannot generate reports", svc.can_generate is False)

reset(rows={"noida.prof@jaipuria.ac.in":
            {"name": "N", "campuses": ["noida"], "active": True, "role": "faculty"}})
svc2 = MoodleService(None, principal_from_claims(claims("noida.prof@jaipuria.ac.in")))
check("enforce: faculty CAN generate by default", svc2.can_generate is True)
check("enforce: faculty scope blocks other campus",
      svc2.campus_scope("noida") == ["noida"] and svc2.campus_scope("jaipur") == [])

print("Self-service access (enforce) — pending session + request_access")
import asyncio  # noqa: E402
from tools import access  # noqa: E402
from tools.access import AccessRequestParams  # noqa: E402

set_mode("enforce")
config.settings.self_service_access = True
reset(rows={})  # nobody provisioned
p = principal_from_claims(claims("newprof@jaipuria.ac.in"))
check("enforce+self-service: unlisted -> pending (not denied)",
      p is not None and p.get("role") == "pending")
psvc = MoodleService(None, p)
check("pending: no campus access (every data tool denies)",
      psvc.campus_scope("noida") == [] and psvc.allowed_campuses == [])
check("pending: cannot generate reports", psvc.can_generate is False)

config.settings.self_service_access = False
reset(rows={})
check("enforce + self-service OFF: unlisted -> hard deny",
      principal_from_claims(claims("newprof@jaipuria.ac.in")) is None)
config.settings.self_service_access = True

# request_access tool: capture the registered coroutines with a fake MCP
class _FakeMCP:
    def __init__(self):
        self.tools = {}

    def tool(self, **_kw):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn
        return deco

_submits = []
faculty.submit_access_request = lambda email, name, role, campuses: (
    _submits.append((email, role, tuple(campuses))) or True)

_fake = _FakeMCP()
_pending = MoodleService(None, {"name": "New", "email": "newprof@jaipuria.ac.in",
                                "campuses": [], "role": "pending", "can_generate": False})


async def _get_pending():
    return _pending

access.register(_fake, _get_pending)
check("access tools registered", "request_access" in _fake.tools
      and "list_access_requests" in _fake.tools)

r = asyncio.run(_fake.tools["request_access"](AccessRequestParams(campus="noida", role="faculty")))
check("request_access: valid campus/role -> pending + submitted",
      r.get("ok") is True and r.get("status") == "pending"
      and _submits and _submits[-1] == ("newprof@jaipuria.ac.in", "faculty", ("noida",)))

before = len(_submits)
r = asyncio.run(_fake.tools["request_access"](AccessRequestParams(campus="delhi", role="faculty")))
check("request_access: unknown campus rejected, nothing submitted",
      r.get("ok") is False and len(_submits) == before)

r = asyncio.run(_fake.tools["request_access"](AccessRequestParams(campus="noida", role="admin")))
check("request_access: privileged role 'admin' not self-requestable",
      r.get("ok") is False and len(_submits) == before)

# list_access_requests is admin-only
faculty.list_pending_requests = lambda limit=200: [{"email": "x@jaipuria.ac.in"}]
denied = False
try:
    asyncio.run(_fake.tools["list_access_requests"]())
except PermissionError:
    denied = True
check("list_access_requests: denied for non-admin (pending) principal", denied)

_admin = MoodleService(None, {"name": "Admin", "email": "mansi.gambhir@jaipuria.ac.in",
                              "campuses": None, "role": "admin", "can_generate": True})


async def _get_admin():
    return _admin

_fake2 = _FakeMCP()
access.register(_fake2, _get_admin)
out = asyncio.run(_fake2.tools["list_access_requests"]())
check("list_access_requests: admin sees the queue", out.get("pending") == [{"email": "x@jaipuria.ac.in"}])

# leave the singleton mode back at off so import order can't leak into other suites
set_mode("off")
print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
