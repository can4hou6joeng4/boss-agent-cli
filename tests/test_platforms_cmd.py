"""本地平台能力清单命令测试。"""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from boss_agent_cli.commands.platforms import _render_platforms, platform_capability_data
from boss_agent_cli.main import cli

_DEFAULT_DATA_DIR = str((Path.home() / ".boss-agent").resolve())


def test_platforms_outputs_local_capability_matrix() -> None:
	runner = CliRunner()
	result = runner.invoke(cli, ["platforms"])

	assert result.exit_code == 0, result.output
	payload = json.loads(result.output)
	assert payload["ok"] is True
	assert payload["command"] == "platforms"
	assert payload["data"]["default"] == "zhipin"
	assert payload["data"]["aliases"] == {}
	legend = payload["data"]["capability_status_legend"]
	assert set(legend) == {"available", "not_supported"}
	assert "NOT_SUPPORTED" in legend["not_supported"]["description"]

	platforms = {item["name"]: item for item in payload["data"]["platforms"]}
	assert set(platforms) == {"zhipin"}
	assert platforms["zhipin"]["recruiter"] is True
	assert platforms["zhipin"]["capabilities"]["readonly"]["search"] == "available"
	assert platforms["zhipin"]["capabilities"]["write"]["greet"] == "available"
	assert "默认平台" in platforms["zhipin"]["notes"]


def test_platforms_json_payload_includes_status_legend() -> None:
	runner = CliRunner()
	result = runner.invoke(cli, ["platforms"])

	assert result.exit_code == 0, result.output
	payload = json.loads(result.output)
	legend = payload["data"]["capability_status_legend"]
	assert set(legend) == {"available", "not_supported"}
	assert legend["available"]["label"] == "可用"
	assert "NOT_SUPPORTED" in legend["not_supported"]["description"]


def test_platforms_terminal_render_includes_status_legend(capsys) -> None:
	_render_platforms(platform_capability_data())
	captured = capsys.readouterr()

	# TTY 渲染必须走 stderr：stdout 只留给七键 JSON 信封。
	assert captured.out == ""
	rendered = captured.err
	assert "capability_status_legend" in rendered
	assert "available" in rendered
	assert "可用" in rendered
	assert "not_supported" in rendered


def test_platforms_next_actions_name_every_listed_platform() -> None:
	"""``next_actions`` 要按本次列出的平台生成：``--platform`` 只接受真实平台名。

	原来固定写成 ``boss --platform <name> status``，Agent 原样执行只会拿到
	``INVALID_PARAM``（可选值只有 ``zhipin``），提示等于空转。
	"""
	runner = CliRunner()
	result = runner.invoke(cli, ["platforms"])

	assert result.exit_code == 0, result.output
	payload = json.loads(result.output)
	platform_names = [item["name"] for item in payload["data"]["platforms"]]
	assert payload["hints"]["next_actions"] == [
		*(f"boss --platform {name} --data-dir {_DEFAULT_DATA_DIR} status" for name in platform_names),
		f"boss --data-dir {_DEFAULT_DATA_DIR} schema",
	]
	assert "<name>" not in " ".join(payload["hints"]["next_actions"])


def test_platforms_can_filter_single_platform_by_registered_name() -> None:
	runner = CliRunner()
	result = runner.invoke(cli, ["platforms", "--platform", "zhipin"])

	assert result.exit_code == 0, result.output
	payload = json.loads(result.output)
	assert payload["data"]["count"] == 1
	assert payload["data"]["platforms"][0]["name"] == "zhipin"
	assert payload["data"]["platforms"][0]["status"] == "available"


def test_platforms_removed_platform_uses_json_error_envelope() -> None:
	runner = CliRunner()
	for platform_name in ("qiancheng", "51job", "zhilian"):
		result = runner.invoke(cli, ["platforms", "--platform", platform_name])
		assert result.exit_code == 1, result.output
		payload = json.loads(result.output)
		assert payload["ok"] is False
		assert payload["error"]["code"] == "INVALID_PARAM"
		assert "不支持的平台" in payload["error"]["message"]


def test_platforms_unknown_platform_uses_json_error_envelope() -> None:
	runner = CliRunner()
	result = runner.invoke(cli, ["platforms", "--platform", "unknown"])

	assert result.exit_code == 1, result.output
	payload = json.loads(result.output)
	assert payload["ok"] is False
	assert payload["error"]["code"] == "INVALID_PARAM"
	assert "不支持的平台" in payload["error"]["message"]


def test_platforms_can_filter_by_capability_status_groups() -> None:
	runner = CliRunner()
	result = runner.invoke(cli, ["platforms", "--capability", "status"])

	assert result.exit_code == 0, result.output
	payload = json.loads(result.output)
	data = payload["data"]
	assert data["count"] == 1
	assert data["capability_filter"] == {
		"capability": "status",
		"status_groups": {
			"available": ["zhipin"],
			"blocked_by_policy": [],
			"not_supported": [],
		},
	}
	assert all(item["capability_match"]["status"] == "available" for item in data["platforms"])


def test_platforms_can_filter_by_open_write_capability() -> None:
	runner = CliRunner()
	result = runner.invoke(cli, ["platforms", "--capability", "apply"])

	assert result.exit_code == 0, result.output
	payload = json.loads(result.output)
	assert payload["data"]["capability_filter"]["status_groups"] == {
		"available": ["zhipin"],
		"blocked_by_policy": [],
		"not_supported": [],
	}


def test_platforms_capability_filter_combines_with_platform_filter() -> None:
	runner = CliRunner()
	result = runner.invoke(cli, ["platforms", "--platform", "zhipin", "--capability", "search"])

	assert result.exit_code == 0, result.output
	payload = json.loads(result.output)
	assert payload["data"]["count"] == 1
	assert payload["data"]["capability_filter"]["status_groups"] == {
		"available": ["zhipin"],
		"blocked_by_policy": [],
		"not_supported": [],
	}
	assert payload["data"]["platforms"][0]["capability_match"]["status"] == "available"


def test_platforms_unknown_capability_uses_json_error_envelope() -> None:
	runner = CliRunner()
	result = runner.invoke(cli, ["platforms", "--capability", "unknown"])

	assert result.exit_code == 1, result.output
	payload = json.loads(result.output)
	assert payload["ok"] is False
	assert payload["error"]["code"] == "INVALID_PARAM"
	assert "不支持的能力" in payload["error"]["message"]


def test_platforms_terminal_render_includes_capability_columns(capsys) -> None:
	_render_platforms(platform_capability_data(capability="apply"))
	captured = capsys.readouterr()

	assert captured.out == ""
	rendered = captured.err
	assert "capability\tcapability_status" in rendered
	assert rendered.count("apply\tavailable") == 1
	assert "apply\tnot_supported" not in rendered


def test_platforms_is_listed_in_schema() -> None:
	runner = CliRunner()
	result = runner.invoke(cli, ["schema"])

	assert result.exit_code == 0, result.output
	payload = json.loads(result.output)
	platforms_schema = payload["data"]["commands"]["platforms"]
	assert platforms_schema["args"] == []
	assert "--platform" in platforms_schema["options"]
	assert platforms_schema["options"]["--platform"]["default"] is None
	assert "--capability" in platforms_schema["options"]
	capability_option = platforms_schema["options"]["--capability"]
	assert capability_option["default"] is None
	assert "available / not_supported" in capability_option["description"]
	assert "blocked_by_policy 仅保留空兼容分组" in capability_option["description"]
	assert "apply" in capability_option["choices"]
	assert "不触发登录" in platforms_schema["description"]
