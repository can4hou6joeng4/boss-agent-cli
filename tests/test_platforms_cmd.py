"""本地平台能力清单命令测试。"""

from __future__ import annotations

import json

from click.testing import CliRunner

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

	platforms = {item["name"]: item for item in payload["data"]["platforms"]}
	assert set(platforms) == {"qiancheng", "zhipin", "zhilian"}
	assert platforms["qiancheng"]["status"] == "placeholder"
	assert platforms["qiancheng"]["capabilities"]["readonly"]["search"] == "not_supported"
	assert platforms["qiancheng"]["capabilities"]["readonly"]["status"] == "placeholder_only"
	assert "NOT_SUPPORTED" in platforms["qiancheng"]["notes"]
	assert platforms["zhipin"]["recruiter"] is True
	assert platforms["zhilian"]["capabilities"]["readonly"]["search"] == "available"


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


def test_platforms_is_listed_in_schema() -> None:
	runner = CliRunner()
	result = runner.invoke(cli, ["schema"])

	assert result.exit_code == 0, result.output
	payload = json.loads(result.output)
	platforms_schema = payload["data"]["commands"]["platforms"]
	assert platforms_schema["args"] == []
	assert "--platform" in platforms_schema["options"]
	assert platforms_schema["options"]["--platform"]["default"] is None
	assert "不触发登录" in platforms_schema["description"]
