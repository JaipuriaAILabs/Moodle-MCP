"""onepager TrueFoundry gateway routing + token-then-stitch (offline, plain asserts, mocked urlopen).

Proves (AIA-1012/AIA-1386): in TrueFoundry mode the real student name never enters the prompt, the
call carries bilateral and advanced guardrails plus bounded execution controls, production fails
closed without TrueFoundry, and direct mode remains development-only. No network or real key needed.

    ../moodle-agent/.venv/bin/python onepager/test_truefoundry_redaction.py
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

build_report.ENV_FILES = []   # isolate from any real .env.* during the test

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
    return _Resp({"x-tfy-trace-id": "t-123", "content-type": "application/json"})


urllib.request.urlopen = _fake_urlopen

print("[ TrueFoundry mode: no real name in prompt, routed via TFY gateway with guardrail ]")
os.environ["TRUEFOUNDRY_API_KEY"] = "tfy-test-key"
os.environ["TRUEFOUNDRY_BASE_URL"] = "https://gradelessai.truefoundry.cloud/api/llm/openai"
os.environ["TRUEFOUNDRY_MODEL"] = "openrouter-main/google/gemini-2.5-flash"
os.environ["TRUEFOUNDRY_INPUT_GUARDRAILS"] = "moodle-pii/pii-redaction"
os.environ["TRUEFOUNDRY_OUTPUT_GUARDRAILS"] = "moodle-pii/pii-redaction"
os.environ["TRUEFOUNDRY_PROMPT_INJECTION_GUARDRAIL"] = "moodle-security/prompt-injection"
os.environ["TRUEFOUNDRY_SECRETS_GUARDRAIL"] = "moodle-security/secrets"
n, _usage = build_report.llm_narrative(D, F, "google/gemini-2.5-flash")
body = json.loads(CAP["data"])
check("routed to TFY gateway /chat/completions",
      CAP["url"] == "https://gradelessai.truefoundry.cloud/api/llm/openai/chat/completions")
check("model = TRUEFOUNDRY_MODEL override", body["model"] == "openrouter-main/google/gemini-2.5-flash")
check("real first name 'Rahul' NOT in prompt", "Rahul" not in CAP["data"])
check("real surname 'Sharma' NOT in prompt", "Sharma" not in CAP["data"])
check("pseudonym present in prompt", "Aarav" in CAP["data"])
check("Authorization: Bearer <tfy key> sent", CAP["headers"].get("authorization") == "Bearer tfy-test-key")
gr = json.loads(CAP["headers"].get("x-tfy-guardrails", "{}"))
check("X-TFY-GUARDRAILS input hook = moodle-pii/pii-redaction",
      "moodle-pii/pii-redaction" in gr.get("llm_input_guardrails", []))
check("X-TFY-GUARDRAILS output hook = moodle-pii/pii-redaction",
      "moodle-pii/pii-redaction" in gr.get("llm_output_guardrails", []))
check("stream=false so output guardrails execute", body.get("stream") is False)
check("gateway body logging disabled",
      json.loads(CAP["headers"].get("x-tfy-logging-config", "{}"))["enabled"] is False)
check("provider web search disabled", CAP["headers"].get("x-tfy-disable-web-search") == "true")
check("gateway request timeout pinned", CAP["headers"].get("x-tfy-request-timeout") == "60000")
retry = json.loads(CAP["headers"].get("x-tfy-retry-config", "{}"))
check("gateway retries bounded", retry.get("attempts") == 2 and 429 in retry.get("onStatusCodes", []))
check("guardrail scope covers every attempt", CAP["headers"].get("x-tfy-guardrails-scope") == "all")
check("Prompt Injection guardrail attached to input",
      "moodle-security/prompt-injection" in gr.get("llm_input_guardrails", []))
check("Secrets Detection guardrail attached to output",
      "moodle-security/secrets" in gr.get("llm_output_guardrails", []))
metadata = json.loads(CAP["headers"].get("x-tfy-metadata", "{}"))
check("TrueFoundry metadata carries no student identity",
      not any(x in json.dumps(metadata) for x in ("Rahul", "Sharma", "JN25MM002")))

print("\n[ stitch_name: pseudonym -> real name after generation ]")
n = build_report.stitch_name(n, "Rahul")
check("headline stitched to real name", n["headline"].startswith("Hi Rahul."))
check("no pseudonym left anywhere", "Aarav" not in json.dumps(n))

print("\n[ direct development mode: pseudonym remains mandatory ]")
for k in ("TRUEFOUNDRY_API_KEY", "TRUEFOUNDRY_BASE_URL", "TRUEFOUNDRY_MODEL"):
    os.environ.pop(k, None)
build_report.llm_narrative(D, F, "google/gemini-2.5-flash")
check("routed to OpenRouter", CAP["url"] == "https://openrouter.ai/api/v1/chat/completions")
check("real first name absent in direct-mode prompt", "Rahul" not in CAP["data"])
check("pseudonym present in direct-mode prompt", "Aarav" in CAP["data"])
check("no X-TFY-GUARDRAILS header in direct mode", "x-tfy-guardrails" not in CAP["headers"])

print("\n[ production: no direct-provider privacy downgrade ]")
os.environ["TRUEFOUNDRY_REQUIRED"] = "1"
try:
    build_report.llm_narrative(D, F, "google/gemini-2.5-flash")
except RuntimeError as exc:
    check("missing TrueFoundry fails closed", "required" in str(exc).lower())
else:
    check("missing TrueFoundry fails closed", False)
del os.environ["TRUEFOUNDRY_REQUIRED"]

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
