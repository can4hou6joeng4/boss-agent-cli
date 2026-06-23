"""ZhilianClient P0 契约测试。"""

from __future__ import annotations

from unittest.mock import MagicMock

from boss_agent_cli.api.zhilian_client import (
	APPLY_URL,
	CSRF_BOOTSTRAP_URL,
	DETAIL_URL_TEMPLATE,
	DELIVER_LIST_URL,
	GREET_URL,
	INTERVIEW_DATA_URL,
	JOB_CARD_URL_TEMPLATE,
	JOB_HISTORY_URL,
	RECOMMEND_URL,
	RESUME_BASEINFO_URL,
	RESUME_EXPECT_URL,
	SEARCH_URL,
	USER_INFO_URL,
	ZhilianClient,
	_extract_csrf_token,
)


class _StubAuth:
	def __init__(self) -> None:
		self.token = {
			"cookies": {"zp_token": "token"},
			"user_agent": "UA",
			"x_zp_client_id": "client-id-1",
		}

	def get_token(self):
		return self.token

	def force_refresh(self, cdp_url=None):
		self.token = {**self.token, "zp_token": "refreshed"}


class TestZhilianClientStructure:
	"""ZhilianClient 类结构对齐 BossClient 的公开面。"""

	def test_can_instantiate_with_auth_manager(self) -> None:
		client = ZhilianClient(_StubAuth())
		assert client is not None

	def test_init_accepts_delay_keyword(self) -> None:
		client = ZhilianClient(_StubAuth(), delay=(2.0, 4.0))
		assert client._delay == (2.0, 4.0)

	def test_init_accepts_cdp_url_keyword(self) -> None:
		client = ZhilianClient(_StubAuth(), cdp_url="http://localhost:9222")
		assert client._cdp_url == "http://localhost:9222"

	def test_close_is_callable_and_idempotent(self) -> None:
		client = ZhilianClient(_StubAuth())
		client.close()
		client.close()  # 重复调用不抛错

	def test_get_client_applies_auth_headers(self) -> None:
		client = ZhilianClient(_StubAuth())
		httpx_client = client._get_client()
		assert httpx_client.headers["User-Agent"] == "UA"
		assert httpx_client.headers["x-zp-client-id"] == "client-id-1"
		assert httpx_client.cookies.get("zp_token") == "token"


class TestZhilianClientContextManager:
	"""with 上下文管理器支持。"""

	def test_enter_returns_self(self) -> None:
		client = ZhilianClient(_StubAuth())
		with client as c:
			assert c is client

	def test_exit_calls_close(self) -> None:
		client = ZhilianClient(_StubAuth())
		with client:
			assert not client._closed
		assert client._closed

	def test_close_releases_httpx_client_and_unregisters(self) -> None:
		from boss_agent_cli.api.zhilian_client import _OPEN_CLIENTS

		client = ZhilianClient(_StubAuth())
		fake_http = MagicMock()
		client._client = fake_http

		assert client in _OPEN_CLIENTS
		client.close()

		fake_http.close.assert_called_once()
		assert client._client is None
		assert client._closed is True
		assert client not in _OPEN_CLIENTS

	def test_atexit_safeguard_closes_open_clients(self) -> None:
		from boss_agent_cli.api.zhilian_client import _OPEN_CLIENTS, _close_open_clients

		client1 = ZhilianClient(_StubAuth())
		client2 = ZhilianClient(_StubAuth())
		assert client1 in _OPEN_CLIENTS
		assert client2 in _OPEN_CLIENTS

		_close_open_clients()

		assert client1._closed is True
		assert client2._closed is True

	def test_atexit_safeguard_ignores_close_errors(self) -> None:
		from boss_agent_cli.api.zhilian_client import _close_open_clients

		broken = ZhilianClient(_StubAuth())
		good = ZhilianClient(_StubAuth())
		broken.close = MagicMock(side_effect=RuntimeError("boom"))

		_close_open_clients()

		broken.close.assert_called_once()
		assert good._closed is True


class TestZhilianClientReadonlyMethods:
	"""P0 只读能力参数构造正确。"""

	def setup_method(self) -> None:
		self.client = ZhilianClient(_StubAuth())
		self.client._request = MagicMock(return_value={"code": 200, "data": {}})

	def test_search_jobs_minimal_params(self) -> None:
		self.client.search_jobs("Python")
		call = self.client._request.call_args
		assert call.args == ("GET", SEARCH_URL)
		assert call.kwargs["params"] == {"keyword": "Python", "pageNum": 1}

	def test_search_jobs_maps_supported_filters(self) -> None:
		self.client.search_jobs(
			"Python",
			page=2,
			page_size=20,
			city="530",
			salary="20K-30K",
			experience="3-5年",
			education="本科",
			scale="100-499人",
			industry="互联网",
			stage="A轮",
			job_type="全职",
		)
		params = self.client._request.call_args.kwargs["params"]
		assert params["keyword"] == "Python"
		assert params["pageNum"] == 2
		assert params["pageSize"] == 20
		assert params["cityId"] == "530"
		assert params["salary"] == "20K-30K"
		assert params["workExp"] == "3-5年"
		assert params["education"] == "本科"
		assert params["companySize"] == "100-499人"
		assert params["industry"] == "互联网"
		assert params["financingStage"] == "A轮"
		assert params["jobType"] == "全职"

	def test_search_jobs_accepts_boss_compatible_aliases(self) -> None:
		self.client.search_jobs(
			"Python",
			raw_params={"cityId": "530", "extra": "keep"},
			city="北京",
			city_code="538",
			experience="3-5年",
			experience_code="104",
			degree="本科",
			degree_code="203",
			job_type="全职",
			job_type_code="1901",
		)
		params = self.client._request.call_args.kwargs["params"]
		assert params["cityId"] == "538"
		assert params["extra"] == "keep"
		assert params["workExp"] == "104"
		assert params["education"] == "203"
		assert params["jobType"] == "1901"

	def test_job_detail_uses_path_param_url(self) -> None:
		self.client.job_detail("job-1")
		call = self.client._request.call_args
		assert call.args == ("GET", DETAIL_URL_TEMPLATE.format(job_id="job-1"))

	def test_recommend_jobs_uses_page_num(self) -> None:
		self.client.recommend_jobs(page=3)
		call = self.client._request.call_args
		assert call.args == ("GET", RECOMMEND_URL)
		assert call.kwargs["params"] == {"pageNum": 3}

	def test_user_info_no_params(self) -> None:
		self.client.user_info()
		call = self.client._request.call_args
		assert call.args == ("GET", USER_INFO_URL)

	def test_job_card_uses_security_id_path_and_lid_param(self) -> None:
		self.client.job_card("sec-1", lid="list-1")
		call = self.client._request.call_args
		assert call.args == ("GET", JOB_CARD_URL_TEMPLATE.format(security_id="sec-1"))
		assert call.kwargs["params"] == {"lid": "list-1"}

	def test_job_history_uses_page_num(self) -> None:
		self.client.job_history(page=4)
		call = self.client._request.call_args
		assert call.args == ("GET", JOB_HISTORY_URL)
		assert call.kwargs["params"] == {"pageNum": 4}

	def test_deliver_list_uses_page_num(self) -> None:
		self.client.deliver_list(page=5)
		call = self.client._request.call_args
		assert call.args == ("GET", DELIVER_LIST_URL)
		assert call.kwargs["params"] == {"pageNum": 5}

	def test_resume_baseinfo_no_params(self) -> None:
		self.client.resume_baseinfo()
		call = self.client._request.call_args
		assert call.args == ("GET", RESUME_BASEINFO_URL)

	def test_resume_expect_no_params(self) -> None:
		self.client.resume_expect()
		call = self.client._request.call_args
		assert call.args == ("GET", RESUME_EXPECT_URL)

	def test_interview_data_no_stub_and_no_params(self) -> None:
		self.client.interview_data()
		call = self.client._request.call_args
		assert call.args == ("GET", INTERVIEW_DATA_URL)

	def test_greet_posts_with_csrf_header(self) -> None:
		self.client.get_csrf_token = MagicMock(return_value="csrf-123")
		self.client.greet("sec-1", "job-1", "hello")
		call = self.client._request.call_args
		assert call.args == ("POST", GREET_URL)
		assert call.kwargs["data"] == {
			"securityId": "sec-1",
			"jobId": "job-1",
			"message": "hello",
		}
		assert call.kwargs["headers"] == {"csrf-token": "csrf-123"}

	def test_apply_posts_with_csrf_header(self) -> None:
		self.client.get_csrf_token = MagicMock(return_value="csrf-123")
		self.client.apply("sec-1", "job-1", lid="list-1")
		call = self.client._request.call_args
		assert call.args == ("POST", APPLY_URL)
		assert call.kwargs["data"] == {
			"securityId": "sec-1",
			"jobId": "job-1",
			"lid": "list-1",
		}
		assert call.kwargs["headers"] == {"csrf-token": "csrf-123"}


class TestZhilianClientCsrf:
	"""CSRF token 获取与缓存。"""

	def test_extract_csrf_token_from_meta(self) -> None:
		html = '<html><head><meta content="csrf-123" name="csrf-token"></head></html>'
		assert _extract_csrf_token(html) == "csrf-123"

	def test_get_csrf_token_fetches_and_caches_value(self) -> None:
		client = ZhilianClient(_StubAuth())
		mock_httpx = MagicMock()
		mock_resp = MagicMock()
		mock_resp.status_code = 200
		mock_resp.text = '<meta name="csrf-token" content="csrf-123">'
		mock_resp.cookies.items.return_value = []
		mock_resp.raise_for_status.return_value = None
		mock_httpx.get.return_value = mock_resp
		client._get_client = MagicMock(return_value=mock_httpx)
		client._merge_cookies = MagicMock()
		client._throttle.wait = MagicMock()
		client._throttle.mark = MagicMock()

		assert client.get_csrf_token() == "csrf-123"
		assert client.get_csrf_token() == "csrf-123"
		mock_httpx.get.assert_called_once_with(
			CSRF_BOOTSTRAP_URL,
			headers={
				"Referer": CSRF_BOOTSTRAP_URL,
				"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
			},
		)

	def test_get_csrf_token_force_refresh_refetches_page(self) -> None:
		client = ZhilianClient(_StubAuth())
		client._fetch_csrf_token = MagicMock(side_effect=["csrf-1", "csrf-2"])

		assert client.get_csrf_token() == "csrf-1"
		assert client.get_csrf_token(force_refresh=True) == "csrf-2"
		assert client._fetch_csrf_token.call_count == 2

	def test_get_csrf_token_raises_when_meta_missing(self) -> None:
		client = ZhilianClient(_StubAuth())
		mock_httpx = MagicMock()
		mock_resp = MagicMock()
		mock_resp.status_code = 200
		mock_resp.text = "<html></html>"
		mock_resp.cookies.items.return_value = []
		mock_resp.raise_for_status.return_value = None
		mock_httpx.get.return_value = mock_resp
		client._get_client = MagicMock(return_value=mock_httpx)
		client._merge_cookies = MagicMock()
		client._throttle.wait = MagicMock()
		client._throttle.mark = MagicMock()

		try:
			client.get_csrf_token()
		except RuntimeError as exc:
			assert "csrf-token" in str(exc)
		else:
			raise AssertionError("expected RuntimeError when csrf meta is missing")


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
