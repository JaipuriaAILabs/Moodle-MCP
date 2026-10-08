"""find_student name-sanitisation (plain asserts). Regression for names dropped by the
old sanitiser: "D'Souza" / "J. Smith" must resolve (apostrophes/dots become token
breaks, not deletions), and the token-wildcard pattern must stay injection-safe.

Run:  ../moodle-agent/.venv/bin/python tests/test_find_student.py
from the moodle-mcp directory.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-service-key")

import tools.common as common  # noqa: E402  (real find_student — not monkeypatched here)
from fastmcp.exceptions import ToolError  # noqa: E402

PASS, FAIL = 0, 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}")


class _Q:
    def __init__(self, rec):
        self.rec = rec

    def select(self, *a, **k):
        return self

    def eq(self, *a, **k):
        return self

    def or_(self, expr):
        self.rec["or"] = expr
        return self

    def limit(self, n):
        return self

    def execute(self):
        class _R:
            data = []          # force the fuzzy path, then "no match" → None
        return _R()


class _Client:
    def __init__(self, rec):
        self.rec = rec

    def table(self, name):
        return _Q(self.rec)


class Svc:
    def __init__(self, rec):
        self.client = _Client(rec)

    def apply_campus(self, q, requested=None):   # campus scoping is a passthrough here
        return q

    self_student_id = None                       # not a student; boundary is a passthrough

    def apply_student(self, q, col="student_id"):
        return q


def _pattern_for(query):
    rec = {}
    res = common.find_student(Svc(rec), query)
    return rec.get("or", ""), res


print("\n[ name sanitisation → token-wildcard pattern ]")
pat, res = _pattern_for("D'Souza")
check("apostrophe kept as a token break: '*D*Souza*'", "*D*Souza*" in pat)
check("no roster match → None (no crash)", res is None)

pat, _ = _pattern_for("J. Smith")
check("dot kept as a token break: '*J*Smith*'", "*J*Smith*" in pat)

pat, _ = _pattern_for("Aashna Gupta")
check("plain name → '*Aashna*Gupta*'", "*Aashna*Gupta*" in pat)

pat, _ = _pattern_for("O'Brien-Kumar")
check("apostrophe + hyphen handled", "*O*Brien-Kumar*" in pat or "*O*Brien*Kumar*" in pat)

print("\n[ injection-safety: pattern carries only alnum / hyphen / space-wildcard ]")
pat, _ = _pattern_for("x,y).ilike.(evil")   # attempt to break out of the or-filter
bad = [c for c in pat.replace("student_name.ilike.", "").replace("student_id.ilike.", "")
       .replace(",", "") if not (c.isalnum() or c in "*- ")]
check("no PostgREST-breaking chars survive sanitisation", bad == [])


class _AmbigQ(_Q):
    def execute(self):
        class _R:
            data = []
        if self.rec.get("or"):
            _R.data = [
                {"student_id": "JN25MM002", "student_name": "Rahul Sharma",
                 "campus": "noida", "batch": "2025-27"},
                {"student_id": "JL25MM019", "student_name": "Rahul Sharma",
                 "campus": "lucknow", "batch": "2025-27"},
            ]
        return _R()


class _AmbigClient(_Client):
    def table(self, name):
        return _AmbigQ(self.rec)


print("\n[ ambiguous lookup errors never disclose roster PII ]")
ambig = Svc({})
ambig.client = _AmbigClient({})
try:
    common.find_student(ambig, "Rahul")
    err = ""
except ToolError as exc:
    err = str(exc)
check("ambiguous lookup raises a safe error", "Multiple students match" in err)
check("candidate names/ids omitted from error",
      "Rahul Sharma" not in err and "JN25MM002" not in err and "JL25MM019" not in err)

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
