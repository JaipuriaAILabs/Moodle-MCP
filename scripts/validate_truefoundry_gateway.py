#!/usr/bin/env python3
"""Static safety checks for the TrueFoundry MCP Gateway desired state.

The files use JSON syntax with a ``.yaml`` extension: JSON is valid YAML, works with
``tfy apply``, and lets this validator stay dependency-free.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANIFESTS = ROOT / "truefoundry" / "manifests"
POLICY = ROOT / "truefoundry" / "policies" / "moodle-access.cedar"

APPROVED_STAFF = {
    "shreevats@jaipuria.ac.in",
    "shiva.kakkar@jaipuria.ac.in",
    "prabhat.pankaj@jaipuria.ac.in",
    "shailaja.sharma@jaipuria.ac.in",
    "amit.attry@jaipuria.ac.in",
    "sauresh.mehrotra@jaipuria.ac.in",
    "kanchan.rana@jaipuria.ac.in",
    "sandip.das@jaipuria.ac.in",
    "rahul.singh@jaipuria.ac.in",
    "sushma.vishnani@jaipuria.ac.in",
    "daneshwar.sharma@jaipuria.ac.in",
    "deepankar.chakrabarti@jaipuria.ac.in",
}
ACCESS_ADMIN_TOOLS = {"request_access", "list_access_requests"}


def _load(name: str) -> dict:
    path = MANIFESTS / name
    text = path.read_text(encoding="utf-8")
    assert "<string>" not in text and "TODO" not in text, f"placeholder in {path}"
    return json.loads(text)


def _registered_tools() -> set[str]:
    result: set[str] = set()
    for path in [ROOT / "server.py", *sorted((ROOT / "tools").glob("*.py"))]:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                call = decorator if isinstance(decorator, ast.Call) else None
                if call and isinstance(call.func, ast.Attribute) and call.func.attr == "tool":
                    result.add(node.name)
    return result


def _student_self_tools() -> set[str]:
    tree = ast.parse((ROOT / "security.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(target, ast.Name) and target.id == "STUDENT_SELF_TOOLS"
                   for target in node.targets):
            continue
        call = node.value
        assert isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
        assert call.func.id == "frozenset" and len(call.args) == 1
        return set(ast.literal_eval(call.args[0]))
    raise AssertionError("STUDENT_SELF_TOOLS not found")


def _source_tools(manifest: dict) -> set[str]:
    assert len(manifest["servers"]) == 1
    assert manifest["servers"][0]["name"] == "jaipuria-moodle-prod"
    return set(manifest["servers"][0]["enabled_tools"])


def validate() -> None:
    student_self_tools = _student_self_tools()
    registered = _registered_tools()
    data_tools = registered - ACCESS_ADMIN_TOOLS
    assert ACCESS_ADMIN_TOOLS <= registered

    remote = _load("00-moodle-remote.yaml")
    assert remote["type"] == "mcp-server/remote"
    assert remote["url"] == "https://moodle-mcp.rehearsal-os.app/mcp"
    assert remote["tls_settings"]["reject_unauthorized"] is True
    assert remote["tool_policy"]["enable_tools_by_default"] is False
    assert set(remote["tool_policy"]["enabled_tools"]) == data_tools
    auth = remote["auth_data"]
    assert auth["type"] == "oauth2"
    assert auth["grant_type"] == "authorization_code"
    assert auth["registration_url"].endswith("/register")
    assert auth["jwt_source"] == "access_token"
    assert auth["code_challenge_methods_supported"] == ["S256"]
    assert "provider" not in auth and "include_resource" not in auth
    assert "headers" not in auth, "interactive users must never share an upstream bearer"
    assert "collaborators" not in remote, "end users must use curated virtual servers"

    student = _load("10-moodle-student-virtual.yaml")
    assert student["type"] == "mcp-server/virtual"
    assert student["best_effort_mode"] is False
    assert _source_tools(student) == student_self_tools
    assert student["collaborators"] == [
        {"subject": "team:everyone", "role_id": "mcp-server-user"}
    ]

    staff = _load("20-moodle-staff-virtual.yaml")
    assert staff["type"] == "mcp-server/virtual"
    assert staff["best_effort_mode"] is False
    assert _source_tools(staff) == data_tools
    staff_users = {
        row["subject"].removeprefix("user:")
        for row in staff["collaborators"]
        if row["role_id"] == "mcp-server-user"
    }
    assert staff_users == APPROVED_STAFF

    user_limit = _load("30-moodle-user-rate-limit.yaml")
    assert user_limit["type"] == "tenant-rate-limit-config/mcp"
    assert user_limit["when"]["mcp_servers"]["in"] == ["jaipuria-moodle-prod"]
    assert user_limit["applies_to"]["type"] == "per-user"
    assert user_limit["mode"] == "enforce"
    assert user_limit["log_request_body_on_block"] is False

    report_limit = _load("31-moodle-report-rate-limit.yaml")
    assert report_limit["when"]["mcp_servers"]["in"] == [
        "jaipuria-moodle-prod:create_report"
    ]
    assert report_limit["limits"]["tool_calls_per_hour"] == 60
    assert report_limit["applies_to"]["type"] == "per-user"
    assert report_limit["log_request_body_on_block"] is False

    data_access = _load("40-gateway-data-access.yaml")
    assert data_access["type"] == "gateway-data-access-config"
    rules = {rule["id"]: rule for rule in data_access["rules"]}
    own = rules["default-everyone-own-data"]
    assert own["subjects"] == ["team:everyone"]
    assert set(own["data_types"]) == {"traces", "metrics"}
    assert own["scope"] == "own_data" and own["enabled"] is True
    team = rules["default-everyone-team-data"]
    assert team["subjects"] == ["team:everyone"]
    assert team["data_types"] == ["metrics"]
    assert team["scope"] == "team_virtual_account_data" and team["enabled"] is True
    admin = rules["jaipuria-tenant-admin-all-data"]
    assert admin["subjects"] == ["role:tenant-admin"]
    assert set(admin["data_types"]) == {"traces", "metrics"}
    assert admin["scope"] == "all_data" and admin["enabled"] is True

    policy = POLICY.read_text(encoding="utf-8")
    assert 'resource == MCPServer::"jaipuria-moodle-prod"' in policy
    for email in APPROVED_STAFF:
        assert f'"{email}"' in policy
    for tool in student_self_tools:
        assert f'"{tool}"' in policy
    assert '"request_access"' not in policy
    assert '"list_access_requests"' not in policy


if __name__ == "__main__":
    validate()
    print("TrueFoundry gateway desired state: valid")
