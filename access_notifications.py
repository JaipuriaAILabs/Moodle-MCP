"""Signed, identity-free notifications for self-service access requests.

The database queue is authoritative. This module only nudges an approved relay;
delivery failure must never discard or roll back an already-filed request.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import time
import uuid

import httpx

from config import settings

log = logging.getLogger("moodle-mcp.access-notifications")

_http: httpx.AsyncClient | None = None


def _client() -> httpx.AsyncClient:
    global _http
    if _http is None:
        _http = httpx.AsyncClient(timeout=3.0, follow_redirects=False)
    return _http


def _signed_headers(body: bytes, *, timestamp: str, nonce: str) -> dict[str, str]:
    message = timestamp.encode() + b"." + nonce.encode() + b"." + body
    digest = hmac.new(settings.access_request_webhook_secret.encode(),
                      message, hashlib.sha256).hexdigest()
    return {
        "Content-Type": "application/json",
        "X-MCP-Event": "access_request.created",
        "X-MCP-Timestamp": timestamp,
        "X-MCP-Nonce": nonce,
        "X-MCP-Signature": f"sha256={digest}",
    }


async def notify_access_request(*, role: str, campuses: list[str]) -> bool:
    """Best-effort approver nudge when a self-service request is filed. Fans out to whichever
    channels are configured — a signed webhook and/or an email to the approver — each carrying
    role/campus ONLY (the requester identity stays in the admin-only DB queue). The queue is
    authoritative; a notification failure never discards or rolls back the filed request."""
    delivered = False
    if settings.access_request_webhook_url.strip() and settings.access_request_webhook_secret.strip():
        delivered = await _notify_webhook(role=role, campuses=campuses) or delivered
    if settings.access_email_enabled():
        delivered = await _notify_email(role=role, campuses=campuses) or delivered
    return delivered


async def _notify_email(*, role: str, campuses: list[str]) -> bool:
    """Email the approver a PII-free 'a request is pending' nudge (role + campus only).
    SMTP is blocking, so it runs in a worker thread; failures are swallowed (best-effort)."""
    try:
        await asyncio.to_thread(_send_smtp, role, campuses)
        return True
    except Exception as exc:  # noqa: BLE001 — queue already persisted; the email is a nudge
        log.warning("access-request email failed: %s", type(exc).__name__)
        return False


def _send_smtp(role: str, campuses: list[str]) -> None:
    import smtplib
    import ssl
    from email.message import EmailMessage

    msg = EmailMessage()
    msg["Subject"] = "Moodle MCP — a new access request is pending approval"
    msg["From"] = settings.mail_from.strip() or settings.smtp_user.strip()
    msg["To"] = settings.access_request_email_to.strip()
    # No requester identity in the body — approvers open the admin-only queue for who/when.
    msg.set_content(
        "A new self-service access request is pending in the Moodle MCP.\n\n"
        f"Requested role:    {role}\n"
        f"Requested campus:  {', '.join(campuses) or '(none)'}\n\n"
        "Review and approve it in the mcp_access_requests queue "
        "(the requester's identity is recorded there, not in this email)."
    )
    with smtplib.SMTP(settings.smtp_host.strip(), settings.smtp_port, timeout=10) as smtp:
        smtp.starttls(context=ssl.create_default_context())
        if settings.smtp_user.strip():
            smtp.login(settings.smtp_user.strip(), settings.smtp_pass)
        smtp.send_message(msg)


async def _notify_webhook(*, role: str, campuses: list[str]) -> bool:
    """Deliver a signed event with role/campus only; identity stays in the queue."""
    url = settings.access_request_webhook_url.strip()
    secret = settings.access_request_webhook_secret.strip()
    if not url or not secret:
        return False
    event_id = str(uuid.uuid4())
    payload = {
        "event": "access_request.created",
        "event_id": event_id,
        "requested_at": int(time.time()),
        "requested_role": role,
        "requested_campuses": campuses,
    }
    body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    timestamp = str(int(time.time()))
    try:
        response = await _client().post(
            url, content=body,
            headers=_signed_headers(body, timestamp=timestamp, nonce=event_id))
        response.raise_for_status()
        return True
    except Exception as exc:  # noqa: BLE001 — queue already persisted; notification is a nudge
        # URL, response body, and exception text are deliberately omitted.
        log.warning("access-request notification failed: %s", type(exc).__name__)
        return False
