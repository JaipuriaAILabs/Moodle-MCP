"""Minimal MCP-UI (mcpui.dev) resource helper — server side, zero extra deps.

An MCP-UI resource is an ``EmbeddedResource`` content block whose URI starts with
``ui://`` and whose mimeType is ``text/html``; clients that support MCP-UI render the
HTML in a sandboxed iframe, and a submit control can post a tool call back to the client
(``{type:"tool", payload:{toolName, params}}``). Clients that do NOT support MCP-UI
simply ignore the resource — which is why every tool that returns one also returns a
plain-text fallback so the flow still works in a text-only client (e.g. the model just
reads the questions and calls the target tool itself).

We build the content block by hand (via ``mcp.types``) rather than pulling the TS
``@mcp-ui/server`` SDK, since the MCP here is Python/FastMCP. Identity and any other
dynamic value MUST be HTML-escaped by the caller before it reaches ``html_text`` — this
HTML is rendered in a browser context, so an un-escaped name is a stored-XSS vector.
"""
import html as _html

import mcp.types as _t

_UI_MIME = "text/html"


def esc(value) -> str:
    """HTML-escape a dynamic value (quotes included) for safe interpolation into markup."""
    return _html.escape("" if value is None else str(value), quote=True)


def ui_resource(uri: str, html_text: str) -> _t.EmbeddedResource:
    """Build an MCP-UI EmbeddedResource (``ui://…`` + text/html). Never embeds secrets."""
    if not uri.startswith("ui://"):
        raise ValueError("MCP-UI resource uri must start with 'ui://'")
    return _t.EmbeddedResource(
        type="resource",
        resource=_t.TextResourceContents(uri=uri, mimeType=_UI_MIME, text=html_text),
    )
