"""Signed, identity-free notifications for self-service access requests.

The database queue is authoritative. This module only nudges an approved relay;
delivery failure must never discard or roll back an already-filed request.
"""
from __future__ import annotations

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
