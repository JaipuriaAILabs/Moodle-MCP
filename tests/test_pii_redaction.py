"""In-server PII redaction (zero-trust `pii.redact`) — offline, plain asserts.

Verifies the DPDP primary control wired at the GuardMiddleware chokepoint: every student identity
is pseudonymised to the deterministic `student_ref` BEFORE a result leaves the server, including
inside free-text (narratives), with NO `_identity` reverse map, and with marks/campus/numbers and
our own `S_` refs left intact. Run with the sibling agent venv:

    ../moodle-agent/.venv/bin/python tests/test_pii_redaction.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-service-key")

import config  # noqa: E402
import pii  # noqa: E402

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
REF = pii.student_ref("JN25MM002")

# A realistic cohort result + a create_report-shaped record whose narrative free-text names the
# student by full name, first name, enrolment id and email — the cases structured tokenisation
# alone would miss.
PAYLOAD = {
    "campus": "noida",
    "course": {"name": "Wealth Management", "sections": 3},   # 'name' w/o student_id -> keep
    "students": [
        {"student_id": "JN25MM002", "student_name": "Rahul Sharma",
         "first_name": "Rahul", "student_email": "rahul.sharma@jaipuria.ac.in",
         "mark_pct": 78, "attendance_pct": 61},
    ],
    "report": {
        "student_id": "JN25MM002", "name": "Rahul Sharma",
        "narrative": "Rahul Sharma (JN25MM002) is strong in Marketing (78). "
                     "Rahul should focus on Finance. Contact rahul.sharma@jaipuria.ac.in "
                     "or call 9876543210.",
        "report_url": "https://reports.tryrehearsal.ai/s/AbC123xyz",
    },
}

print("[ enforce: no raw identifier survives anywhere, incl. free-text ]")
red, stats = pii.redact(PAYLOAD)
blob = json.dumps(red)
for canary in ("Rahul Sharma", "Rahul", "JN25MM002", "rahul.sharma@jaipuria.ac.in"):
    check(f"canary gone: {canary!r}", canary not in blob)
check("phone leak-guarded", "9876543210" not in blob and "[PHONE]" in blob)
check("structured id/name -> ref", red["students"][0]["student_id"] == REF
      and red["students"][0]["student_name"] == REF)
check("free-text full name -> ref", REF in red["report"]["narrative"])
check("NO _identity side-map emitted", "_identity" not in red and "_identity" not in blob)
check("stats are counts only", set(stats) == {"ids", "freetext", "leak"}
      and stats["ids"] == 1 and stats["freetext"] > 0 and stats["leak"] >= 1)

print("\n[ non-identity data preserved exactly ]")
check("marks number byte-identical", red["students"][0]["mark_pct"] == 78
      and red["students"][0]["attendance_pct"] == 61)
check("campus kept", red["campus"] == "noida")
check("course 'name' (no student_id) NOT redacted", red["course"]["name"] == "Wealth Management")
check("report_url untouched", red["report"]["report_url"] == "https://reports.tryrehearsal.ai/s/AbC123xyz")
check("input not mutated", PAYLOAD["students"][0]["student_name"] == "Rahul Sharma")

print("\n[ determinism + opacity ]")
red2, _ = pii.redact(PAYLOAD)
check("same student -> same ref across calls", red2["students"][0]["student_id"] == REF)
check("ref is opaque S_ token, no raw id", REF.startswith("S_") and "jn25mm002" not in REF.lower())
check("S_ ref is not itself leak-guarded (no @, not 10-digit)",
      pii.redact({"x": REF})[0]["x"] == REF)

print("\n[ shadow: redact_hits=False counts but leaves leak hits in place ]")
_shadow, sstats = pii.redact({"t": "mail me at a@b.com or 9876543210"}, redact_hits=False)
check("shadow counts the leak", sstats["leak"] >= 1)
check("shadow leaves raw in place", "a@b.com" in _shadow["t"] and "9876543210" in _shadow["t"])

print("\n[ mode normaliser ]")
config.settings.pii_redaction_mode_raw = "ENFORCE"
check("'ENFORCE' -> enforce", config.settings.pii_redaction_mode() == "enforce")
config.settings.pii_redaction_mode_raw = "bogus"
check("bad value -> off", config.settings.pii_redaction_mode() == "off")
config.settings.pii_redaction_mode_raw = "off"

print("\n[ scalar + empty-result safety ]")
check("bare string passes through leak guard", pii.redact("hello world")[0] == "hello world")
check("empty dict -> empty dict, no crash", pii.redact({})[0] == {})

config.settings.pii_hmac_key = ""
print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
