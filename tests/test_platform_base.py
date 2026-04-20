"""Platform 抽象基类契约测试。

覆盖：
- Platform ABC 不能实例化
- BossPlatform 满足接口契约
- 包络适配方法（is_success / unwrap_data / parse_error）语义正确
- 委托方法正确传递给 BossClient
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from boss_agent_cli.platforms.base import Platform
from boss_agent_cli.platforms.zhipin import BossPlatform


class TestPlatformABC:
	"""Platform 抽象基类契约。"""

	def test_cannot_instantiate_abstract_base(self) -> None:
		with pytest.raises(TypeError):
			Platform()  # type: ignore[abstract]

	def test_boss_platform_is_subclass(self) -> None:
		assert issubclass(BossPlatform, Platform)

	def test_boss_platform_metadata(self) -> None:
		plat = BossPlatform(MagicMock())
		assert plat.name == "zhipin"
		assert plat.display_name == "BOSS 直聘"
		assert "zhipin.com" in plat.base_url


class TestBossEnvelopeAdapter:
	"""BOSS 直聘响应包络适配。"""

	def setup_method(self) -> None:
		self.plat = BossPlatform(MagicMock())

	def test_is_success_code_zero(self) -> None:
		assert self.plat.is_success({"code": 0, "zpData": {}}) is True

	def test_is_success_non_zero(self) -> None:
		assert self.plat.is_success({"code": 9}) is False

	def test_is_success_missing_code(self) -> None:
		assert self.plat.is_success({}) is False

	def test_unwrap_data_from_zpdata(self) -> None:
		result = self.plat.unwrap_data({"code": 0, "zpData": {"jobList": [1, 2]}})
		assert result == {"jobList": [1, 2]}

	def test_unwrap_data_missing_zpdata(self) -> None:
		assert self.plat.unwrap_data({"code": 0}) is None

	def test_parse_error_stoken_expired(self) -> None:
		code, msg = self.plat.parse_error({"code": 37, "message": "stoken invalid"})
		assert code == "TOKEN_REFRESH_FAILED"
		assert "stoken" in msg.lower() or msg

	def test_parse_error_rate_limited(self) -> None:
		code, _ = self.plat.parse_error({"code": 9, "message": "请求频繁"})
		assert code == "RATE_LIMITED"

	def test_parse_error_account_risk(self) -> None:
		code, _ = self.plat.parse_error({"code": 36, "message": "risk"})
		assert code == "ACCOUNT_RISK"

	def test_parse_error_unknown(self) -> None:
		code, _ = self.plat.parse_error({"code": 999, "message": "unknown"})
		assert code == "UNKNOWN"


class TestBossPlatformDelegation:
	"""BossPlatform 委托给底层 BossClient。"""

	def setup_method(self) -> None:
		self.mock_client = MagicMock()
		self.plat = BossPlatform(self.mock_client)

	def test_search_jobs_delegates(self) -> None:
		self.mock_client.search_jobs.return_value = {"code": 0, "zpData": {}}
		result = self.plat.search_jobs("Python", city="广州")
		self.mock_client.search_jobs.assert_called_once_with("Python", city="广州")
		assert result == {"code": 0, "zpData": {}}

	def test_job_detail_delegates(self) -> None:
		self.mock_client.job_detail.return_value = {"code": 0}
		self.plat.job_detail("job_123")
		self.mock_client.job_detail.assert_called_once_with("job_123")

	def test_recommend_jobs_delegates(self) -> None:
		self.mock_client.recommend_jobs.return_value = {"code": 0}
		self.plat.recommend_jobs(page=2)
		self.mock_client.recommend_jobs.assert_called_once_with(2)

	def test_user_info_delegates(self) -> None:
		self.mock_client.user_info.return_value = {"code": 0}
		self.plat.user_info()
		self.mock_client.user_info.assert_called_once_with()

	def test_greet_delegates(self) -> None:
		self.mock_client.greet.return_value = {"code": 0}
		self.plat.greet("sec_abc", "job_123", message="hi")
		self.mock_client.greet.assert_called_once_with("sec_abc", "job_123", "hi")

	def test_apply_delegates(self) -> None:
		self.mock_client.apply.return_value = {"code": 0}
		self.plat.apply("sec_abc", "job_123", lid="lid_x")
		self.mock_client.apply.assert_called_once_with("sec_abc", "job_123", "lid_x")

	def test_friend_list_delegates(self) -> None:
		self.mock_client.friend_list.return_value = {"code": 0}
		self.plat.friend_list(page=3)
		self.mock_client.friend_list.assert_called_once_with(3)


class TestPlatformRegistry:
	"""Platform 注册表：通过 name 查找实现。"""

	def test_get_platform_zhipin(self) -> None:
		from boss_agent_cli.platforms import get_platform

		plat_cls = get_platform("zhipin")
		assert plat_cls is BossPlatform

	def test_get_platform_default_zhipin(self) -> None:
		"""向后兼容：不传参数等同 zhipin。"""
		from boss_agent_cli.platforms import get_platform

		plat_cls = get_platform()
		assert plat_cls is BossPlatform

	def test_get_platform_unknown_raises(self) -> None:
		from boss_agent_cli.platforms import get_platform

		with pytest.raises(ValueError, match="unknown platform"):
			get_platform("nonexistent")

	def test_list_platforms(self) -> None:
		from boss_agent_cli.platforms import list_platforms

		names = list_platforms()
		assert "zhipin" in names


class TestPlatformInterfaceCompleteness:
	"""确保新实现不会漏实现抽象方法。"""

	def test_boss_platform_implements_all_p0_methods(self) -> None:
		plat = BossPlatform(MagicMock())
		assert callable(plat.search_jobs)
		assert callable(plat.job_detail)
		assert callable(plat.recommend_jobs)
		assert callable(plat.user_info)

	def test_boss_platform_implements_envelope_methods(self) -> None:
		plat = BossPlatform(MagicMock())
		assert callable(plat.is_success)
		assert callable(plat.unwrap_data)
		assert callable(plat.parse_error)


class TestPlatformContextManager:
	"""Platform 作为 with 上下文管理器释放底层资源。"""

	def test_platform_supports_with_statement(self) -> None:
		client = MagicMock()
		plat = BossPlatform(client)
		with plat as p:
			assert p is plat
		client.close.assert_called_once()

	def test_close_calls_client_close(self) -> None:
		client = MagicMock()
		plat = BossPlatform(client)
		plat.close()
		client.close.assert_called_once()

	def test_close_tolerates_missing_close(self) -> None:
		"""底层 client 没有 close 方法时不抛错。"""
		client_no_close = object()
		plat = BossPlatform(client_no_close)  # type: ignore[arg-type]
		plat.close()  # 不抛错

	def test_exit_closes_on_exception(self) -> None:
		"""with 块内抛异常时仍然关闭底层资源。"""
		client = MagicMock()
		plat = BossPlatform(client)
		with pytest.raises(RuntimeError):
			with plat:
				raise RuntimeError("boom")
		client.close.assert_called_once()

	def test_enter_returns_self(self) -> None:
		client = MagicMock()
		plat = BossPlatform(client)
		entered = plat.__enter__()
		assert entered is plat
