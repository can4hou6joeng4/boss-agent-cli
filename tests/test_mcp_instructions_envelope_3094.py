"""Issue #3094：MCP 宿主提示必须与运行时七键信封同源。"""

from __future__ import annotations

import json

from boss_agent_cli.mcp_server import SERVER_INSTRUCTIONS
from boss_agent_cli.output import envelope_success


def test_instructions_list_full_runtime_envelope_keys() -> None:
	keys = list(json.loads(envelope_success("test", {})).keys())
	assert keys == ["ok", "schema_version", "command", "data", "pagination", "error", "hints"]
	assert "{" + ", ".join(keys) + "}" in SERVER_INSTRUCTIONS
