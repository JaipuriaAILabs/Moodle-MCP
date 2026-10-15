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

Visual identity follows the official Rehearsal brand kit: the 4-stop rainbow stripe at the
card's top edge, Indigo (#4e44fd) primary, the "Re" mark, and Raleway with a system-font
fallback (font CDNs can be blocked in a sandboxed iframe, so the layout degrades cleanly).
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

# --- static page chrome (plain strings: CSS/JS braces are literal, not f-string) ---------
_PAGE_HEAD = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Jaipuria Moodle — Welcome</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=Raleway:wght@200;400;600&display=swap');
:root{
  --re-black:#0a0a0a; --re-white:#ffffff; --re-lavender:#9677f8; --re-indigo:#4e44fd;
  --re-coral:#ff4859; --re-green:#00c483; --re-orange:#ff5e00; --re-gray-800:#262626;
  color-scheme:dark;
  --page:var(--re-black); --ink:var(--re-white); --card:#121319; --muted:#b0b0b0;
  --line:var(--re-gray-800); --input-line:#4a4a52;
  --button-bg:var(--re-indigo); --button-hover:#3c32d4; --button-ink:#ffffff;
  --success:var(--re-green); --warning:var(--re-orange);
  --stripe:linear-gradient(90deg,var(--re-lavender),var(--re-indigo),var(--re-coral),var(--re-green));
  --font:'Raleway',-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif; }
*{box-sizing:border-box}
body{font:14px/1.6 var(--font);margin:0;background:var(--page);color:var(--ink);
  -webkit-font-smoothing:antialiased;letter-spacing:.005em;}
.wrap{max-width:560px;margin:24px auto;padding:0 16px;}
.card{background:var(--card);border:1px solid var(--line);border-radius:16px;overflow:hidden;}
.stripe{height:4px;background:var(--stripe);}
.pad{padding:22px 24px;}
.head{display:flex;align-items:center;gap:13px;}
.re{width:42px;height:42px;border-radius:12px;background:var(--re-black);color:#fff;display:flex;
  align-items:center;justify-content:center;font-weight:400;font-size:19px;letter-spacing:-1px;
  position:relative;overflow:hidden;flex:0 0 auto;border:1px solid var(--re-gray-800);}
.re::after{content:"";position:absolute;left:0;right:0;bottom:0;height:3px;background:var(--stripe);}
.org{color:var(--muted);font-size:12px;}
h1{font-size:19px;margin:2px 0 0;font-weight:600;}
.sec{border-top:1px solid var(--line);padding:18px 24px;}
.sec h2{font-size:13px;margin:0 0 10px;font-weight:600;}
.num{color:var(--muted);font-weight:400;margin-right:8px;font-size:12px;letter-spacing:.04em;}
.idrow{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap;}
.who{font-weight:600;}
.verified{color:var(--success);font-size:12px;font-weight:600;white-space:nowrap;}
label{display:block;margin:12px 0 6px;font-weight:600;font-size:13px;}
select{width:100%;padding:11px;border-radius:10px;border:1px solid var(--input-line);
  background:var(--card);color:var(--ink);font:inherit;}
.ack{display:flex;align-items:flex-start;gap:9px;margin-top:16px;font-weight:400;
  font-size:13px;color:var(--muted);}
.ack input{margin-top:3px;flex:0 0 auto;accent-color:var(--re-indigo);}
button{margin-top:16px;padding:12px 18px;border:0;border-radius:10px;background:var(--button-bg);
  color:var(--button-ink);font-weight:600;font:inherit;cursor:pointer;}
button:hover:not(:disabled){background:var(--button-hover);}
button:disabled{opacity:.5;cursor:not-allowed;}
.pill{display:inline-block;padding:4px 11px;border-radius:999px;font-size:12px;font-weight:600;}
.pending{background:rgba(255,94,0,.15);color:var(--warning);}
.ok{background:rgba(0,196,131,.16);color:var(--success);}
.kv{display:flex;justify-content:space-between;gap:12px;padding:7px 0;font-size:13px;
  border-top:1px dashed var(--line);}
.kv:first-of-type{border-top:0;}
.k{color:var(--muted);}
.note{color:var(--muted);font-size:12px;margin-top:10px;}
.foot{display:flex;justify-content:space-between;color:var(--muted);font-size:11px;
  padding:14px 24px;border-top:1px solid var(--line);gap:12px;flex-wrap:wrap;}
</style></head><body>
"""

_PAGE_TAIL = """
<script>
(function(){
  var f=document.getElementById('f'); if(!f){return;}
  var ack=document.getElementById('ack'), go=document.getElementById('go');
  if(ack&&go){ ack.addEventListener('change',function(){ go.disabled=!ack.checked; }); }
  f.addEventListener('submit',function(e){
    e.preventDefault();
    var c=document.getElementById('campus').value, r=document.getElementById('role').value;
    try{ window.parent.postMessage({type:'tool',payload:{toolName:'request_access',
         params:{campus:c,role:r}}},'*'); }catch(_e){}
    f.innerHTML='<p class="pill ok">Request for '+c+' ('+r+') filed for administrator approval.</p>';
  });
})();
</script>
</body></html>"""


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

    # ---- MCP-UI HTML (sandboxed iframe on capable clients; brand-kit styled) ----
    safe_name = esc(name)
    if status["state"] == "pending":
        campus_opts = "".join(f'<option value="{esc(c)}">{esc(c.title())}</option>'
                              for c in valid_campuses)
        desig_opts = "".join(f'<option value="{esc(v)}">{esc(l)}</option>'
                             for v, l in designations)
        sections = f"""  <div class="sec">
    <h2><span class="num">02</span>Your affiliation</h2>
    <p class="note">You're signed in but don't have campus access yet. Tell us a little about
    you and we'll file a request for an administrator to approve.</p>
    <form id="f">
      <label for="campus">Which campus are you from?</label>
      <select id="campus" required>{campus_opts}</select>
      <label for="role">Your designation</label>
      <select id="role" required>{desig_opts}</select>
      <label class="ack"><input type="checkbox" id="ack">
        I understand this is an access <b>request</b> and needs authorised review — selecting a
        role here never grants it.</label>
      <button id="go" type="submit" disabled>Request access&nbsp;→</button>
    </form>
  </div>
  <div class="sec">
    <h2><span class="num">03</span>Your access</h2>
    <p><span class="pill pending">Academic access pending</span></p>
    <div class="kv"><span class="k">Campus scope</span><span>Not granted</span></div>
    <div class="kv"><span class="k">Approved role</span><span>Not granted</span></div>
    <div class="kv"><span class="k">Latest request</span><span>Awaiting review</span></div>
    <p class="note">An administrator reviews every request. Organisation-wide scope needs
    explicit data-owner approval — it is never inferred from a selected designation.</p>
  </div>"""
    else:
        hint = ("Ask me for your marks, attendance, trajectory, or your report."
                if status["state"] == "student"
                else "You're all set — ask for a student report, a cohort overview, "
                     "at-risk students, attendance, and more.")
        sections = f"""  <div class="sec">
    <h2><span class="num">02</span>Your access</h2>
    <p><span class="pill ok">Active</span></p>
    <div class="kv"><span class="k">Role</span><span>{esc(status['role'])}</span></div>
    <div class="kv"><span class="k">Campus scope</span><span>{esc(status['scope'])}</span></div>
    <p class="note">{hint}</p>
  </div>"""

    body = f"""<div class="wrap"><div class="card">
  <div class="stripe"></div>
  <div class="pad head">
    <div class="re">Re</div>
    <div><div class="org">Jaipuria Institute of Management</div><h1>Moodle MCP</h1></div>
  </div>
  <div class="sec">
    <h2><span class="num">01</span>Your identity</h2>
    <div class="idrow"><div class="who">👋 {safe_name}</div>
      <div class="verified">✓ Google verified</div></div>
  </div>
{sections}
  <div class="foot"><span>Institutional access review</span>
    <span>No access from self-declared roles</span></div>
</div></div>"""

    html = _PAGE_HEAD + body + _PAGE_TAIL
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
