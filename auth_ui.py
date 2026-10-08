"""Production branding for FastMCP's security-critical OAuth consent page.

FastMCP owns the consent transaction, CSRF token, cookies, and POST handler.  This
wrapper intentionally changes presentation only: it leaves every hidden field and
the approve/deny form semantics intact, then the approved request continues to the
real Google OAuth flow.
"""
from __future__ import annotations

import html as html_module
import logging
import re

log = logging.getLogger("moodle-mcp.auth-ui")

_CONSENT_PATHS = frozenset(("/consent", "/mcp/consent"))
_REQUIRED_FORM_MARKERS = (
    'id="consentForm"',
    'name="txn_id"',
    'name="csrf_token"',
    'name="action" value="approve"',
    'name="action" value="deny"',
)

_GOOGLE_MARK = """
<svg class="google-mark" viewBox="0 0 18 18" aria-hidden="true" focusable="false">
  <path fill="#EA4335" d="M17.64 9.205c0-.638-.057-1.252-.164-1.841H9v3.482h4.844a4.14 4.14 0 0 1-1.797 2.715v2.258h2.909c1.702-1.567 2.684-3.875 2.684-6.614Z"/>
  <path fill="#4285F4" d="M9 18c2.43 0 4.468-.806 5.956-2.181l-2.909-2.258c-.806.54-1.835.859-3.047.859-2.344 0-4.328-1.585-5.037-3.715H.956v2.333A9 9 0 0 0 9 18Z"/>
  <path fill="#FBBC05" d="M3.963 10.705A5.41 5.41 0 0 1 3.682 9c0-.592.102-1.168.281-1.705V4.962H.956A9 9 0 0 0 0 9c0 1.452.347 2.827.956 4.038l3.007-2.333Z"/>
  <path fill="#34A853" d="M9 3.58c1.322 0 2.508.454 3.441 1.346l2.582-2.582C13.464.891 11.426 0 9 0A9 9 0 0 0 .956 4.962l3.007 2.333C4.672 5.165 6.656 3.58 9 3.58Z"/>
</svg>
""".strip()

_BRAND_CSS = """
        :root {
            color-scheme: light;
            --jaipuria-purple: #4f3267;
            --jaipuria-purple-dark: #382149;
            --jaipuria-gold: #d47a16;
            --ink: #211b26;
            --muted: #665f6b;
            --line: #e6e0e9;
        }
        body {
            background:
                radial-gradient(circle at 12% 8%, rgba(212, 122, 22, .10), transparent 31rem),
                radial-gradient(circle at 90% 100%, rgba(79, 50, 103, .12), transparent 34rem),
                #f8f6f9;
            color: var(--ink);
        }
        .container {
            max-width: 31rem;
            padding: 2.5rem 2.5rem 2rem;
            border-color: var(--line);
            border-radius: 1.25rem;
            box-shadow: 0 24px 64px rgba(56, 33, 73, .13);
        }
        .logo {
            width: min(18rem, 82%);
            max-height: 5.2rem;
            object-fit: contain;
            margin-bottom: 1.75rem;
        }
        h1 {
            color: var(--ink);
            font-size: 1.7rem;
            letter-spacing: -.02em;
            margin-bottom: .55rem;
        }
        .auth-subtitle {
            color: var(--muted);
            font-size: .98rem;
            line-height: 1.55;
            margin: 0 auto 1.5rem;
            max-width: 25rem;
        }
        .info-box {
            background: #faf8fb;
            border-color: var(--line);
            color: #4f4853;
            text-align: center;
            margin-bottom: 1.1rem;
        }
        .info-box strong, .info-box .server-name-link {
            color: var(--jaipuria-purple);
        }
        .redirect-section { display: none; }
        details { margin: .5rem 0 1.1rem; }
        summary { color: var(--muted); text-align: center; }
        .detail-box { margin-top: .5rem; margin-bottom: 0; }
        .detail-row { align-items: flex-start; }
        .detail-label { min-width: 132px; }
        .auth-assurance {
            display: flex;
            align-items: flex-start;
            gap: .55rem;
            color: var(--muted);
            font-size: .82rem;
            line-height: 1.45;
            text-align: left;
            margin: .35rem auto 1.2rem;
        }
        .auth-assurance .shield {
            color: var(--jaipuria-purple);
            font-size: 1rem;
            line-height: 1.25;
        }
        .button-group {
            flex-direction: column;
            gap: .7rem;
            margin-top: 0;
        }
        button { width: 100%; min-height: 3rem; }
        .btn-approve, .btn-primary {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            gap: .8rem;
            background: #fff;
            color: #3c4043;
            border: 1px solid #c7c9cc;
            border-radius: .55rem;
            font-weight: 600;
            box-shadow: 0 1px 2px rgba(60, 64, 67, .12);
        }
        .btn-approve:hover, .btn-primary:hover {
            background: #f8faff;
            border-color: #aeb4bc;
            box-shadow: 0 2px 6px rgba(60, 64, 67, .16);
        }
        .google-mark { width: 1.15rem; height: 1.15rem; flex: 0 0 auto; }
        .btn-deny, .btn-secondary {
            background: transparent;
            color: var(--jaipuria-purple);
            font-weight: 600;
        }
        .btn-deny:hover, .btn-secondary:hover {
            background: #f4eff6;
            box-shadow: none;
        }
        .help-link-container { bottom: 1rem; right: 1rem; }
        .help-link { color: var(--muted); }
        @media (max-width: 640px) {
            body { align-items: flex-start; padding-top: 1rem; }
            .container { padding: 2rem 1.35rem 1.5rem; }
            .detail-row { display: block; }
            .detail-label { padding-bottom: .2rem; }
        }
"""


def brand_consent_html(source: str, *, logo_data_uri: str = "") -> str:
    """Return a Jaipuria/Google presentation of FastMCP's existing consent form.

    If FastMCP changes the required form shape in a future upgrade, fail safe by
    returning its original page instead of guessing at OAuth fields.
    """
    if not all(marker in source for marker in _REQUIRED_FORM_MARKERS):
        return source

    branded = source.replace(
        "<title>Application Access Request</title>",
        "<title>Sign in to Jaipuria Moodle</title>",
        1,
    )
    branded = branded.replace("</style>", _BRAND_CSS + "\n        </style>", 1)

    if logo_data_uri:
        safe_logo = html_module.escape(logo_data_uri, quote=True)
        branded = re.sub(
            r'<img\s+[^>]*class="logo"\s*/>',
            f'<img src="{safe_logo}" alt="Jaipuria Institute of Management" class="logo" />',
            branded,
            count=1,
        )

    branded = branded.replace(
        "<h1>Application Access Request</h1>",
        "<h1>Continue to Jaipuria Moodle</h1>"
        '<p class="auth-subtitle">Sign in with your Jaipuria Google Workspace account. '
        "Password login is not available.</p>",
        1,
    )
    branded = branded.replace(
        "The application <strong>",
        "<strong>",
        1,
    ).replace(
        "</strong> wants to access the MCP server <strong>",
        "</strong> is requesting access to <strong>",
        1,
    ).replace(
        "</strong>. Please ensure you recognize the callback address below.",
        "</strong>. Google will verify your identity before this connection is authorized.",
        1,
    )
    branded = branded.replace(
        '<form id="consentForm" method="POST" action="">',
        '<div class="auth-assurance"><span class="shield" aria-hidden="true">&#128274;</span>'
        "<span>Your Google password is handled by Google and is never shared with "
        "Jaipuria Moodle or the requesting application.</span></div>"
        '<form id="consentForm" method="POST" action="">',
        1,
    )
    branded = branded.replace(
        '<button type="submit" name="action" value="approve" class="btn-approve">'
        "Allow Access</button>",
        '<button type="submit" name="action" value="approve" class="btn-approve">'
        f"{_GOOGLE_MARK}<span>Continue with Google</span></button>",
        1,
    )
    branded = branded.replace(
        '<button type="submit" name="action" value="deny" class="btn-deny">Deny</button>',
        '<button type="submit" name="action" value="deny" class="btn-deny">Cancel</button>',
        1,
    )
    branded = branded.replace(
        "This FastMCP server requires your consent",
        "Jaipuria Moodle requires your consent",
        1,
    ).replace(
        "Learn more about\n                    FastMCP security",
        "Learn more about\n                    OAuth security",
        1,
    )
    return branded


class GoogleConsentBranding:
    """ASGI presentation wrapper for GET consent pages only.

    POST handling, redirects, CSRF validation, transaction storage, and cookies stay
    entirely inside FastMCP. Other paths and error responses pass through byte-for-byte.
    """

    def __init__(self, app, *, enabled: bool, logo_data_uri: str = ""):
        self.app = app
        self.enabled = enabled
        self.logo_data_uri = logo_data_uri

    async def __call__(self, scope, receive, send):
        if (not self.enabled or scope.get("type") != "http"
                or scope.get("method") != "GET"
                or scope.get("path") not in _CONSENT_PATHS):
            return await self.app(scope, receive, send)

        response_start = None
        body_parts: list[bytes] = []

        async def capture(message):
            nonlocal response_start
            message_type = message.get("type")
            if message_type == "http.response.start":
                response_start = message
                return
            if message_type != "http.response.body" or response_start is None:
                await send(message)
                return

            body_parts.append(message.get("body", b""))
            if message.get("more_body", False):
                return

            start = dict(response_start)
            body = b"".join(body_parts)
            headers = list(start.get("headers") or [])
            content_type = next(
                (value.lower() for key, value in headers if key.lower() == b"content-type"),
                b"",
            )
            encoded = next(
                (value for key, value in headers if key.lower() == b"content-encoding"),
                None,
            )
            if start.get("status") == 200 and b"text/html" in content_type and encoded is None:
                try:
                    source = body.decode("utf-8")
                    branded = brand_consent_html(source, logo_data_uri=self.logo_data_uri)
                    body = branded.encode("utf-8")
                except Exception:  # noqa: BLE001 - presentation must never break OAuth
                    log.warning("consent branding failed; serving FastMCP page", exc_info=True)

            headers = [(key, value) for key, value in headers
                       if key.lower() != b"content-length"]
            headers.append((b"content-length", str(len(body)).encode("ascii")))
            if not any(key.lower() == b"cache-control" for key, _ in headers):
                headers.append((b"cache-control", b"no-store"))
            start["headers"] = headers
            await send(start)
            await send({"type": "http.response.body", "body": body, "more_body": False})

        return await self.app(scope, receive, capture)
