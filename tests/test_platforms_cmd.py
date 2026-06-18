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
	assert payload["data"]["aliases"] == {"51job": "qiancheng"}
	legend = payload["data"]["capability_status_legend"]
	assert set(legend) == {"available", "not_supported", "placeholder_only", "low_risk_blocked"}
	assert "NOT_SUPPORTED" in legend["not_supported"]["description"]
	assert "低风险模式" in legend["low_risk_blocked"]["label"]
	assert "不代表真实平台能力" in legend["placeholder_only"]["description"]

	platforms = {item["name"]: item for item in payload["data"]["platforms"]}
	assert set(platforms) == {"qiancheng", "zhipin", "zhilian"}
	assert platforms["qiancheng"]["status"] == "placeholder"
	assert platforms["qiancheng"]["capabilities"]["readonly"]["search"] == "not_supported"
	assert platforms["qiancheng"]["capabilities"]["readonly"]["status"] == "placeholder_only"
	assert "NOT_SUPPORTED" in platforms["qiancheng"]["notes"]
	assert platforms["zhipin"]["recruiter"] is True
	assert platforms["zhilian"]["capabilities"]["readonly"]["search"] == "available"
	assert platforms["zhilian"]["capabilities"]["readonly"]["show"] == "available"
	assert platforms["zhilian"]["capabilities"]["readonly"]["history"] == "available"
	assert platforms["zhilian"]["capabilities"]["readonly"]["interviews"] == "available"
	assert platforms["zhilian"]["capabilities"]["write"]["greet"] == "low_risk_blocked"
	assert "只读 + 本地辅助" in platforms["zhilian"]["notes"]


def test_platforms_json_payload_includes_status_legend() -> None:
	runner = CliRunner()
	result = runner.invoke(cli, ["platforms"])

	assert result.exit_code == 0, result.output
	payload = json.loads(result.output)
	legend = payload["data"]["capability_status_legend"]
	assert set(legend) == {"available", "not_supported", "placeholder_only", "low_risk_blocked"}
	assert legend["available"]["label"] == "可用"
	assert "NOT_SUPPORTED" in legend["not_supported"]["description"]
	assert "不代表真实平台能力" in legend["placeholder_only"]["description"]
	assert "默认低风险模式阻断" in legend["low_risk_blocked"]["description"]


def test_platforms_terminal_render_includes_status_legend(capsys) -> None:
	_render_platforms(platform_capability_data())
	captured = capsys.readouterr()

	rendered = captured.out + captured.err
	assert "capability_status_legend" in rendered
	assert "available" in rendered
	assert "可用" in rendered
	assert "not_supported" in rendered
	assert "placeholder_only" in rendered
	assert "low_risk_blocked" in rendered


def test_platforms_can_filter_single_platform_by_registered_name() -> None:
	runner = CliRunner()
	result = runner.invoke(cli, ["platforms", "--platform", "qiancheng"])

	assert result.exit_code == 0, result.output
	payload = json.loads(result.output)
	assert payload["data"]["count"] == 1
	assert payload["data"]["platforms"][0]["name"] == "qiancheng"
	assert payload["data"]["platforms"][0]["status"] == "placeholder"


def test_platforms_can_filter_single_platform_by_alias() -> None:
	runner = CliRunner()
	result = runner.invoke(cli, ["platforms", "--platform", "51job"])

	assert result.exit_code == 0, result.output
	payload = json.loads(result.output)
	assert payload["data"]["count"] == 1
	assert payload["data"]["platforms"][0]["name"] == "qiancheng"


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
	assert data["count"] == 3
	assert data["capability_filter"] == {
		"capability": "status",
		"status_groups": {
			"available": ["zhilian", "zhipin"],
			"placeholder": ["qiancheng"],
			"blocked_by_policy": [],
			"not_supported": [],
		},
	}
	platforms = {item["name"]: item for item in data["platforms"]}
	assert platforms["qiancheng"]["capability_match"] == {
		"capability": "status",
		"status": "placeholder",
		"raw_status": "placeholder_only",
	}


def test_platforms_can_filter_by_blocked_capability() -> None:
	runner = CliRunner()
	result = runner.invoke(cli, ["platforms", "--capability", "apply"])

	assert result.exit_code == 0, result.output
	payload = json.loads(result.output)
	assert payload["data"]["capability_filter"]["status_groups"] == {
		"available": [],
		"placeholder": [],
		"blocked_by_policy": ["zhilian", "zhipin"],
		"not_supported": ["qiancheng"],
	}


def test_platforms_capability_filter_combines_with_platform_filter() -> None:
	runner = CliRunner()
	result = runner.invoke(cli, ["platforms", "--platform", "51job", "--capability", "search"])

	assert result.exit_code == 0, result.output
	payload = json.loads(result.output)
	assert payload["data"]["count"] == 1
	assert payload["data"]["capability_filter"]["status_groups"] == {
		"available": [],
		"placeholder": [],
		"blocked_by_policy": [],
		"not_supported": ["qiancheng"],
	}
	assert payload["data"]["platforms"][0]["capability_match"]["status"] == "not_supported"


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
	assert "apply\tblocked_by_policy" in rendered
	assert "apply\tnot_supported" in rendered


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
	assert "available / placeholder / blocked_by_policy / not_supported" in capability_option["description"]
	assert "apply" in capability_option["choices"]
	assert "不触发登录" in platforms_schema["description"]
