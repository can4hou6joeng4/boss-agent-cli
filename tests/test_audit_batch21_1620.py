"""批次二十一回归：Issue #1620，超大整数参数不得击穿 MCP 信封。"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from boss_agent_cli import mcp_server


@pytest.fixture
def tool_result(monkeypatch):
	monkeypatch.setattr(mcp_server, "TextContent", SimpleNamespace)
	monkeypatch.setattr(mcp_server, "CallToolResult", SimpleNamespace)

	async def forbidden(*args, **kwargs):
		raise AssertionError("参数校验失败不应启动 CLI 子进程")

	monkeypatch.setattr(mcp_server, "_run_boss_async", forbidden)
	return mcp_server.call_tool


def test_huge_scalar_integer_returns_invalid_param_envelope(monkeypatch, tool_result):
	from mcp.types import CallToolRequestParams

	result = asyncio.run(
		tool_result(None, CallToolRequestParams(name="boss_hr_chat", arguments={"summary_limit": 10**5000}))
	)
	payload = json.loads(result.content[0].text)

	assert result.is_error is True
	assert payload["ok"] is False
	assert payload["error"]["code"] == "INVALID_PARAM"
	assert "5000" not in payload["error"]["message"]


def test_huge_array_item_returns_invalid_param_envelope(monkeypatch, tool_result):
	from mcp.types import CallToolRequestParams

	result = asyncio.run(
		tool_result(
			None,
			CallToolRequestParams(name="boss_hr_last_messages", arguments={"friend_ids": [10**5000]}),
		)
	)
	payload = json.loads(result.content[0].text)

	assert result.is_error is True
	assert payload["error"]["code"] == "INVALID_PARAM"
	assert "超出本地数值表示范围" in payload["error"]["message"]
