"""CLI / MCP / 向导的 watch add 都支持活跃度过滤（Issue #2704）。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from boss_agent_cli.cache.store import CacheStore
from boss_agent_cli.main import cli
from boss_agent_cli.mcp_args import _build_args
from boss_agent_cli.schema.data import SCHEMA_DATA
from boss_agent_cli.schema.tool_catalog import build_tool_catalog


def _run(tmp_path: Path, *args: str) -> tuple[Any, dict[str, Any]]:
	result = CliRunner().invoke(
		cli, ["--data-dir", str(tmp_path), "watch", "add", *args], obj={}
	)
	envelope = json.loads(result.output)
	return result, envelope


def _params(tmp_path: Path, name: str) -> dict[str, Any] | None:
	with CacheStore(tmp_path / "cache" / "boss_agent.db") as cache:
		record = cache.get_saved_search(name)
	return None if record is None else record["params"]


@pytest.mark.parametrize(
	("raw", "stored"),
	[("3day", "3d"), ("3日", "3d"), ("ONLINE", "online"), ("week", "week")],
)
def test_cli_watch_add_normalizes_active(tmp_path: Path, raw: str, stored: str) -> None:
	result, envelope = _run(tmp_path, "daily", "Python", "--active", raw)
	assert result.exit_code == 0, result.output
	assert envelope["ok"] is True
	assert envelope["data"]["params"]["active"] == stored
	assert _params(tmp_path, "daily") == {**envelope["data"]["params"]}


def test_cli_watch_add_without_active_omits_key(tmp_path: Path) -> None:
	result, envelope = _run(tmp_path, "plain", "Python")
	assert result.exit_code == 0, result.output
	assert "active" not in envelope["data"]["params"]
	assert "active" not in (_params(tmp_path, "plain") or {})


def test_cli_watch_add_rejects_unknown_active_before_write(tmp_path: Path) -> None:
	result, envelope = _run(tmp_path, "daily", "Python", "--active", "never")
	assert result.exit_code == 1
	assert envelope["ok"] is False
	assert envelope["error"]["code"] == "INVALID_PARAM"
	assert _params(tmp_path, "daily") is None


def test_schema_exposes_active_for_watch_add() -> None:
	option = SCHEMA_DATA["commands"]["watch"]["options"]["add"]["--active"]
	assert option["choices"] == ["online", "today", "3d", "week", "2w", "month", "half-year"]


def test_mcp_watch_add_forwards_active() -> None:
	assert _build_args("watch_add", {"name": "daily", "query": "Python", "active": "3d"}) == [
		"watch",
		"add",
		"daily",
		"Python",
		"--active",
		"3d",
	]
	assert _build_args("watch_add", {"name": "daily", "query": "Python"}) == ["watch", "add", "daily", "Python"]


def test_mcp_schema_has_active() -> None:
	tool = next(tool for tool in build_tool_catalog() if tool.name == "boss_watch_add")
	active = tool.input_schema["properties"]["active"]
	assert active["type"] == "string"
	assert active["minLength"] == 1
