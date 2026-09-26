"""验证 hr 子命令在不支持平台下抛 PLATFORM_NOT_SUPPORTED 错误码。"""
import json
from pathlib import Path

from click.testing import CliRunner

from boss_agent_cli.main import cli


def test_hr_on_candidate_only_platform_returns_platform_not_supported(tmp_path: Path, monkeypatch) -> None:
	"""已注册但没有招聘者适配器的平台，hr 子命令应返回 PLATFORM_NOT_SUPPORTED。"""
	import boss_agent_cli.platforms as platforms

	monkeypatch.setitem(platforms._REGISTRY, "candidate-only", platforms.BossPlatform)
	runner = CliRunner()
	result = runner.invoke(
		cli,
		["--data-dir", str(tmp_path), "--platform", "candidate-only", "hr", "applications"],
	)
	assert result.exit_code == 1
	envelope = json.loads(result.output)
	assert envelope["ok"] is False
	assert envelope["error"]["code"] == "PLATFORM_NOT_SUPPORTED"
	assert envelope["error"]["recoverable"] is True
	assert "boss --platform zhipin" in envelope["error"]["recovery_action"]


def test_hr_on_removed_zhilian_platform_returns_invalid_param(tmp_path: Path) -> None:
	"""智联平台已移除：--platform zhilian 在全局参数校验时即被拒绝。"""
	runner = CliRunner()
	result = runner.invoke(
		cli,
		["--data-dir", str(tmp_path), "--platform", "zhilian", "hr", "applications"],
	)
	assert result.exit_code == 1
	envelope = json.loads(result.output)
	assert envelope["ok"] is False
	assert envelope["error"]["code"] == "INVALID_PARAM"
	assert "unknown platform" in envelope["error"]["message"]
	assert "supported: zhipin" in envelope["error"]["message"]


def test_schema_includes_platform_not_supported_error_code(tmp_path: Path) -> None:
	"""boss schema 输出的错误码枚举应包含 PLATFORM_NOT_SUPPORTED。"""
	runner = CliRunner()
	result = runner.invoke(cli, ["--data-dir", str(tmp_path), "schema"])
	assert result.exit_code == 0
	envelope = json.loads(result.output)
	error_codes = envelope["data"].get("error_codes", {})
	assert "PLATFORM_NOT_SUPPORTED" in error_codes
	entry = error_codes["PLATFORM_NOT_SUPPORTED"]
	assert entry["recoverable"] is True
	assert entry["recovery_action"]
