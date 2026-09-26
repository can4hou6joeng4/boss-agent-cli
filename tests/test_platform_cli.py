"""CLI `--platform` 全局选项与 Platform 辅助函数测试。"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner


@pytest.fixture
def runner() -> CliRunner:
	return CliRunner()


class TestPlatformGlobalOption:
	"""main.py 新增 --platform 全局选项。"""

	def test_schema_exposes_supported_platforms(self, runner: CliRunner) -> None:
		from boss_agent_cli.main import cli

		result = runner.invoke(cli, ["schema"])
		assert result.exit_code == 0
		payload = json.loads(result.output)
		meta = payload["data"]
		assert meta["supported_platforms"] == ["zhipin"]
		assert "supported_recruiter_platforms" in meta
		assert "zhipin-recruiter" in meta["supported_recruiter_platforms"]
		assert meta.get("current_platform") == "zhipin"

	def test_schema_exposes_command_availability(self, runner: CliRunner) -> None:
		from boss_agent_cli.main import cli

		result = runner.invoke(cli, ["schema"])
		assert result.exit_code == 0
		payload = json.loads(result.output)
		commands = payload["data"]["commands"]
		search_availability = commands["search"]["availability"]
		assert search_availability["roles"] == ["candidate"]
		assert search_availability["candidate_platforms"] == ["zhipin"]
		assert search_availability["recruiter_platforms"] == []

		hr_availability = commands["hr"]["availability"]
		assert hr_availability["roles"] == ["recruiter"]
		assert hr_availability["candidate_platforms"] == []
		assert "zhipin-recruiter" in hr_availability["recruiter_platforms"]
		assert "applications" in hr_availability["subcommands"]

	def test_schema_current_platform_reflects_option(self, runner: CliRunner) -> None:
		from boss_agent_cli.main import cli

		result = runner.invoke(cli, ["--platform", "zhipin", "schema"])
		assert result.exit_code == 0
		payload = json.loads(result.output)
		assert payload["data"]["current_platform"] == "zhipin"

	def test_unknown_platform_exits_with_error(self, runner: CliRunner) -> None:
		from boss_agent_cli.main import cli

		result = runner.invoke(cli, ["--platform", "nonexistent", "schema"])
		assert result.exit_code == 1
		payload = json.loads(result.output)
		assert payload["ok"] is False
		assert payload["command"] == "boss"
		assert payload["error"]["code"] == "INVALID_PARAM"
		assert payload["error"]["recoverable"] is False
		assert payload["error"]["recovery_action"] == "修正参数"
		assert result.stderr == ""

	@pytest.mark.parametrize("platform_name", ["qiancheng", "51job", "zhilian"])
	def test_removed_platform_exits_with_invalid_param(self, runner: CliRunner, platform_name: str) -> None:
		from boss_agent_cli.main import cli

		result = runner.invoke(cli, ["--platform", platform_name, "schema"])
		assert result.exit_code == 1
		payload = json.loads(result.output)
		assert set(payload) == {"ok", "schema_version", "command", "data", "pagination", "error", "hints"}
		assert payload["ok"] is False
		assert payload["command"] == "boss"
		assert payload["error"]["code"] == "INVALID_PARAM"
		assert payload["error"]["recoverable"] is False
		assert payload["error"]["recovery_action"] == "修正参数"
		assert "supported: zhipin" in payload["error"]["message"]

	def test_schema_exposes_platform_option_in_global(self, runner: CliRunner) -> None:
		from boss_agent_cli.main import cli

		result = runner.invoke(cli, ["schema"])
		assert result.exit_code == 0
		payload = json.loads(result.output)
		global_opts = payload["data"]["global_options"]
		assert "--platform" in global_opts
		assert global_opts["--platform"]["choices"] == ["zhipin"]
		assert "zhilian" not in global_opts["--platform"]["description"]

	def test_openai_tools_description_includes_availability(self, runner: CliRunner) -> None:
		from boss_agent_cli.main import cli

		result = runner.invoke(cli, ["schema", "--format", "openai-tools"])
		assert result.exit_code == 0
		payload = json.loads(result.output)
		tool = next(t for t in payload["data"]["tools"] if t["function"]["name"] == "boss_search")
		assert "candidate_platforms=" in tool["function"]["description"]
		assert "zhilian" not in tool["function"]["description"]
		assert "zhipin" in tool["function"]["description"]

	def test_schema_login_description_mentions_platform_aware_flow(self, runner: CliRunner) -> None:
		from boss_agent_cli.main import cli

		result = runner.invoke(cli, ["schema"])
		assert result.exit_code == 0
		payload = json.loads(result.output)
		login_desc = payload["data"]["commands"]["login"]["description"]
		assert "当前平台" in login_desc
		assert "两种兼容运行模式共享相同能力" in login_desc
		assert "zhilian" not in login_desc

	def test_stale_config_platform_is_rejected_but_cli_flag_recovers(self, runner: CliRunner, tmp_path) -> None:
		"""config.json 里残留已移除的平台时整条命令被拒；显式 --platform zhipin 可以把配置改回来。"""
		from boss_agent_cli.main import cli

		(tmp_path / "config.json").write_text(json.dumps({"platform": "zhilian"}), encoding="utf-8")

		rejected = runner.invoke(cli, ["--data-dir", str(tmp_path), "schema"])
		assert rejected.exit_code == 1
		payload = json.loads(rejected.output)
		assert payload["error"]["code"] == "INVALID_PARAM"
		assert "supported: zhipin" in payload["error"]["message"]

		repaired = runner.invoke(cli, ["--data-dir", str(tmp_path), "--platform", "zhipin", "config", "set", "platform", "zhipin"])
		assert repaired.exit_code == 0, repaired.output

		recovered = runner.invoke(cli, ["--data-dir", str(tmp_path), "schema"])
		assert recovered.exit_code == 0, recovered.output
		assert json.loads(recovered.output)["data"]["current_platform"] == "zhipin"


class TestGetPlatformInstanceHelper:
	"""get_platform_instance(ctx, auth) helper。"""

	def test_helper_returns_boss_platform_by_default(self) -> None:
		from boss_agent_cli.platforms import BossPlatform
		from boss_agent_cli.commands._platform import get_platform_instance

		ctx = MagicMock()
		ctx.obj = {"platform": "zhipin", "data_dir": "/tmp/fake", "delay": (0.0, 0.0), "cdp_url": None}
		auth = MagicMock()

		with patch("boss_agent_cli.platforms.factory.BossClient") as mock_client_cls:
			plat = get_platform_instance(ctx, auth)
			assert isinstance(plat, BossPlatform)
			mock_client_cls.assert_called_once()

	def test_helper_passes_delay_and_cdp_to_client(self) -> None:
		from boss_agent_cli.commands._platform import get_platform_instance

		ctx = MagicMock()
		ctx.obj = {"platform": "zhipin", "delay": (2.0, 4.0), "cdp_url": "http://localhost:9222"}
		auth = MagicMock()

		with patch("boss_agent_cli.platforms.factory.BossClient") as mock_client_cls:
			get_platform_instance(ctx, auth)
			mock_client_cls.assert_called_once_with(
				auth, delay=(2.0, 4.0), cdp_url="http://localhost:9222", browser_source="auto"
			)

	def test_helper_defaults_missing_platform_to_zhipin(self) -> None:
		from boss_agent_cli.platforms import BossPlatform
		from boss_agent_cli.commands._platform import get_platform_instance

		ctx = MagicMock()
		ctx.obj = {"delay": (0.0, 0.0)}
		auth = MagicMock()

		with patch("boss_agent_cli.platforms.factory.BossClient"):
			plat = get_platform_instance(ctx, auth)
			assert isinstance(plat, BossPlatform)

	def test_helper_raises_on_unknown_platform(self) -> None:
		from boss_agent_cli.commands._platform import get_platform_instance

		ctx = MagicMock()
		ctx.obj = {"platform": "unknown", "delay": (0.0, 0.0)}
		auth = MagicMock()

		with pytest.raises(ValueError, match="unknown platform"):
			get_platform_instance(ctx, auth)

	def test_helper_passes_browser_source_to_client(self) -> None:
		"""非默认来源必须显式透传给 BossClient。"""
		from boss_agent_cli.commands._platform import get_platform_instance

		ctx = MagicMock()
		ctx.obj = {"platform": "zhipin", "delay": (0.0, 0.0), "cdp_url": None, "browser_source": "stored-cookie"}
		auth = MagicMock()

		with patch("boss_agent_cli.platforms.factory.BossClient") as mock_client_cls:
			get_platform_instance(ctx, auth)
			mock_client_cls.assert_called_once_with(
				auth, delay=(0.0, 0.0), cdp_url=None, browser_source="stored-cookie"
			)

	def test_helper_rejects_fail_closed_source_on_non_zhipin_platform(self, monkeypatch: pytest.MonkeyPatch) -> None:
		"""非 zhipin 平台没有浏览器通道：非 auto 来源必须抛 BrowserSourceUnsupported（→ NOT_SUPPORTED）。"""
		import boss_agent_cli.platforms as platforms
		from boss_agent_cli.api.browser_source import BrowserSourceUnsupported
		from boss_agent_cli.commands._platform import get_platform_instance

		monkeypatch.setitem(platforms._REGISTRY, "nobrowser", platforms.BossPlatform)
		ctx = MagicMock()
		ctx.obj = {"platform": "nobrowser", "delay": (0.0, 0.0), "cdp_url": None, "browser_source": "stored-cookie"}
		auth = MagicMock()

		with patch("boss_agent_cli.platforms.factory.BossClient") as mock_client_cls:
			with pytest.raises(BrowserSourceUnsupported):
				get_platform_instance(ctx, auth)
			mock_client_cls.assert_not_called()


class TestConfigPlatformDefault:
	"""config.json 新增 platform 字段默认值。"""

	def test_defaults_has_platform_zhipin(self) -> None:
		from boss_agent_cli.config import DEFAULTS

		assert DEFAULTS.get("platform") == "zhipin"

	def test_load_config_honors_user_platform(self, tmp_path: Any) -> None:
		import json as _json

		from boss_agent_cli.config import load_config

		cfg_path = tmp_path / "config.json"
		cfg_path.write_text(_json.dumps({"platform": "zhipin"}))
		cfg = load_config(cfg_path)
		assert cfg["platform"] == "zhipin"
