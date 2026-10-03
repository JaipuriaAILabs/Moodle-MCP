#!/usr/bin/env python3
"""Mint the SUPABASE_AUDIT_KEY — a Supabase JWT whose role is `mcp_audit_writer`.

The audit writer role can ONLY execute public.record_mcp_tool_call (SECURITY DEFINER)
and touches no student data — it is the least-privilege credential the MCP uses to
persist activity into mcp_audit.*. This mints the same shape of custom-role JWT that
SUPABASE_DATA_KEY (reporting_readonly) already uses for this project, so no new
mechanism is introduced.

Usage:
    SUPABASE_JWT_SECRET='<project legacy JWT secret>' \\
        python3 scripts/mint_audit_jwt.py [--role mcp_audit_writer] [--years 10]

Prints the JWT to stdout. Set it in Render as SUPABASE_AUDIT_KEY. Never commit the
output or the secret. HS256 + the project's legacy JWT secret (Settings -> API ->
JWT Secret) — the method that already produced the reporting_readonly key.
"""
import argparse
import base64
import hashlib
import hmac
import json
import os
import sys
import time


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def mint(secret: str, role: str, years: int) -> str:
    now = int(time.time())
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "role": role,
        "iss": "supabase",
        "iat": now,
        "exp": now + years * 365 * 24 * 3600,
    }
    signing_input = f"{_b64url(json.dumps(header, separators=(',', ':')).encode())}." \
                    f"{_b64url(json.dumps(payload, separators=(',', ':')).encode())}"
    sig = hmac.new(secret.encode(), signing_input.encode(), hashlib.sha256).digest()
    return f"{signing_input}.{_b64url(sig)}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--role", default="mcp_audit_writer")
    ap.add_argument("--years", type=int, default=10)
    args = ap.parse_args()
    secret = os.environ.get("SUPABASE_JWT_SECRET", "").strip()
    if not secret:
        print("ERROR: set SUPABASE_JWT_SECRET (Supabase → Settings → API → JWT Secret)",
              file=sys.stderr)
        return 2
    print(mint(secret, args.role, args.years))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
