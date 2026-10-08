"""onepager Portkey routing + token-then-stitch (offline, plain asserts, mocked urlopen).

Proves: in Portkey mode the real student name never enters the prompt (a pseudonym does), the call
is routed through Portkey with the config header + @openrouter model, and stitch_name restores the
real name afterwards; in direct mode behaviour is unchanged. No network / no real key needed.

    ../moodle-agent/.venv/bin/python onepager/test_portkey_redaction.py
"""
import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

PASS, FAIL = 0, 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}")


os.environ["OPENROUTER_API_KEY"] = "dummy-openrouter-key"   # for the direct-mode path
import build_report  # noqa: E402

build_report.ENV_FILES = []   # isolate from any real .env / .env.portkey.local during the test

D = {
    "student": {"id": "JN25MM002", "name": "Rahul Sharma", "campus": "noida", "batch": "2025-27"},
    "trimester": 2, "benchmark": "class average", "data_date": "2026-10-01",
    "tracks": ["Finance"],
    "subjects": [{
        "subject": "Corporate Finance", "track": "Finance",
        "you_pct": 60.0, "class_pct": 70.0, "att_you": 80.0, "att_class": 75.0,
        "components": [{"component": "Quiz 1", "kind": "quiz", "you_pct": 55.0, "class_pct": 72.0}],
    }],
}
F = build_report.facts(D)

NARRATIVE = {"headline": "Hi Aarav. You scored above class in 1 of 1 subjects.",
             "subtitle": "x", "pattern_title": "Aarav's finance split", "pattern_text": "...",
             "attendance_line": "1 of 1 subjects",
             "tracks": [{"track": "Finance", "title": "t", "learning": "l", "interview": "i"}]}
RESP_BYTES = json.dumps({"choices": [{"message": {"content": json.dumps(NARRATIVE)}}],
                         "usage": {"cost": 0.001}}).encode()
CAP = {}


class _Resp:
    def __init__(self, headers):
        self.headers = type("H", (), {"items": staticmethod(lambda: list(headers.items()))})()

    def read(self):
        return RESP_BYTES

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _fake_urlopen(req, timeout=None):
    CAP["url"] = req.full_url
    CAP["data"] = req.data.decode()
    CAP["headers"] = {k.lower(): v for k, v in req.headers.items()}
    return _Resp({"x-portkey-trace-id": "t-123", "content-type": "application/json"})


urllib.request.urlopen = _fake_urlopen

print("[ Portkey mode: no real name in prompt, routed via Portkey ]")
os.environ["PORTKEY_API_KEY"] = "pk-test"
os.environ["PORTKEY_CONFIG_ID"] = "pc-moodle-cf8b65"
n, _usage = build_report.llm_narrative(D, F, "google/gemini-2.5-flash")
body = json.loads(CAP["data"])
check("routed to Portkey gateway", CAP["url"] == "https://api.portkey.ai/v1/chat/completions")
check("model prefixed @openrouter/", body["model"] == "@openrouter/google/gemini-2.5-flash")
check("real first name 'Rahul' NOT in prompt", "Rahul" not in CAP["data"])
check("real full name 'Sharma' NOT in prompt", "Sharma" not in CAP["data"])
check("pseudonym present in prompt", "Aarav" in CAP["data"])
check("x-portkey-api-key header sent", "x-portkey-api-key" in CAP["headers"])
check("x-portkey-config header = pc-moodle-cf8b65", CAP["headers"].get("x-portkey-config") == "pc-moodle-cf8b65")
check("no OpenRouter Authorization header in Portkey mode", "authorization" not in CAP["headers"])

print("\n[ stitch_name: pseudonym -> real name, naturally ]")
n = build_report.stitch_name(n, "Rahul")
check("headline stitched", n["headline"].startswith("Hi Rahul."))
check("possessive stitched ('Aarav's' -> 'Rahul's')", n["pattern_title"] == "Rahul's finance split")
check("no pseudonym left anywhere", "Aarav" not in json.dumps(n))
check("no-op when pseudonym absent",
      build_report.stitch_name({"headline": "Hi Rahul."}, "Rahul")["headline"] == "Hi Rahul.")
check("word-bounded: does not corrupt other words",
      build_report.stitch_name({"headline": "Aaravind met Aarav."}, "Rahul")["headline"] == "Aaravind met Rahul.")

print("\n[ direct development mode: pseudonym remains mandatory ]")
del os.environ["PORTKEY_API_KEY"]
build_report.llm_narrative(D, F, "google/gemini-2.5-flash")
check("routed to OpenRouter", CAP["url"] == "https://openrouter.ai/api/v1/chat/completions")
check("real first name absent in direct-mode prompt", "Rahul" not in CAP["data"])
check("pseudonym present in direct-mode prompt", "Aarav" in CAP["data"])
check("Authorization header present in direct mode", "authorization" in CAP["headers"])
check("model NOT @openrouter-prefixed in direct mode",
      json.loads(CAP["data"])["model"] == "google/gemini-2.5-flash")

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
