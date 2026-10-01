"""Faculty-registry + create_report-input verification (plain asserts, no pytest).

Run:  ../moodle-agent/.venv/bin/python tests/test_faculty_registry.py
from the moodle-mcp directory. Everything runs offline: the registry's DB fetches
are monkeypatched, so this exercises pure authorization logic.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-service-key")
os.environ["OAUTH_DEFAULT_CAMPUSES"] = "none"
os.environ["MCP_FACULTY"] = '{"admin@example.com": {"name": "Admin", "campuses": null}}'

import faculty  # noqa: E402
from security import principal_from_claims  # noqa: E402

PASS, FAIL = 0, 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}")


def reset(rows=None, students=(), db_error=False, student_error=False):
    """Point the registry at an in-memory 'DB' and clear its caches."""
    faculty._grants = faculty.TTLCache(maxsize=64, ttl=60)
    faculty._stale_grants = faculty.TTLCache(maxsize=64, ttl=3600)
    faculty._students = faculty.TTLCache(maxsize=64, ttl=60)
    calls = {"faculty": 0, "student": 0}

    def fetch_row(email):
        calls["faculty"] += 1
        if db_error:
            raise RuntimeError("db down")
        return (rows or {}).get(email)

    def fetch_student(email):
        calls["student"] += 1
        if student_error:
            raise RuntimeError("db down")
        return email in students

    faculty._fetch_faculty_row = fetch_row
    faculty._fetch_student_hit = fetch_student
    return calls


def claims(email, name="Prof X"):
    return {"email": email, "email_verified": True, "name": name}


print("PHASE 1 — campuses normalization (malformed rows must DENY, never widen)")
check('"all" -> None (all campuses)', faculty.normalize_campuses("all") is None)
check('"ALL " (case/space) -> None', faculty.normalize_campuses("ALL ") is None)
check("list -> lowercased list", faculty.normalize_campuses(["Noida", " Jaipur"]) == ["noida", "jaipur"])
check("empty list -> invalid", faculty.normalize_campuses([]) == "invalid")
check("list with non-string -> invalid", faculty.normalize_campuses(["noida", 3]) == "invalid")
check("dict -> invalid", faculty.normalize_campuses({"x": 1}) == "invalid")
check('random string -> invalid', faculty.normalize_campuses("everything") == "invalid")
check("None -> invalid", faculty.normalize_campuses(None) == "invalid")

print("PHASE 2 — DB registry grants")
reset(rows={"prof@example.com": {"name": "Prof", "campuses": ["noida"], "active": True}})
p = principal_from_claims(claims("prof@example.com"))
check("active external row grants scoped principal",
      p == {"name": "Prof", "email": "prof@example.com", "campuses": ["noida"],
            "role": "faculty", "can_generate": True})
reset(rows={"prof@example.com": {"name": "Prof", "campuses": "all", "active": True}})
p = principal_from_claims(claims("prof@example.com"))
check('campuses "all" -> None (all)', p is not None and p["campuses"] is None)
reset(rows={"prof@example.com": {"name": None, "campuses": "all", "active": True}})
p = principal_from_claims(claims("prof@example.com", name="Google Name"))
check("row without name falls back to Google claim name", p is not None and p["name"] == "Google Name")
reset(rows={"prof@example.com": {"name": "Prof", "campuses": ["noida"], "active": False}})
check("inactive external row -> denied", principal_from_claims(claims("prof@example.com")) is None)
reset(rows={"prof@example.com": {"name": "Prof", "campuses": "everything", "active": True}})
check("malformed external campuses -> denied", principal_from_claims(claims("prof@example.com")) is None)
reset(rows={})
check("unknown external email -> denied (default none)", principal_from_claims(claims("stranger@example.com")) is None)

print("PHASE 3 — verified Jaipuria IDs get all-campus access")
reset(rows={"kid@jaipuria.ac.in": {"name": "Kid", "campuses": "all", "active": True}},
      students={"kid@jaipuria.ac.in"})
check("roster email receives all-campus access",
      principal_from_claims(claims("kid@jaipuria.ac.in"))["campuses"] is None)
reset(students={"kid@jaipuria.ac.in"})
import config as _cfg  # noqa: E402
check("default none cannot block a Jaipuria ID",
      principal_from_claims(claims("kid@jaipuria.ac.in"))["campuses"] is None)
reset(rows={"prof@jaipuria.ac.in": {"name": "Prof", "campuses": ["noida"], "active": True}})
check("old per-campus row cannot narrow a Jaipuria ID",
      principal_from_claims(claims("prof@jaipuria.ac.in"))["campuses"] is None)
check("mixed-case Jaipuria ID accepted",
      principal_from_claims(claims("PROF@JAIPURIA.AC.IN"))["email"] == "prof@jaipuria.ac.in")
check("subdomain Jaipuria ID accepted",
      principal_from_claims(claims("prof@ailabs.jaipuria.ac.in"))["campuses"] is None)
check("lookalike domain denied",
      principal_from_claims(claims("prof@evil-jaipuria.ac.in")) is None)
from supabase_client import MoodleService  # noqa: E402
svc = MoodleService(None, principal_from_claims(claims("kid@jaipuria.ac.in")))
check("Jaipuria principal can query multiple campuses",
      svc.campus_scope("noida") == ["noida"]
      and svc.campus_scope("jaipur") == ["jaipur"])
calls = reset(db_error=True, student_error=True)
check("Jaipuria sign-in does not depend on registry availability",
      principal_from_claims(claims("kid@jaipuria.ac.in"))["campuses"] is None
      and calls == {"faculty": 0, "student": 0})

print("PHASE 4 — env break-glass override beats everything")
reset(rows={}, students={"admin@example.com"}, db_error=True, student_error=True)
p = principal_from_claims(claims("admin@example.com"))
check("MCP_FACULTY env admin gets in with DB down AND a poisoned roster row",
      p is not None and p["campuses"] is None)

print("PHASE 5 — fail-closed on DB errors, with last-known-good grace")
reset(rows={}, db_error=True)
check("faculty lookup error -> denied (no stale entry)",
      faculty.faculty_grant("prof@jaipuria.ac.in") is None)
reset(student_error=True)
check("student-roster check error -> treated as student (deny)",
      faculty.is_student("prof@jaipuria.ac.in") is True)
calls = reset(rows={"prof@jaipuria.ac.in": {"name": "P", "campuses": "all", "active": True}})
g1 = faculty.faculty_grant("prof@jaipuria.ac.in")
faculty._grants = faculty.TTLCache(maxsize=64, ttl=60)  # expire the fresh cache only

def _boom(email):
    raise RuntimeError("db down")

faculty._fetch_faculty_row = _boom
g2 = faculty.faculty_grant("prof@jaipuria.ac.in")
check("transient DB blip serves last-known-good grant", g1 == g2 and g2 is not None)

print("PHASE 6 — caching (bounded lookups per user)")
calls = reset(rows={"prof@example.com": {"name": "P", "campuses": "all", "active": True}})
for _ in range(50):
    principal_from_claims(claims("prof@example.com"))
check("50 tool calls -> 1 faculty SELECT", calls["faculty"] == 1)
check("50 tool calls -> 1 roster SELECT", calls["student"] == 1)
calls = reset(rows={})
for _ in range(50):
    principal_from_claims(claims("stranger@example.com"))
check("repeated denials are negative-cached (1 SELECT)", calls["faculty"] == 1)

print("PHASE 7 — identity gates unchanged")
reset(rows={"prof@example.com": {"name": "P", "campuses": "all", "active": True}})
check("unverified email denied",
      principal_from_claims({"email": "prof@jaipuria.ac.in", "email_verified": False}) is None)
check("missing verification claim denied",
      principal_from_claims({"email": "prof@jaipuria.ac.in"}) is None)
check("foreign domain denied",
      principal_from_claims(claims("prof@gmail.com")) is None)
check("empty claims denied", principal_from_claims({}) is None)

print("PHASE 8 — create_report input handling (student names resolve before agent URL)")
from tools.actions import CreateReportParams  # noqa: E402

def rejected(**kw):
    base = {"campus": "noida", "batch": "2025-27", "student_id": "JN25PG067"}
    base.update(kw)
    try:
        CreateReportParams(**base)
        return False
    except Exception:
        return True

check("valid params accepted", not rejected())
check("underscore/hyphen ids accepted", not rejected(student_id="JN25_PG-067"))
# params are OPTIONAL now: empty/None means "omitted" (pick for me), never an injection
check("empty campus accepted (means: any campus)", not rejected(campus=""))
check("empty student_id accepted (means: pick a student)", not rejected(student_id=""))
check("all omitted accepted", not rejected(campus="", batch="", student_id=""))
check("student names accepted", CreateReportParams(student_id="Aashna Gupta").student_id == "Aashna Gupta")
for label, value in [
    ("path traversal", "../../runs"),
    ("slash", "a/b"),
    ("encoded traversal", "..%2F..%2Fruns"),
    ("query smuggling", "x?refresh=true"),
    ("fragment", "x#y"),
    ("bidi control", "JN25PG067‮"),
]:
    ref = CreateReportParams(student_id=value).student_id
    check(f"unsafe student reference normalized: {label}",
          ref is None or not any(ch in ref for ch in "/?#%‮"))
for label, payload in [
    ("dot segment", {"batch": ".."}),
    ("leading dash", {"campus": "-noida"}),
    ("percent", {"campus": "no%2fida"}),
]:
    check(f"unsafe URL path segment rejected: {label}", rejected(**payload))

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
