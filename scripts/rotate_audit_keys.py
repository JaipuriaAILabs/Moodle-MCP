#!/usr/bin/env python3
"""Rotate the Moodle MCP audit secrets WITHOUT printing them to the terminal.

Generates a fresh:
  * MCP_AUDIT_HMAC_KEY   — the pseudonymization secret for user_subject
  * SUPABASE_AUDIT_KEY   — a Supabase JWT for role `mcp_audit_writer` (HS256)

...and writes both to `.audit_env.rotated.local` (gitignored) for you to paste into Render.
Only the SUPABASE_AUDIT_KEY needs the project JWT secret; get it from
Supabase dashboard → Project Settings → API → JWT Settings → "JWT Secret".

Usage:
    SUPABASE_JWT_SECRET='<paste-jwt-secret>' python3 scripts/rotate_audit_keys.py

The secret is read from the environment so it never lands in shell history or chat.
Nothing sensitive is printed; the new values go only to the output file.
"""
import base64
import hashlib
import hmac
import json
import os
import secrets
import sys
import time

OUT_FILE = ".audit_env.rotated.local"
ROLE = "mcp_audit_writer"
TEN_YEARS = 10 * 365 * 24 * 3600


def b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def mint_supabase_jwt(secret: str, role: str, ttl_seconds: int) -> str:
    now = int(time.time())
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {"role": role, "iss": "supabase", "iat": now, "exp": now + ttl_seconds}
    signing_input = f"{b64url(json.dumps(header, separators=(',', ':')).encode())}." \
                    f"{b64url(json.dumps(payload, separators=(',', ':')).encode())}"
    sig = hmac.new(secret.encode(), signing_input.encode(), hashlib.sha256).digest()
    return f"{signing_input}.{b64url(sig)}"


def main() -> int:
    jwt_secret = os.environ.get("SUPABASE_JWT_SECRET", "").strip()
    if not jwt_secret:
        print("ERROR: set SUPABASE_JWT_SECRET (Supabase → Settings → API → JWT Secret) and re-run.",
              file=sys.stderr)
        print("  SUPABASE_JWT_SECRET='<secret>' python3 scripts/rotate_audit_keys.py", file=sys.stderr)
        return 2

    new_hmac = secrets.token_urlsafe(32)
    new_jwt = mint_supabase_jwt(jwt_secret, ROLE, TEN_YEARS)

    # Sanity: the signature must verify against the same secret.
    signing_input, _, sig_b64 = new_jwt.rpartition(".")
    expected = b64url(hmac.new(jwt_secret.encode(), signing_input.encode(), hashlib.sha256).digest())
    assert hmac.compare_digest(expected, sig_b64), "self-verify failed — do not use this JWT"

    with open(OUT_FILE, "w") as fh:
        fh.write("# Rotated audit secrets — paste into Render (jaipuria-moodle-mcp → Environment).\n")
        fh.write("# Gitignored. DELETE this file after pasting. Do NOT commit or share.\n")
        fh.write(f"SUPABASE_AUDIT_KEY={new_jwt}\n")
        fh.write(f"MCP_AUDIT_HMAC_KEY={new_hmac}\n")
    os.chmod(OUT_FILE, 0o600)

    print(f"OK — wrote 2 fresh values to {OUT_FILE} (mode 600).")
    print("Next: paste both into Render env, Save (auto-redeploy), then verify a new mcp_audit row.")
    print("NOTE: rotating the HMAC key changes user_subject pseudonyms — pre-launch rows won't")
    print("      correlate with post-rotation rows (fine now: negligible history).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
