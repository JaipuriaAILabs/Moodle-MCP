# Access-request approver webhook

The webhook is an optional nudge after `request_access` has already written the authoritative
database queue. It never contains requester identity; an administrator uses `list_access_requests`
or the locked table to see who requested access.

Configure both values or neither (partial/plaintext/short-secret configurations fail boot):

```text
MCP_ACCESS_REQUEST_WEBHOOK_URL=https://approved-relay.example/access-requests
MCP_ACCESS_REQUEST_WEBHOOK_SECRET=<independent random value, at least 32 characters>
```

Payload:

```json
{
  "event": "access_request.created",
  "event_id": "<random UUID>",
  "requested_at": 1790850000,
  "requested_role": "faculty",
  "requested_campuses": ["noida"]
}
```

Verification contract:

1. Read the exact raw request body; do not re-serialize JSON before verification.
2. Reject timestamps outside a five-minute window.
3. Reject any previously accepted `X-MCP-Nonce` during that window.
4. Compute hexadecimal
   `HMAC-SHA256(secret, timestamp + "." + nonce + "." + raw_body)`.
5. Constant-time compare `X-MCP-Signature` with `sha256=<hex digest>`.
6. Require `X-MCP-Event: access_request.created` and the expected JSON schema.
7. Only after verification, post a generic alert to the approved email/Slack/Teams destination.

Webhook failure never changes a successful request into an error. Alert separately on
`access-request notification failed`; approvers can always poll the queue.
