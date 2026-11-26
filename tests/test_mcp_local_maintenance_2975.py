"""MCP 本地维护工具：favorites sync / resume init / resume delete（Issue #2975）。"""

from __future__ import annotations

import json

from click.testing import CliRunner
import pytest

from boss_agent_cli.commands.favorites import MAX_FAVORITES_PAGES
from boss_agent_cli.main import cli
from boss_agent_cli.mcp_args import _build_args
from boss_agent_cli.mcp_server import (
	_MAX_CLI_TIMEOUT_SECONDS,
	_MAX_ESTIMATED_REQUESTS,
	_cli_timeout,
	_estimated_requests,
	_write_interrupt_recovery,
)
from boss_agent_cli.mcp_tools import TOOLS


def _tool(name: str):
	return next(tool for tool in TOOLS if tool.name == name)


def test_local_maintenance_tools_are_registered() -> None:
	names = {tool.name for tool in TOOLS}
	assert {"boss_favorites_sync", "boss_resume_init", "boss_resume_delete"} <= names


def test_favorites_sync_schema_has_no_parameters() -> None:
	schema = _tool("boss_favorites_sync").input_schema
	assert schema["properties"] == {}
	assert schema["required"] == []
	assert schema["additionalProperties"] is False


def test_resume_init_schema_declares_only_default_template() -> None:
	schema = _tool("boss_resume_init").input_schema
	assert set(schema["properties"]) == {"name", "template"}
	assert schema["required"] == []
	assert schema["properties"]["template"]["enum"] == ["default"]
	assert schema["properties"]["template"]["default"] == "default"


def test_resume_delete_schema_requires_non_blank_name() -> None:
	schema = _tool("boss_resume_delete").input_schema
	assert schema["required"] == ["name"]
	assert schema["properties"]["name"]["minLength"] == 1
	assert "不可恢复" in _tool("boss_resume_delete").description


def test_build_args_for_local_maintenance_tools() -> None:
	assert _build_args("boss_favorites_sync", {}) == ["favorites", "sync"]
	assert _build_args("boss_resume_init", {}) == ["resume", "init"]
	assert _build_args("boss_resume_init", {"name": "演示", "template": "default"}) == [
		"resume",
		"init",
		"--name",
		"演示",
		"--template",
		"default",
	]
	assert _build_args("boss_resume_delete", {"name": "演示"}) == ["resume", "delete", "演示"]


@pytest.mark.parametrize("arguments", [{}, {"name": "演示", "template": "default"}])
def test_resume_init_mapping_parses_with_cli(arguments: dict[str, str]) -> None:
	command = cli.commands["resume"].commands["init"]
	ctx = command.make_context("init", _build_args("boss_resume_init", arguments)[2:])
	assert ctx.params["name"] == arguments.get("name")
	assert ctx.params["template"] == arguments.get("template")
	ctx.close()


def test_resume_init_explicit_blank_name_is_not_replaced_by_default(tmp_path) -> None:
	"""显式空名必须原样下发由 CLI 拒绝；真值判断会静默改成默认简历名。"""
	argv = ["--json", "--data-dir", str(tmp_path), *_build_args("boss_resume_init", {"name": ""})]
	result = CliRunner().invoke(cli, argv)
	assert result.exit_code == 1, result.output
	payload = json.loads(result.stdout)
	assert payload["error"]["code"] == "INVALID_PARAM"
	assert not (tmp_path / "resume").exists() or not any((tmp_path / "resume").iterdir())


def test_resume_init_and_delete_round_trip_offline(tmp_path) -> None:
	runner = CliRunner()
	prefix = ["--json", "--data-dir", str(tmp_path)]
	created = runner.invoke(cli, [*prefix, *_build_args("boss_resume_init", {"name": "演示"})])
	assert created.exit_code == 0, created.output
	assert json.loads(created.stdout)["ok"] is True
	listed = runner.invoke(cli, [*prefix, *_build_args("boss_resume_list", {})])
	assert listed.exit_code == 0, listed.output
	assert [item["name"] for item in json.loads(listed.stdout)["data"]] == ["演示"]
	removed = runner.invoke(cli, [*prefix, *_build_args("boss_resume_delete", {"name": "演示"})])
	assert removed.exit_code == 0, removed.output
	assert json.loads(removed.stdout)["ok"] is True
	listed_after = runner.invoke(cli, [*prefix, *_build_args("boss_resume_list", {})])
	assert json.loads(listed_after.stdout)["data"] == []


def test_favorites_sync_timeout_estimate_uses_max_budget() -> None:
	"""默认 120s 只够少数页；收藏同步没有分页参数可拆，必须给满封顶估算。"""
	assert _estimated_requests(("favorites", "sync")) == _MAX_ESTIMATED_REQUESTS
	assert _cli_timeout(("favorites", "sync")) == _MAX_CLI_TIMEOUT_SECONDS
	assert MAX_FAVORITES_PAGES > _MAX_ESTIMATED_REQUESTS  # 页数上限超过估算可覆盖的请求数
	assert _estimated_requests(("favorites", "list")) is None


def test_local_write_interrupt_recovery_does_not_send_agent_to_platform() -> None:
	for argv in (("resume", "init", "--name", "演示"), ("resume", "delete", "演示")):
		message = _write_interrupt_recovery(argv)
		assert "boss_resume_list" in message
		assert "不产生平台写入" in message
	sync_message = _write_interrupt_recovery(("favorites", "sync"))
	assert "upsert" in sync_message
	assert "平台侧只读" in sync_message
