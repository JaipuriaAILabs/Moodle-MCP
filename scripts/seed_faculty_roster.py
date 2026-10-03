#!/usr/bin/env python3
"""Phase 2 — bulk-load the per-campus faculty roster into public.mcp_faculty.

Reads a CSV (see docs/faculty_roster_template.csv), validates every row against the
same rules the DB CHECK constraints enforce (so a typo is caught BEFORE it hits the
table), and upserts by email via PostgREST. Writes use the service role, because the
registry is intentionally not writable by anon/authenticated.

Usage:
    SUPABASE_URL=https://sadbfvfcmmxgtatfjfmc.supabase.co \
    SUPABASE_SERVICE_ROLE_KEY='<service-role JWT>' \
    python3 scripts/seed_faculty_roster.py path/to/roster.csv [--dry-run]

--dry-run validates + prints what WOULD be written, and touches nothing. Run it first.
The service key never appears on the command line (read from env), and is not printed.
"""
import csv
import json
import os
import sys
import urllib.request
from urllib.parse import urlsplit

VALID_ROLES = {"admin", "cross_campus", "campus_admin", "faculty", "viewer"}
ALL_ROLES = {"admin", "cross_campus"}          # must have campuses = "all"
SCOPED_ROLES = {"campus_admin", "faculty", "viewer"}  # must have a campus list
CAMPUSES = set((os.environ.get("MCP_CAMPUSES")
                or "noida,lucknow,jaipur,indore").lower().split(","))


def parse_row(row: dict, lineno: int):
    """Validate one CSV row -> a clean dict ready to upsert, or raise ValueError."""
    email = (row.get("email") or "").strip().lower()
    if email.count("@") != 1 or not email.split("@")[1]:
        raise ValueError(f"line {lineno}: bad email {email!r}")
    role = (row.get("role") or "faculty").strip().lower()
    if role not in VALID_ROLES:
        raise ValueError(f"line {lineno}: role {role!r} not in {sorted(VALID_ROLES)}")

    raw_campuses = (row.get("campuses") or "").strip()
    if role in ALL_ROLES:
        if raw_campuses.lower() not in ("all", ""):
            raise ValueError(f"line {lineno}: role {role} must have campuses=all, got {raw_campuses!r}")
        campuses = "all"
    else:  # scoped role -> non-empty list of known campuses
        items = [c.strip().lower() for c in raw_campuses.split("|") if c.strip()]
        if not items:
            raise ValueError(f"line {lineno}: role {role} needs a campus list (e.g. noida|lucknow)")
        bad = [c for c in items if c not in CAMPUSES]
        if bad:
            raise ValueError(f"line {lineno}: unknown campus(es) {bad} — valid: {sorted(CAMPUSES)}")
        campuses = items

    cg = (row.get("can_generate") or "true").strip().lower()
    if cg not in ("true", "false"):
        raise ValueError(f"line {lineno}: can_generate must be true/false, got {cg!r}")

    return {
        "email": email,
        "name": (row.get("name") or "").strip() or None,
        "role": role,
        "campuses": campuses,                 # jsonb: "all" (str) or [..] (list)
        "can_generate": cg == "true",
        "active": True,
        "note": (row.get("note") or "").strip() or None,
        "granted_by": os.environ.get("SEED_GRANTED_BY", "roster-import"),
    }


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    dry = "--dry-run" in sys.argv
    if not args:
        print("usage: seed_faculty_roster.py <roster.csv> [--dry-run]", file=sys.stderr)
        return 2
    url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if not dry and (not url or not key):
        print("ERROR: set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY (or use --dry-run).",
              file=sys.stderr)
        return 2
    if not dry:
        parsed_url = urlsplit(url)
        if (parsed_url.scheme != "https" or not parsed_url.hostname
                or parsed_url.username or parsed_url.password
                or parsed_url.query or parsed_url.fragment):
            print("ERROR: SUPABASE_URL must be HTTPS without credentials, query, or fragment.",
                  file=sys.stderr)
            return 2

    rows, errors = [], []
    with open(args[0], newline="", encoding="utf-8") as fh:
        for i, raw in enumerate(csv.DictReader(fh), start=2):
            if not (raw.get("email") or "").strip() or (raw.get("email") or "").lstrip().startswith("#"):
                continue  # skip blank / comment lines
            try:
                rows.append(parse_row(raw, i))
            except ValueError as e:
                errors.append(str(e))
    if errors:
        print("VALIDATION FAILED — nothing written:", file=sys.stderr)
        for e in errors:
            print("  -", e, file=sys.stderr)
        return 1

    print(f"{len(rows)} valid row(s):")
    for r in rows:
        print(f"  {r['email']:<40} {r['role']:<13} "
              f"{'all' if r['campuses']=='all' else ','.join(r['campuses'])}"
              f"{'' if r['can_generate'] else '  (no-generate)'}")
    if dry:
        print("\n--dry-run: no changes made.")
        return 0

    body = json.dumps(rows).encode()
    req = urllib.request.Request(
        f"{url}/rest/v1/mcp_faculty?on_conflict=email", data=body, method="POST",
        headers={"apikey": key, "Authorization": f"Bearer {key}",
                 "Content-Type": "application/json",
                 "Prefer": "resolution=merge-duplicates,return=minimal"})
    try:
        # SUPABASE_URL was constrained to a credential-free HTTPS authority above.
        with urllib.request.urlopen(req, timeout=30) as resp:  # nosec B310
            print(f"\nUpserted {len(rows)} row(s) — HTTP {resp.status}.")
    except urllib.error.HTTPError as e:
        print(f"\nUpsert FAILED HTTP {e.code}: {e.read().decode()[:500]}", file=sys.stderr)
        return 1
    print("Done. Verify: select email, role, campuses, active from public.mcp_faculty order by email;")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
