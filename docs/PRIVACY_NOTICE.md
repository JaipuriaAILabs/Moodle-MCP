# Moodle MCP — activity recording notice (DRAFT for review/legal)

A plain-language notice to publish (staff/student-facing) for the Jaipuria Moodle MCP.
Draft — the institution/legal owns the final wording and where it's surfaced.

## What this service is
The Moodle MCP lets verified Jaipuria accounts query student performance data (marks,
attendance, subjects, cohort analytics) and generate reports through AI assistants
(the client harness, Claude, Codex). Access requires signing in with a `jaipuria.ac.in` Google
account. Every verified account can query institutional student data across campuses.

## What we record ("to an extent")
For accountability, security, and service reliability, each interaction with the MCP is
logged to a locked, access-controlled store. We record **who did what**:
- the signed-in **identity** (name, email, campus scope);
- the **action** — which tool was used and **which student/cohort was queried**;
- **when**, the **outcome**, and the **source IP** of the request;
- **connection** events (when an account connects to the service).

We do **not** store the returned report/marks content in the activity log (the log
records *that* a record was accessed, not a second copy of the marks).

## Why
- **Security & accountability** — detect and investigate misuse of student data.
- **Support & reliability** — diagnose errors and performance.
- **Compliance** — an auditable record of who accessed what.

## How it's protected & how long we keep it
- Stored in a dedicated, **access-restricted** schema (row-level security; admins only).
- **Not** exported to third-party monitoring in identifiable form.
- **Retention: 180 days**, then automatically deleted (a scheduled daily purge).

## Your responsibilities
Access to student data is a professional trust. Do not access records you have no
legitimate need to see; misuse is traceable and subject to institutional policy.

## Questions / requests
Contact the programme office / data protection owner (insert contact). Requests about
your own logged activity can be actioned by an administrator.

---
*Owner: Jaipuria AI Labs · reflects the recording configuration as of 2026-09-21
(identity + action + source IP + connections; result payloads not logged).*
