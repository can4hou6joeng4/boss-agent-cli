"""本地平台能力清单命令测试。"""

from __future__ import annotations

import json

from click.testing import CliRunner

from boss_agent_cli.commands.platforms import _render_platforms, platform_capability_data
from boss_agent_cli.main import cli


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
	assert set(platforms) == {"zhipin", "zhilian"}
	assert platforms["zhipin"]["recruiter"] is True
	assert platforms["zhilian"]["capabilities"]["readonly"]["search"] == "available"
	assert platforms["zhilian"]["capabilities"]["readonly"]["show"] == "available"
	assert platforms["zhilian"]["capabilities"]["readonly"]["history"] == "available"
	assert platforms["zhilian"]["capabilities"]["readonly"]["interviews"] == "available"
	assert platforms["zhilian"]["capabilities"]["write"]["greet"] == "available"
	assert "投递和沟通" in platforms["zhilian"]["notes"]


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

	rendered = captured.out + captured.err
	assert "capability_status_legend" in rendered
	assert "available" in rendered
	assert "可用" in rendered
	assert "not_supported" in rendered


def test_platforms_can_filter_single_platform_by_registered_name() -> None:
	runner = CliRunner()
	result = runner.invoke(cli, ["platforms", "--platform", "zhilian"])

	assert result.exit_code == 0, result.output
	payload = json.loads(result.output)
	assert payload["data"]["count"] == 1
	assert payload["data"]["platforms"][0]["name"] == "zhilian"
	assert payload["data"]["platforms"][0]["status"] == "available"


def test_platforms_removed_platform_uses_json_error_envelope() -> None:
	runner = CliRunner()
	for platform_name in ("qiancheng", "51job"):
		result = runner.invoke(cli, ["platforms", "--platform", platform_name])
		assert result.exit_code == 1, result.output
		payload = json.loads(result.output)
		assert payload["ok"] is False
		assert payload["error"]["code"] == "INVALID_PARAM"
		assert "unknown platform" in payload["error"]["message"]


def test_platforms_unknown_platform_uses_json_error_envelope() -> None:
	runner = CliRunner()
	result = runner.invoke(cli, ["platforms", "--platform", "unknown"])

	assert result.exit_code == 1, result.output
	payload = json.loads(result.output)
	assert payload["ok"] is False
	assert payload["error"]["code"] == "INVALID_PARAM"
	assert "unknown platform" in payload["error"]["message"]


def test_platforms_can_filter_by_capability_status_groups() -> None:
	runner = CliRunner()
	result = runner.invoke(cli, ["platforms", "--capability", "status"])

	assert result.exit_code == 0, result.output
	payload = json.loads(result.output)
	data = payload["data"]
	assert data["count"] == 2
	assert data["capability_filter"] == {
		"capability": "status",
		"status_groups": {
			"available": ["zhilian", "zhipin"],
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
		"available": ["zhilian", "zhipin"],
		"blocked_by_policy": [],
		"not_supported": [],
	}


def test_platforms_capability_filter_combines_with_platform_filter() -> None:
	runner = CliRunner()
	result = runner.invoke(cli, ["platforms", "--platform", "zhilian", "--capability", "search"])

	assert result.exit_code == 0, result.output
	payload = json.loads(result.output)
	assert payload["data"]["count"] == 1
	assert payload["data"]["capability_filter"]["status_groups"] == {
		"available": ["zhilian"],
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
	assert "unknown capability" in payload["error"]["message"]


def test_platforms_terminal_render_includes_capability_columns(capsys) -> None:
	_render_platforms(platform_capability_data(capability="apply"))
	captured = capsys.readouterr()

	rendered = captured.out + captured.err
	assert "capability\tcapability_status" in rendered
	assert rendered.count("apply\tavailable") == 2
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
