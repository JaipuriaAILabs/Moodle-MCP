"""LLM-blind PII tokeniser + resolve_identities (offline, plain asserts)."""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-service-key")

import config  # noqa: E402
import pii  # noqa: E402
from tools import common, identity  # noqa: E402

PASS, FAIL = 0, 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}")


config.settings.pii_hmac_key = "unit-test-pii-key-0123456789abcdef"

print("[ student_ref: deterministic, opaque, one-way ]")
r1 = pii.student_ref("JN25PG067")
r2 = pii.student_ref("jn25pg067")           # case-insensitive -> same
r3 = pii.student_ref("JL24SM001")
check("ref is opaque 'S_' token", isinstance(r1, str) and r1.startswith("S_") and len(r1) <= 16)
check("same id -> same ref (case-insensitive)", r1 == r2)
check("different id -> different ref", r1 != r3)
check("ref does not contain the raw id", "JN25PG067".lower() not in r1.lower())

print("\n[ tokenise: identity fields -> ref; non-identity untouched ]")
payload = {
    "campus": "noida",
    "subject": "Business Research Methods",            # a 'name'-less record -> untouched
    "students": [
        {"student_id": "JN25PG067", "student_name": "Aashna Gupta", "mark_pct": 78},
        {"student_id": "JL24SM001", "student_name": "Rohan Verma", "student_email": "x@y", "mark_pct": 55},
    ],
    "course": {"name": "Wealth Management", "sections": 3},   # 'name' without student_id -> untouched
}
tok, idmap = pii.tokenise(payload)
s0 = tok["students"][0]
check("student_id replaced by ref", s0["student_id"] == r1)
check("student_name replaced by ref", s0["student_name"] == r1)
check("non-identity field preserved", s0["mark_pct"] == 78)
check("subject 'name' (no student_id) NOT tokenised", tok["course"]["name"] == "Wealth Management")
check("map reverses ref -> real identity",
      idmap[r1]["student_id"] == "JN25PG067" and idmap[r1]["student_name"] == "Aashna Gupta")
check("original payload not mutated", payload["students"][0]["student_name"] == "Aashna Gupta")

print("\n[ tokenise_response: flag-gated ]")
config.settings.pii_tokenize = False
off = pii.tokenise_response({"students": [{"student_id": "JN25PG067", "student_name": "Aashna Gupta"}]})
check("flag OFF -> unchanged, no _identity", off["students"][0]["student_name"] == "Aashna Gupta"
      and "_identity" not in off)
config.settings.pii_tokenize = True
on = pii.tokenise_response({"students": [{"student_id": "JN25PG067", "student_name": "Aashna Gupta"}]})
check("flag ON -> tokenised + _identity side-map",
      on["students"][0]["student_name"] == r1 and on["_identity"][r1]["student_name"] == "Aashna Gupta")

print("\n[ resolve_identities tool (RBAC-scoped via find_student) ]")
roster = {"Aashna Gupta": {"student_id": "JN25PG067", "campus": "noida", "student_name": "Aashna Gupta"},
          "JL24SM001":    {"student_id": "JL24SM001", "campus": "lucknow", "student_name": "Rohan Verma"}}


def _fake_find(svc, query):
    from fastmcp.exceptions import ToolError
    if query == "Ambiguous Name":
        raise ToolError("Multiple students match")
    return roster.get(query)


common.find_student = _fake_find


class _FakeMCP:
    def __init__(self):
        self.tools = {}

    def tool(self, **_kw):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn
        return deco


async def _svc():
    return object()   # find_student is stubbed, so svc is unused


fake = _FakeMCP()
identity.register(fake, _svc)
from tools.identity import ResolveParams  # noqa: E402
res = asyncio.run(fake.tools["resolve_identities"](
    ResolveParams(students=["Aashna Gupta", "JL24SM001", "Nobody Here", "Ambiguous Name"])))["resolved"]
by_q = {r["query"]: r for r in res}
check("known name -> ref + canonical id",
      by_q["Aashna Gupta"]["resolved"] and by_q["Aashna Gupta"]["student_ref"] == r1
      and by_q["Aashna Gupta"]["student_id"] == "JN25PG067")
check("known id -> ref", by_q["JL24SM001"]["student_ref"] == r3)
check("unknown -> resolved=false", by_q["Nobody Here"]["resolved"] is False)
check("ambiguous -> resolved=false (reason)", by_q["Ambiguous Name"]["resolved"] is False
      and by_q["Ambiguous Name"].get("reason") == "ambiguous")

config.settings.pii_tokenize = False
config.settings.pii_hmac_key = ""
print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
