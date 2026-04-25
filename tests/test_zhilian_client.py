"""ZhilianClient 契约测试。

Zhilian 内部 HTTP 客户端骨架，Week 2 真实现之前只提供类结构 / __init__ 签名 / 资源生命周期。
所有 API 调用方法抛 NotImplementedError 并附 Issue #140 链接。
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

import httpx
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


class TestZhilianClientHttpxClient:
	"""P0-1：`_get_httpx_client()` 基础认证头与生命周期（Issue #140）。"""

	def _make_auth(
		self,
		*,
		cookies: dict[str, str] | None = None,
		user_agent: str | None = None,
		client_id: str | None = None,
	) -> MagicMock:
		auth = MagicMock()
		token: dict[str, object] = {"cookies": cookies or {}}
		if user_agent is not None:
			token["user_agent"] = user_agent
		if client_id is not None:
			token["x_zp_client_id"] = client_id
		auth.get_token.return_value = token
		return auth

	def test_returns_httpx_client_instance(self) -> None:
		client = ZhilianClient(self._make_auth())
		http = client._get_httpx_client()
		assert isinstance(http, httpx.Client)

	def test_lazy_init_returns_same_instance(self) -> None:
		client = ZhilianClient(self._make_auth())
		first = client._get_httpx_client()
		second = client._get_httpx_client()
		assert first is second

	def test_cookies_come_from_token(self) -> None:
		auth = self._make_auth(cookies={"zp_token": "T", "x-zp-client-id": "ID"})
		client = ZhilianClient(auth)
		http = client._get_httpx_client()
		assert http.cookies.get("zp_token") == "T"
		assert http.cookies.get("x-zp-client-id") == "ID"

	def test_user_agent_from_token(self) -> None:
		auth = self._make_auth(user_agent="Mozilla/5.0 (TestUA) Chrome/123")
		client = ZhilianClient(auth)
		http = client._get_httpx_client()
		assert http.headers.get("User-Agent") == "Mozilla/5.0 (TestUA) Chrome/123"

	def test_default_user_agent_when_token_missing(self) -> None:
		client = ZhilianClient(self._make_auth())
		http = client._get_httpx_client()
		ua = http.headers.get("User-Agent") or ""
		assert "Mozilla" in ua  # 兜底有合理 UA

	def test_sec_ch_ua_platform_present(self) -> None:
		client = ZhilianClient(self._make_auth())
		http = client._get_httpx_client()
		platform_header = http.headers.get("sec-ch-ua-platform")
		assert platform_header is not None
		assert platform_header.startswith('"') and platform_header.endswith('"')
		expected_map = {"win32": '"Windows"', "linux": '"Linux"', "darwin": '"macOS"'}
		assert platform_header == expected_map.get(sys.platform, '"macOS"')

	def test_x_zp_client_id_header_when_token_provides(self) -> None:
		auth = self._make_auth(client_id="abc-123")
		client = ZhilianClient(auth)
		http = client._get_httpx_client()
		assert http.headers.get("x-zp-client-id") == "abc-123"

	def test_referer_and_origin_headers_zhaopin(self) -> None:
		client = ZhilianClient(self._make_auth())
		http = client._get_httpx_client()
		assert "zhaopin.com" in (http.headers.get("Referer") or "")
		assert "zhaopin.com" in (http.headers.get("Origin") or "")

	def test_follow_redirects_and_timeout(self) -> None:
		client = ZhilianClient(self._make_auth())
		http = client._get_httpx_client()
		assert http.follow_redirects is True
		# httpx Timeout 对象支持 read 属性
		assert http.timeout.read == 30

	def test_close_releases_httpx_client(self) -> None:
		client = ZhilianClient(self._make_auth())
		http = client._get_httpx_client()
		assert http.is_closed is False
		client.close()
		assert http.is_closed is True
		assert client._closed is True

	def test_close_is_idempotent_after_httpx_init(self) -> None:
		client = ZhilianClient(self._make_auth())
		client._get_httpx_client()
		client.close()
		client.close()  # 第二次不抛错

	def test_context_manager_closes_httpx_client(self) -> None:
		auth = self._make_auth()
		with ZhilianClient(auth) as client:
			http = client._get_httpx_client()
			assert http.is_closed is False
		assert http.is_closed is True


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
