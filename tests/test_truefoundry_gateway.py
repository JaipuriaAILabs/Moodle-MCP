"""The checked-in TrueFoundry gateway state must stay aligned with MCP RBAC."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from validate_truefoundry_gateway import validate  # noqa: E402


def test_truefoundry_gateway_desired_state():
    validate()
