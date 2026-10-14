"""Onboarding landing page (plain asserts). The page must: show the right state per role,
collect answers as an admin-approved REQUEST (never self-grant), HTML-escape identity, and be
callable by pending + student sessions. Pure renderer is tested directly; no network.

Run:  ../moodle-agent/.venv/bin/python tests/test_onboarding.py   (from the moodle-mcp dir)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-service-key")

import mcp.types as t  # noqa: E402

import security  # noqa: E402
from mcp_ui import esc, ui_resource  # noqa: E402
from tools import access  # noqa: E402
from tools.onboarding import (SELF_SERVICE_DESIGNATIONS, _UI_URI,  # noqa: E402
                              render_onboarding)

PASS = FAIL = 0


def check(name, cond):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    print(f"  {'✓' if cond else '✗'} {name}")


CAMPUSES = ["noida", "lucknow", "jaipur", "indore"]

print("\n[ allowlist: pending + student may open onboarding; it is deny-by-default elsewhere ]")
check("onboarding in PENDING_TOOLS", "onboarding" in security.PENDING_TOOLS)
check("onboarding in STUDENT_SELF_TOOLS", "onboarding" in security.STUDENT_SELF_TOOLS)
check("pending principal allowed to call onboarding",
      security.tool_allowed_for_principal("onboarding", {"role": "pending"}))
check("student principal allowed to call onboarding",
      security.tool_allowed_for_principal("onboarding", {"role": "student"}))
check("pending still CANNOT call a data tool",
      not security.tool_allowed_for_principal("get_student", {"role": "pending"}))

print("\n[ designations are a safe subset — admin/cross_campus are never self-requestable ]")
desig_values = {v for v, _ in SELF_SERVICE_DESIGNATIONS}
check("designations ⊆ access._SELF_SERVICE_ROLES", desig_values <= set(access._SELF_SERVICE_ROLES))
check("admin not offered", "admin" not in desig_values)
check("cross_campus not offered", "cross_campus" not in desig_values)

print("\n[ pending user: sees the request form + the questions ]")
v = render_onboarding({"name": "Asha Rao", "role": "pending", "campuses": []}, CAMPUSES)
check("state == pending", v["status"]["state"] == "pending")
check("html has a form", "<form" in v["html"])
check("every campus is an option", all(f'value="{c}"' in v["html"] for c in CAMPUSES))
check("designation options present", all(f'value="{d}"' in v["html"] for d in desig_values))
check("submit routes to request_access (MCP-UI action)", "request_access" in v["html"])
check("fallback text names the campus question", "Which campus" in v["text"])
check("fallback text names the designation question", "designation" in v["text"].lower())
check("greets by name", "Asha Rao" in v["text"])

print("\n[ staff user: no form, shows current access ]")
v2 = render_onboarding({"name": "Dr Bose", "role": "faculty", "campuses": ["noida"]}, CAMPUSES)
check("state == staff", v2["status"]["state"] == "staff")
check("no request form for staff", "<form" not in v2["html"])
check("text states the role", "faculty" in v2["text"])
check("text states the campus scope", "noida" in v2["text"])

print("\n[ cross_campus (all) + student states ]")
vc = render_onboarding({"name": "Admin", "role": "cross_campus", "campuses": None}, CAMPUSES)
check("cross_campus -> staff/all campuses", vc["status"]["state"] == "staff"
      and vc["status"]["scope"] == "all campuses")
vs = render_onboarding({"name": "Rahul", "role": "student", "campuses": ["noida"],
                        "student_id": "JN25MM002"}, CAMPUSES)
check("student -> student state, own record only", vs["status"]["state"] == "student")
check("student page has no request form", "<form" not in vs["html"])

print("\n[ identity is HTML-escaped (no stored XSS) ]")
vx = render_onboarding({"name": "<script>alert(1)</script>", "role": "pending",
                        "campuses": []}, CAMPUSES)
check("raw <script> NOT in html", "<script>alert(1)</script>" not in vx["html"])
check("name is escaped", "&lt;script&gt;" in vx["html"])
check("esc() escapes quotes too", esc('a"b<c') == "a&quot;b&lt;c")

print("\n[ MCP-UI resource block is well-formed ]")
r = ui_resource(_UI_URI, "<h1>hi</h1>")
check("is an EmbeddedResource", isinstance(r, t.EmbeddedResource))
check("uri is ui://…", str(r.resource.uri).startswith("ui://"))
check("mimeType text/html", r.resource.mimeType == "text/html")
try:
    ui_resource("https://evil.example/x", "<h1>no</h1>")
    check("rejects non-ui:// uri", False)
except ValueError:
    check("rejects non-ui:// uri", True)

print("\n[ empty/missing name degrades gracefully ]")
vn = render_onboarding({"role": "pending", "campuses": []}, CAMPUSES)
check("missing name -> 'there'", "there" in vn["text"])

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
