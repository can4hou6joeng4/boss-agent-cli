"""api/client.py 公开方法参数映射覆盖率测试。

针对 20+ 个公开方法批量覆盖：mock `_request` / `_browser_request`，
验证每个方法的 URL + params/data 构建是否正确。
"""

from unittest.mock import MagicMock, patch

import pytest

from boss_agent_cli.api import endpoints
from boss_agent_cli.api.client import BossClient


class _StubAuth:
	"""极简 AuthManager 替身，只返回合法 token，不触发真实 httpx。"""

	def __init__(self):
		self.token = {"cookies": {"wt2": "c"}, "stoken": "s", "user_agent": "UA"}

	def get_token(self):
		return self.token

	def force_refresh(self, cdp_url=None):
		self.token = {**self.token, "stoken": "refreshed"}


def _make_client() -> BossClient:
	"""构建一个 BossClient，mock 掉 _request 和 _browser_request。"""
	client = BossClient(_StubAuth())
	client._request = MagicMock(return_value={"code": 0, "zpData": {}})
	client._browser_request = MagicMock(return_value={"code": 0, "zpData": {}})
	return client


# ── 高风险通道（浏览器通道）─────────────────────────────────────────────


def test_search_jobs_minimal_params():
	client = _make_client()
	client.search_jobs("python")
	call = client._browser_request.call_args
	assert call.args == ("GET", endpoints.SEARCH_URL)
	assert call.kwargs["params"]["query"] == "python"
	assert call.kwargs["params"]["page"] == 1


def test_search_jobs_with_page():
	client = _make_client()
	client.search_jobs("python", page=3)
	call = client._browser_request.call_args
	assert call.kwargs["params"]["page"] == 3


def test_search_jobs_all_filter_codes_applied():
	"""验证 8 个 filter 的 code 映射全部走通。"""
	client = _make_client()
	# 用已知合法值
	client.search_jobs(
		"python",
		city="北京",
		salary="20-50K",
		experience="3-5年",
		education="本科",
		scale="100-499人",
		industry="互联网",
		stage="A轮",
		job_type="全职",
	)
	params = client._browser_request.call_args.kwargs["params"]
	assert params["query"] == "python"
	# 每个映射都应该生成一个 code 字段，code 非空
	assert params.get("city") is not None
	assert params.get("salary") is not None
	assert params.get("experience") is not None
	assert params.get("degree") is not None
	assert params.get("scale") is not None
	assert params.get("industry") is not None
	assert params.get("stage") is not None
	assert params.get("jobType") is not None


def test_search_jobs_unknown_city_raises():
	client = _make_client()
	with pytest.raises(ValueError, match="未知城市"):
		client.search_jobs("python", city="火星")


def test_search_jobs_unknown_salary_does_not_crash():
	"""未知 salary 不生成 code，但不抛异常（静默跳过）。"""
	client = _make_client()
	client.search_jobs("python", salary="unknown-range")
	params = client._browser_request.call_args.kwargs["params"]
	assert "salary" not in params


def test_recommend_jobs_default_page():
	client = _make_client()
	client.recommend_jobs()
	call = client._browser_request.call_args
	assert call.args == ("GET", endpoints.RECOMMEND_URL)
	assert call.kwargs["params"]["page"] == 1


def test_recommend_jobs_custom_page():
	client = _make_client()
	client.recommend_jobs(page=5)
	assert client._browser_request.call_args.kwargs["params"]["page"] == 5


def test_greet_default_message():
	client = _make_client()
	client.greet("sid_1", "jid_1")
	call = client._browser_request.call_args
	assert call.args == ("POST", endpoints.GREET_URL)
	data = call.kwargs["data"]
	assert data["securityId"] == "sid_1"
	assert data["jobId"] == "jid_1"
	assert "岗位" in data["greeting"]  # 默认问候语带"岗位"字样


def test_greet_custom_message():
	client = _make_client()
	client.greet("sid_1", "jid_1", message="Hello")
	data = client._browser_request.call_args.kwargs["data"]
	assert data["greeting"] == "Hello"


def test_apply_without_lid():
	client = _make_client()
	client.apply("sid", "jid")
	data = client._browser_request.call_args.kwargs["data"]
	assert data["securityId"] == "sid"
	assert data["jobId"] == "jid"
	assert "lid" not in data


def test_apply_with_lid():
	client = _make_client()
	client.apply("sid", "jid", lid="lid_abc")
	data = client._browser_request.call_args.kwargs["data"]
	assert data["lid"] == "lid_abc"


def test_job_card_prefers_httpx():
	"""job_card 走 httpx 优先，httpx 成功时不应调用 _browser_request。"""
	client = _make_client()
	client.job_card_httpx = MagicMock(return_value={"code": 0, "zpData": {"title": "Go"}})

	result = client.job_card("sid")
	assert result["zpData"]["title"] == "Go"
	client.job_card_httpx.assert_called_once_with("sid", "")
	client._browser_request.assert_not_called()


def test_job_card_falls_back_to_browser_on_httpx_failure():
	"""httpx 抛异常时降级到浏览器通道。"""
	client = _make_client()
	client.job_card_httpx = MagicMock(side_effect=RuntimeError("httpx died"))

	client.job_card("sid", lid="L1")
	call = client._browser_request.call_args
	assert call.args == ("GET", endpoints.JOB_CARD_URL)
	assert call.kwargs["params"]["securityId"] == "sid"
	assert call.kwargs["params"]["lid"] == "L1"


def test_job_card_httpx_routes_to_request():
	"""job_card_httpx 直接走 _request 通道。"""
	client = _make_client()
	client.job_card_httpx("sid", lid="L")
	call = client._request.call_args
	assert call.args == ("GET", endpoints.JOB_CARD_URL)
	assert call.kwargs["params"] == {"securityId": "sid", "lid": "L"}


def test_exchange_contact_default_type_is_phone():
	client = _make_client()
	client.exchange_contact("sid", "uid", "张三")
	call = client._browser_request.call_args
	assert call.args == ("POST", endpoints.EXCHANGE_REQUEST_URL)
	data = call.kwargs["data"]
	assert data["type"] == 1
	assert data["securityId"] == "sid"
	assert data["uniqueId"] == "uid"
	assert data["name"] == "张三"


def test_exchange_contact_wechat_type():
	client = _make_client()
	client.exchange_contact("sid", "uid", "李四", exchange_type=2)
	data = client._browser_request.call_args.kwargs["data"]
	assert data["type"] == 2


# ── 低风险通道（httpx 直连）─────────────────────────────────────────────


def test_job_detail_passes_encrypted_job_id():
	client = _make_client()
	client.job_detail("encrypted_j1")
	call = client._request.call_args
	assert call.args == ("GET", endpoints.DETAIL_URL)
	assert call.kwargs["params"] == {"encryptJobId": "encrypted_j1"}


def test_user_info_no_params():
	client = _make_client()
	client.user_info()
	call = client._request.call_args
	assert call.args == ("GET", endpoints.USER_INFO_URL)


def test_resume_baseinfo():
	client = _make_client()
	client.resume_baseinfo()
	assert client._request.call_args.args == ("GET", endpoints.RESUME_BASEINFO_URL)


def test_resume_expect():
	client = _make_client()
	client.resume_expect()
	assert client._request.call_args.args == ("GET", endpoints.RESUME_EXPECT_URL)


def test_resume_status():
	client = _make_client()
	client.resume_status()
	assert client._request.call_args.args == ("GET", endpoints.RESUME_STATUS_URL)


def test_deliver_list_default_and_page():
	client = _make_client()
	client.deliver_list()
	assert client._request.call_args.kwargs["params"] == {"page": 1}

	client.deliver_list(page=7)
	assert client._request.call_args.kwargs["params"] == {"page": 7}


def test_friend_list_default_and_page():
	client = _make_client()
	client.friend_list(page=4)
	call = client._request.call_args
	assert call.args == ("GET", endpoints.FRIEND_LIST_URL)
	assert call.kwargs["params"] == {"page": 4}


def test_interview_data_no_params():
	client = _make_client()
	client.interview_data()
	assert client._request.call_args.args == ("GET", endpoints.INTERVIEW_DATA_URL)


def test_job_history():
	client = _make_client()
	client.job_history(page=2)
	call = client._request.call_args
	assert call.args == ("GET", endpoints.JOB_HISTORY_URL)
	assert call.kwargs["params"] == {"page": 2}


def test_chat_history_defaults():
	client = _make_client()
	client.chat_history(gid="g1", security_id="s1")
	params = client._request.call_args.kwargs["params"]
	assert params == {"gid": "g1", "securityId": "s1", "page": 1, "c": 20, "src": 0}


def test_chat_history_custom_pagination():
	client = _make_client()
	client.chat_history(gid="g1", security_id="s1", page=3, count=50)
	params = client._request.call_args.kwargs["params"]
	assert params["page"] == 3
	assert params["c"] == 50


def test_friend_label_add():
	client = _make_client()
	client.friend_label("friend_1", 42)
	call = client._request.call_args
	assert call.args == ("GET", endpoints.FRIEND_LABEL_ADD_URL)
	assert call.kwargs["params"]["labelId"] == 42


def test_friend_label_remove():
	client = _make_client()
	client.friend_label("friend_1", 42, remove=True)
	assert client._request.call_args.args == ("GET", endpoints.FRIEND_LABEL_DELETE_URL)


def test_friend_label_friend_source():
	client = _make_client()
	client.friend_label("f1", 1, friend_source=5)
	assert client._request.call_args.kwargs["params"]["friendSource"] == 5


def test_geek_get_job():
	client = _make_client()
	client.geek_get_job("sid_x")
	call = client._request.call_args
	assert call.args == ("GET", endpoints.GEEK_GET_JOB_URL)
	assert call.kwargs["params"] == {"securityId": "sid_x"}


# ── 生命周期 / close 等 ─────────────────────────────────────────────────


def test_close_is_idempotent():
	"""close 多次调用应无副作用。"""
	client = BossClient(_StubAuth())
	client.close()
	# 第二次 close 不应抛异常
	client.close()


def test_context_manager_closes_on_exit():
	"""with 语句退出时应自动 close。"""
	auth = _StubAuth()
	with BossClient(auth) as client:
		assert client is not None
	# 退出后应已关闭
	assert client._closed is True


def test_close_releases_browser_session_if_exists():
	"""close 时若 browser_session 存在，应调用其 close。"""
	client = BossClient(_StubAuth())
	fake_browser = MagicMock()
	client._browser_session = fake_browser
	client.close()
	fake_browser.close.assert_called_once()
	assert client._browser_session is None


def test_close_releases_httpx_client_if_exists():
	"""close 时若 httpx client 存在，应调用其 close。"""
	client = BossClient(_StubAuth())
	fake_http = MagicMock()
	client._client = fake_http
	client.close()
	fake_http.close.assert_called_once()
	assert client._client is None


@patch("boss_agent_cli.api.client.atexit")
def test_atexit_safeguard_closes_open_clients(mock_atexit):
	"""atexit handler 应关闭还未被 close 的 BossClient。"""
	from boss_agent_cli.api.client import _close_open_clients, _OPEN_CLIENTS

	client1 = BossClient(_StubAuth())
	client2 = BossClient(_StubAuth())
	# 它们应已经被加入 _OPEN_CLIENTS（通过 __init__）
	# 注：实际代码中 _OPEN_CLIENTS.add 在 __init__ 里，我们通过弱引用能看到
	_OPEN_CLIENTS.add(client1)
	_OPEN_CLIENTS.add(client2)

	# 手动触发 atexit handler
	_close_open_clients()

	# 两个都应被关闭
	assert client1._closed is True
	assert client2._closed is True


@patch("boss_agent_cli.api.client.atexit")
def test_atexit_safeguard_ignores_close_errors(mock_atexit):
	"""atexit handler 中若 close() 抛异常，应静默忽略不影响其他 client。"""
	from boss_agent_cli.api.client import _close_open_clients, _OPEN_CLIENTS

	broken = BossClient(_StubAuth())
	broken.close = MagicMock(side_effect=RuntimeError("boom"))
	good = BossClient(_StubAuth())
	_OPEN_CLIENTS.add(broken)
	_OPEN_CLIENTS.add(good)

	# 不应抛异常
	_close_open_clients()
	# good 依然能被关闭
	assert good._closed is True
