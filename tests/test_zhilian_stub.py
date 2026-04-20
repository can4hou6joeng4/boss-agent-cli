"""ZhilianPlatform stub 契约测试。

Zhilian 在 Week 1d 以 stub 形态接入 Platform 注册表，P0/P1/P2 方法暂抛 NotImplementedError，
但 is_success / unwrap_data / parse_error 按智联真实协议返回正确值，
验证 Platform 抽象设计对第二平台也自洽（Issue #129 Week 1d 自证）。

Week 2 会把 stub 替换为真实实现。
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner

from boss_agent_cli.platforms import BossPlatform, Platform, get_platform, list_platforms
from boss_agent_cli.platforms.zhilian import ZhilianPlatform


class TestZhilianRegistration:
	"""Zhilian 已注册到 Platform 注册表。"""

	def test_list_platforms_contains_zhilian(self) -> None:
		assert "zhilian" in list_platforms()

	def test_list_platforms_still_contains_zhipin(self) -> None:
		assert "zhipin" in list_platforms()

	def test_get_platform_returns_zhilian_class(self) -> None:
		assert get_platform("zhilian") is ZhilianPlatform

	def test_zhilian_subclasses_platform(self) -> None:
		assert issubclass(ZhilianPlatform, Platform)

	def test_zhilian_is_distinct_from_boss(self) -> None:
		assert ZhilianPlatform is not BossPlatform


class TestZhilianMetadata:
	"""Zhilian 基础元信息（对齐 docs/research/platforms/zhaopin.md）。"""

	def setup_method(self) -> None:
		self.plat = ZhilianPlatform(MagicMock())

	def test_name_is_zhilian(self) -> None:
		assert self.plat.name == "zhilian"

	def test_display_name_is_chinese(self) -> None:
		assert self.plat.display_name == "智联招聘"

	def test_base_url_points_to_zhaopin(self) -> None:
		assert "zhaopin.com" in self.plat.base_url


class TestZhilianEnvelopeAdapter:
	"""Zhilian 响应包络适配（基于 zhaopin.md §4）。"""

	def setup_method(self) -> None:
		self.plat = ZhilianPlatform(MagicMock())

	def test_is_success_code_200(self) -> None:
		"""智联成功是 code == 200，区别于 BOSS 的 code == 0。"""
		assert self.plat.is_success({"code": 200, "data": {}}) is True

	def test_is_success_non_200(self) -> None:
		assert self.plat.is_success({"code": 401}) is False

	def test_is_success_missing_code(self) -> None:
		assert self.plat.is_success({}) is False

	def test_unwrap_data_from_data_key(self) -> None:
		"""智联数据在 data key，区别于 BOSS 的 zpData。"""
		result = self.plat.unwrap_data({"code": 200, "data": {"list": [1, 2]}})
		assert result == {"list": [1, 2]}

	def test_unwrap_data_missing(self) -> None:
		assert self.plat.unwrap_data({"code": 200}) is None

	def test_parse_error_unauthorized(self) -> None:
		"""智联 401 → AUTH_EXPIRED / AUTH_REQUIRED。"""
		code, _ = self.plat.parse_error({"code": 401, "message": "unauthorized"})
		assert code in ("AUTH_EXPIRED", "AUTH_REQUIRED")

	def test_parse_error_forbidden_as_risk(self) -> None:
		"""智联 403 → ACCOUNT_RISK。"""
		code, _ = self.plat.parse_error({"code": 403, "message": "forbidden"})
		assert code == "ACCOUNT_RISK"

	def test_parse_error_rate_limited(self) -> None:
		"""智联 429 → RATE_LIMITED。"""
		code, _ = self.plat.parse_error({"code": 429, "message": "too many"})
		assert code == "RATE_LIMITED"

	def test_parse_error_unknown(self) -> None:
		code, _ = self.plat.parse_error({"code": 999, "message": "whatever"})
		assert code == "UNKNOWN"


class TestZhilianStubBehavior:
	"""Stub 方法在 Week 2 之前应抛 NotImplementedError 附友好提示。"""

	def setup_method(self) -> None:
		self.plat = ZhilianPlatform(MagicMock())

	def test_search_jobs_not_implemented(self) -> None:
		with pytest.raises(NotImplementedError, match="Week 2"):
			self.plat.search_jobs("Python")

	def test_job_detail_not_implemented(self) -> None:
		with pytest.raises(NotImplementedError, match="Week 2"):
			self.plat.job_detail("abc")

	def test_recommend_jobs_not_implemented(self) -> None:
		with pytest.raises(NotImplementedError, match="Week 2"):
			self.plat.recommend_jobs()

	def test_user_info_not_implemented(self) -> None:
		with pytest.raises(NotImplementedError, match="Week 2"):
			self.plat.user_info()

	def test_greet_not_implemented_defaults_from_base(self) -> None:
		"""Platform 基类 greet 抛 NotImplementedError，Zhilian stub 沿用。"""
		with pytest.raises(NotImplementedError):
			self.plat.greet("sid", "jid")

	def test_apply_not_implemented_defaults_from_base(self) -> None:
		with pytest.raises(NotImplementedError):
			self.plat.apply("sid", "jid")

	def test_stub_can_enter_with_context(self) -> None:
		"""with 上下文即使 P0 方法抛错也能正常关闭（客户端是 MagicMock）。"""
		mock_client = MagicMock()
		plat = ZhilianPlatform(mock_client)
		with plat:
			pass
		mock_client.close.assert_called_once()


class TestZhilianCliIntegration:
	"""CLI 集成：--platform zhilian schema 可用且暴露正确字段。"""

	def test_zhilian_accepted_as_platform_option(self) -> None:
		from boss_agent_cli.main import cli

		runner = CliRunner()
		result = runner.invoke(cli, ["--platform", "zhilian", "schema"])
		assert result.exit_code == 0
		payload = json.loads(result.output)
		assert payload["data"]["current_platform"] == "zhilian"

	def test_schema_supported_platforms_includes_zhilian(self) -> None:
		from boss_agent_cli.main import cli

		runner = CliRunner()
		result = runner.invoke(cli, ["schema"])
		assert result.exit_code == 0
		payload = json.loads(result.output)
		assert "zhilian" in payload["data"]["supported_platforms"]

	def test_schema_platform_choice_updated(self) -> None:
		"""schema 的 --platform 选项 choices 应包含 zhilian。"""
		from boss_agent_cli.main import cli

		runner = CliRunner()
		result = runner.invoke(cli, ["schema"])
		assert result.exit_code == 0
		payload = json.loads(result.output)
		platform_opt = payload["data"]["global_options"]["--platform"]
		assert "zhilian" in platform_opt.get("choices", [])
