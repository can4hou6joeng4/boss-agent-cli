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
		assert "supported_platforms" in meta
		assert "zhipin" in meta["supported_platforms"]
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
		assert "zhipin" in search_availability["candidate_platforms"]
		assert "zhilian" in search_availability["candidate_platforms"]
		assert search_availability["recruiter_platforms"] == []

		hr_availability = commands["hr"]["availability"]
		assert hr_availability["roles"] == ["recruiter"]
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

	def test_schema_exposes_platform_option_in_global(self, runner: CliRunner) -> None:
		from boss_agent_cli.main import cli

		result = runner.invoke(cli, ["schema"])
		assert result.exit_code == 0
		payload = json.loads(result.output)
		global_opts = payload["data"]["global_options"]
		assert "--platform" in global_opts

	def test_openai_tools_description_includes_availability(self, runner: CliRunner) -> None:
		from boss_agent_cli.main import cli

		result = runner.invoke(cli, ["schema", "--format", "openai-tools"])
		assert result.exit_code == 0
		payload = json.loads(result.output)
		tool = next(t for t in payload["data"]["tools"] if t["function"]["name"] == "boss_search")
		assert "candidate_platforms=" in tool["function"]["description"]
		assert "zhilian" in tool["function"]["description"]
		assert "zhipin" in tool["function"]["description"]

	def test_schema_login_description_mentions_platform_aware_flow(self, runner: CliRunner) -> None:
		from boss_agent_cli.main import cli

		result = runner.invoke(cli, ["schema"])
		assert result.exit_code == 0
		payload = json.loads(result.output)
		login_desc = payload["data"]["commands"]["login"]["description"]
		assert "当前平台" in login_desc
		assert "低风险模式" in login_desc
		assert "zhilian" in login_desc


class TestGetPlatformInstanceHelper:
	"""get_platform_instance(ctx, auth) helper。"""

	def test_helper_returns_boss_platform_by_default(self) -> None:
		from boss_agent_cli.platforms import BossPlatform
		from boss_agent_cli.commands._platform import get_platform_instance

		ctx = MagicMock()
		ctx.obj = {"platform": "zhipin", "data_dir": "/tmp/fake", "delay": (0.0, 0.0), "cdp_url": None}
		auth = MagicMock()

		with patch("boss_agent_cli.commands._platform.BossClient") as mock_client_cls:
			plat = get_platform_instance(ctx, auth)
			assert isinstance(plat, BossPlatform)
			mock_client_cls.assert_called_once()

	def test_helper_passes_delay_and_cdp_to_client(self) -> None:
		from boss_agent_cli.commands._platform import get_platform_instance

		ctx = MagicMock()
		ctx.obj = {"platform": "zhipin", "delay": (2.0, 4.0), "cdp_url": "http://localhost:9222"}
		auth = MagicMock()

		with patch("boss_agent_cli.commands._platform.BossClient") as mock_client_cls:
			get_platform_instance(ctx, auth)
			mock_client_cls.assert_called_once_with(auth, delay=(2.0, 4.0), cdp_url="http://localhost:9222")

	def test_helper_defaults_missing_platform_to_zhipin(self) -> None:
		from boss_agent_cli.platforms import BossPlatform
		from boss_agent_cli.commands._platform import get_platform_instance

		ctx = MagicMock()
		ctx.obj = {"delay": (0.0, 0.0)}
		auth = MagicMock()

		with patch("boss_agent_cli.commands._platform.BossClient"):
			plat = get_platform_instance(ctx, auth)
			assert isinstance(plat, BossPlatform)

	def test_helper_raises_on_unknown_platform(self) -> None:
		from boss_agent_cli.commands._platform import get_platform_instance

		ctx = MagicMock()
		ctx.obj = {"platform": "unknown", "delay": (0.0, 0.0)}
		auth = MagicMock()

		with pytest.raises(ValueError, match="unknown platform"):
			get_platform_instance(ctx, auth)

	def test_helper_returns_qiancheng_placeholder_without_network_client(self) -> None:
		"""51job 占位适配器应可通过全局 --platform 被实例化，且不构造真实 client。"""
		from boss_agent_cli.commands._platform import get_platform_instance
		from boss_agent_cli.platforms import QianchengPlatform

		ctx = MagicMock()
		ctx.obj = {"platform": "qiancheng", "delay": (0.0, 0.0), "cdp_url": None}
		auth = MagicMock()

		with patch("boss_agent_cli.commands._platform.BossClient") as mock_boss_client_cls:
			plat = get_platform_instance(ctx, auth)

		assert isinstance(plat, QianchengPlatform)
		mock_boss_client_cls.assert_not_called()
		assert plat.client is None


class TestQianchengPlaceholderContract:
	"""51job 占位平台必须保持可发现、只读安全且统一 NOT_SUPPORTED。"""

	def test_registry_exposes_qiancheng_aliases(self) -> None:
		from boss_agent_cli.platforms import QianchengPlatform, get_platform, list_platforms

		assert get_platform("qiancheng") is QianchengPlatform
		assert get_platform("51job") is QianchengPlatform
		assert "qiancheng" in list_platforms()
		assert "51job" in list_platforms()

	def test_schema_exposes_qiancheng_as_candidate_placeholder(self, runner: CliRunner) -> None:
		from boss_agent_cli.main import cli

		result = runner.invoke(cli, ["--platform", "qiancheng", "schema"])
		assert result.exit_code == 0
		payload = json.loads(result.output)
		meta = payload["data"]

		assert meta["current_platform"] == "qiancheng"
		assert "qiancheng" in meta["supported_platforms"]
		assert "qiancheng" in meta["commands"]["search"]["availability"]["candidate_platforms"]
		assert "51job" in meta["commands"]["search"]["availability"]["note"]

	def test_qiancheng_methods_return_stable_not_supported_envelope(self) -> None:
		from boss_agent_cli.platforms import QianchengPlatform

		plat = QianchengPlatform()
		for method_name, args in [
			("search_jobs", ("python",)),
			("job_detail", ("security-id",)),
			("recommend_jobs", ()),
			("user_info", ()),
			("job_card", ("security-id",)),
		]:
			result = getattr(plat, method_name)(*args)
			assert result["code"] == -1
			assert result["error"]["code"] == "NOT_SUPPORTED"
			assert result["error"]["recoverable"] is True
			assert result["error"]["details"]["platform"] == "qiancheng"
			assert result["error"]["details"]["capability"] == method_name
			assert "51job" in result["error"]["message"]
			assert "research backlog" in result["error"]["message"]

	def test_qiancheng_envelope_helpers_parse_not_supported(self) -> None:
		from boss_agent_cli.platforms import QianchengPlatform

		plat = QianchengPlatform()
		result = plat.search_jobs("python")

		assert plat.is_success(result) is False
		assert plat.unwrap_data(result) is None
		assert plat.parse_error(result) == ("NOT_SUPPORTED", result["error"]["message"])


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
