"""ZhilianClient 契约测试。

Zhilian 内部 HTTP 客户端骨架，Week 2 真实现之前只提供类结构 / __init__ 签名 / 资源生命周期。
所有 API 调用方法抛 NotImplementedError 并附 Issue #140 链接。
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from boss_agent_cli.api.zhilian_client import ZhilianClient


class TestZhilianClientStructure:
	"""ZhilianClient 类结构对齐 BossClient 的公开面。"""

	def test_can_instantiate_with_auth_manager(self) -> None:
		client = ZhilianClient(MagicMock())
		assert client is not None

	def test_init_accepts_delay_keyword(self) -> None:
		client = ZhilianClient(MagicMock(), delay=(2.0, 4.0))
		assert client._delay == (2.0, 4.0)

	def test_init_accepts_cdp_url_keyword(self) -> None:
		client = ZhilianClient(MagicMock(), cdp_url="http://localhost:9222")
		assert client._cdp_url == "http://localhost:9222"

	def test_close_is_callable_and_idempotent(self) -> None:
		client = ZhilianClient(MagicMock())
		client.close()
		client.close()  # 重复调用不抛错


class TestZhilianClientContextManager:
	"""with 上下文管理器支持。"""

	def test_enter_returns_self(self) -> None:
		client = ZhilianClient(MagicMock())
		with client as c:
			assert c is client

	def test_exit_calls_close(self) -> None:
		client = ZhilianClient(MagicMock())
		with client:
			assert not client._closed
		assert client._closed


class TestZhilianClientStubMethods:
	"""Week 2 待实现方法抛清晰 NotImplementedError。"""

	def setup_method(self) -> None:
		self.client = ZhilianClient(MagicMock())

	def test_search_jobs_raises(self) -> None:
		with pytest.raises(NotImplementedError, match="Week 2"):
			self.client.search_jobs("Python")

	def test_job_detail_raises(self) -> None:
		with pytest.raises(NotImplementedError, match="Week 2"):
			self.client.job_detail("abc")

	def test_recommend_jobs_raises(self) -> None:
		with pytest.raises(NotImplementedError, match="Week 2"):
			self.client.recommend_jobs()

	def test_user_info_raises(self) -> None:
		with pytest.raises(NotImplementedError, match="Week 2"):
			self.client.user_info()

	def test_error_messages_point_to_issue_140(self) -> None:
		with pytest.raises(NotImplementedError) as exc_info:
			self.client.user_info()
		assert "#140" in str(exc_info.value)


class TestPlatformInstanceRoutesToClient:
	"""get_platform_instance 按 platform 分发到正确的 client 类。"""

	def test_zhipin_platform_gets_boss_client(self) -> None:
		from unittest.mock import patch
		from boss_agent_cli.commands._platform import get_platform_instance
		from boss_agent_cli.platforms import BossPlatform

		ctx = MagicMock()
		ctx.obj = {"platform": "zhipin", "delay": (1.0, 2.0), "cdp_url": None}
		auth = MagicMock()

		with patch("boss_agent_cli.commands._platform.BossClient") as mock_boss_cls:
			plat = get_platform_instance(ctx, auth)
			assert isinstance(plat, BossPlatform)
			mock_boss_cls.assert_called_once()

	def test_zhilian_platform_gets_zhilian_client(self) -> None:
		from unittest.mock import patch
		from boss_agent_cli.commands._platform import get_platform_instance
		from boss_agent_cli.platforms import ZhilianPlatform

		ctx = MagicMock()
		ctx.obj = {"platform": "zhilian", "delay": (1.0, 2.0), "cdp_url": None}
		auth = MagicMock()

		with patch("boss_agent_cli.commands._platform.ZhilianClient") as mock_zhilian_cls:
			plat = get_platform_instance(ctx, auth)
			assert isinstance(plat, ZhilianPlatform)
			mock_zhilian_cls.assert_called_once()
