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
		assert meta.get("current_platform") == "zhipin"

	def test_schema_current_platform_reflects_option(self, runner: CliRunner) -> None:
		from boss_agent_cli.main import cli

		result = runner.invoke(cli, ["--platform", "zhipin", "schema"])
		assert result.exit_code == 0
		payload = json.loads(result.output)
		assert payload["data"]["current_platform"] == "zhipin"

	def test_unknown_platform_exits_with_error(self, runner: CliRunner) -> None:
		from boss_agent_cli.main import cli

		result = runner.invoke(cli, ["--platform", "nonexistent", "schema"])
		assert result.exit_code != 0

	def test_schema_exposes_platform_option_in_global(self, runner: CliRunner) -> None:
		from boss_agent_cli.main import cli

		result = runner.invoke(cli, ["schema"])
		assert result.exit_code == 0
		payload = json.loads(result.output)
		global_opts = payload["data"]["global_options"]
		assert "--platform" in global_opts


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
