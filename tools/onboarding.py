"""Onboarding landing page (RBAC front door).

A single tool, ``onboarding``, that renders a welcome/landing page: who you are (from the
verified OAuth identity — never self-asserted), your CURRENT access, and, when you have
none yet, the onboarding questions (which campus + your designation). It is the friendly
face over the existing self-service flow — it does NOT grant anything. Answers become a
pending ACCESS REQUEST (via the existing ``request_access`` tool / ``mcp_access_requests``
queue) that an administrator approves out-of-band; only then does a ``mcp_faculty`` row
exist and the real role + campus scope take effect. Self-declaring "I'm the Noida dean"
can never, by itself, grant access — that is the whole point of the RBAC.

Rendering: returns an MCP-UI resource (``ui://moodle/onboarding``) that MCP-UI-capable
clients show as an interactive page whose submit posts a ``request_access`` tool call,
AND a plain-text fallback so a text-only client still works (the model reads the questions
and calls ``request_access`` itself). Identity is HTML-escaped before it touches the markup.
"""
import mcp.types as _t

from annotations import READONLY_ANNOTATIONS
from config import settings
from mcp_ui import esc, ui_resource

# Designations a user may self-request, mapped to the RBAC role. MUST stay a subset of
# tools.access._SELF_SERVICE_ROLES — admin / cross_campus are assigned by an administrator,
# never self-requested. Label is for humans; value is the role sent to request_access.
SELF_SERVICE_DESIGNATIONS = (
    ("faculty", "Faculty / Teacher"),
    ("campus_admin", "Campus Head / Dean / Director"),
    ("viewer", "Viewer (read-only)"),
)

_STAFF_ROLES = {"admin", "cross_campus", "campus_admin", "faculty", "viewer"}

_UI_URI = "ui://moodle/onboarding"


def _access_state(principal: dict) -> dict:
    """Bounded, non-PII view of the caller's current access, for the page + fallback."""
    role = str(principal.get("role") or "").strip().lower()
    campuses = principal.get("campuses")
    if role == "student":
        return {"state": "student", "role": "student", "scope": "your own record only"}
    if role in {"admin", "cross_campus"} or campuses is None:
        return {"state": "staff", "role": role or "cross_campus", "scope": "all campuses"}
    if role in _STAFF_ROLES and campuses:
        scope = ", ".join(sorted({str(c).strip().lower() for c in campuses if str(c).strip()}))
        return {"state": "staff", "role": role, "scope": scope or "(none)"}
    # pending / unprovisioned / unknown -> no access yet
    return {"state": "pending", "role": role or "pending", "scope": "none yet"}


def render_onboarding(principal: dict, campuses, designations=SELF_SERVICE_DESIGNATIONS) -> dict:
    """Pure renderer → {html, text, status}. No I/O, so it is directly unit-testable.

    `principal` is the verified session principal; `campuses` the valid campus codes;
    `designations` the (value,label) self-service roles. All dynamic values are escaped.
    """
    name = str(principal.get("name") or "").strip() or "there"
    status = _access_state(principal)
    valid_campuses = [str(c).strip().lower() for c in (campuses or []) if str(c).strip()]

    # ---- plain-text fallback (always correct, even where MCP-UI is not rendered) ----
    lines = [f"👋 Welcome, {name} — Jaipuria Moodle Analytics."]
    if status["state"] == "staff":
        lines += [f"Your access: **{status['role']}** · campuses: **{status['scope']}**.",
                  "You're all set — ask me for a student report, a cohort overview, "
                  "at-risk students, attendance, and more."]
    elif status["state"] == "student":
        lines += ["Your access: **student** — your own record only.",
                  "Ask me for your marks, attendance, trajectory, or your report."]
    else:  # pending
        lines += [
            "You don't have campus access yet. To get set up, tell me:",
            "  1. **Which campus** — " + (", ".join(valid_campuses) or "(none configured)"),
            "  2. **Your designation** — " + ", ".join(v for v, _ in designations),
            "…and I'll file your access request for an administrator to approve. "
            "(Approval is required — access is never granted from what you type here.)",
        ]
    text = "\n".join(lines)

    # ---- MCP-UI HTML (rendered in a sandboxed iframe by capable clients) ----
    safe_name = esc(name)
    if status["state"] == "pending":
        campus_opts = "".join(f'<option value="{esc(c)}">{esc(c)}</option>' for c in valid_campuses)
        desig_opts = "".join(f'<option value="{esc(v)}">{esc(l)}</option>' for v, l in designations)
        body = f"""
        <p class="sub">You're signed in, but you don't have campus access yet. Tell us a
        little about you and we'll send a request to an administrator to approve.</p>
        <form id="f">
          <label>Which campus are you from?
            <select id="campus" required>{campus_opts}</select></label>
          <label>Your designation
            <select id="role" required>{desig_opts}</select></label>
          <button type="submit">Request access</button>
          <p class="note">An administrator reviews every request. Access is never granted
          automatically from this form.</p>
        </form>
        <script>
          document.getElementById('f').addEventListener('submit', function (e) {{
            e.preventDefault();
            var campus = document.getElementById('campus').value;
            var role = document.getElementById('role').value;
            // MCP-UI client action: ask the host to call the request_access tool.
            window.parent.postMessage({{ type: 'tool', payload: {{
              toolName: 'request_access', params: {{ campus: campus, role: role }} }} }}, '*');
            document.getElementById('f').innerHTML =
              '<p class="ok">Thanks — your request for <b>' + campus + '</b> (' + role +
              ') is being filed for administrator approval.</p>';
          }});
        </script>"""
    else:
        body = (f'<p class="sub">Your access: <b>{esc(status["role"])}</b> · '
                f'<b>{esc(status["scope"])}</b>.</p>'
                '<p class="note">You\'re all set. Ask for a student report, a cohort '
                'overview, at-risk students, attendance, and more.</p>')

    html = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Jaipuria Moodle — Welcome</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{ font: 15px/1.5 -apple-system,Segoe UI,Roboto,sans-serif; margin:0;
          background:#0f1117; color:#e6e8ee; }}
  .card {{ max-width:480px; margin:24px auto; padding:24px; background:#161a23;
           border:1px solid #262c3a; border-radius:14px; }}
  h1 {{ font-size:20px; margin:0 0 4px; }}
  .sub {{ color:#aeb4c2; margin:0 0 18px; }}
  label {{ display:block; margin:14px 0 6px; font-weight:600; }}
  select {{ width:100%; padding:10px; border-radius:8px; border:1px solid #39415a;
            background:#0f1117; color:#e6e8ee; }}
  button {{ margin-top:18px; width:100%; padding:11px; border:0; border-radius:8px;
            background:#4f6bff; color:#fff; font-weight:600; cursor:pointer; }}
  .note {{ color:#8b92a3; font-size:13px; margin-top:12px; }}
  .ok {{ color:#36d399; font-weight:600; }}
</style></head><body><div class="card">
  <h1>👋 Welcome, {safe_name}</h1>
  <p class="sub">Jaipuria Moodle Analytics</p>
  {body}
</div></body></html>"""

    return {"html": html, "text": text, "status": status}


def register(mcp, get_service):
    @mcp.tool(title="Welcome / Onboarding", annotations=READONLY_ANNOTATIONS)
    async def onboarding() -> list:
        """
        WHAT: A welcome / onboarding landing page — who you are, your CURRENT access, and
        (if you have none yet) how to get it. Renders an interactive page on MCP-UI clients
        and a text summary everywhere.
        USE WHEN they say: 'who am I / get me started / onboard me / how do I get access',
        or it's a first-time / pending user.
        DO NOT USE WHEN they want data (use the report/analytics tools) — this only orients
        and routes to request_access. It grants nothing; access is admin-approved.
        RETURNS: an MCP-UI resource (ui://moodle/onboarding) + a plain-text fallback.
        """
        svc = await get_service()
        view = render_onboarding(dict(svc.principal), settings.campuses())
        return [ui_resource(_UI_URI, view["html"]),
                _t.TextContent(type="text", text=view["text"])]
