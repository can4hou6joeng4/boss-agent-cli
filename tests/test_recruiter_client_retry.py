"""BossRecruiterClient._request 重试循环的直接测试。

此前招聘者 client 的 _request 重试路径无任何直接覆盖（其余测试都 mock 掉 _request）。
这些用例锁定 403/安全验证刷新、stoken 过期刷新、限频冷却、达上限抛错、
__cli_endpoint_hint__ 注入与 extra_headers 覆盖等行为，作为后续重构的兜底。
"""
from unittest.mock import patch

import pytest

from boss_agent_cli.api import recruiter_endpoints as ep
from boss_agent_cli.api.recruiter_client import BossRecruiterClient, RecruiterAuthError


class FakeCookieJar:
	def __init__(self, initial: dict[str, str] | None = None):
		self._data = dict(initial or {})

	def items(self):
		return self._data.items()

	def set(self, name: str, value: str):
		self._data[name] = value

	def get(self, name: str):
		return self._data.get(name)


class FakeResponse:
	def __init__(self, *, status_code: int = 200, payload: dict | None = None, text: str = "", cookies: dict | None = None):
		self.status_code = status_code
		self._payload = payload or {"code": 0}
		self.text = text
		self.cookies = FakeCookieJar(cookies)

	def json(self):
		return self._payload

	def raise_for_status(self):
		if self.status_code >= 400:
			raise RuntimeError(f"HTTP {self.status_code}")


class FakeHttpxClient:
	def __init__(self, responses: list[FakeResponse]):
		self.responses = list(responses)
		self.calls: list[dict] = []
		self.cookies = FakeCookieJar()

	def request(self, method: str, url: str, headers: dict | None = None, **kwargs):
		self.calls.append({"method": method, "url": url, "headers": headers, "kwargs": kwargs})
		return self.responses.pop(0)

	def close(self):
		pass


class FakeAuthManager:
	def __init__(self):
		self.token = {"cookies": {"wt2": "cookie"}, "stoken": "initial-stoken", "user_agent": "agent-ua"}
		self.refresh_calls: list[str | None] = []

	def get_token(self):
		return self.token

	def force_refresh(self, cdp_url: str | None = None):
		self.refresh_calls.append(cdp_url)
		self.token = {**self.token, "stoken": f"refreshed-{len(self.refresh_calls)}"}


def test_request_get_adds_stoken_and_sets_endpoint_hint():
	auth = FakeAuthManager()
	client = BossRecruiterClient(auth)
	http_client = FakeHttpxClient([FakeResponse(payload={"code": 0}, cookies={"bst": "cookie-from-resp"})])
	client._client = http_client
	client._throttle.wait = lambda: None
	client._throttle.mark = lambda: None

	data = client._request("GET", ep.BOSS_FRIEND_LABELS_URL, params={"page": 1})

	assert data["code"] == 0
	assert data["__cli_endpoint_hint__"] == ep.BOSS_FRIEND_LABELS_URL
	assert http_client.calls[0]["kwargs"]["params"]["__zp_stoken__"] == "initial-stoken"
	assert http_client.cookies.get("bst") == "cookie-from-resp"


def test_request_extra_headers_override_is_merged():
	auth = FakeAuthManager()
	client = BossRecruiterClient(auth)
	http_client = FakeHttpxClient([FakeResponse(payload={"code": 0})])
	client._client = http_client
	client._throttle.wait = lambda: None
	client._throttle.mark = lambda: None

	client._request("GET", ep.BOSS_FRIEND_LABELS_URL, extra_headers={"Referer": "https://override"})

	assert http_client.calls[0]["headers"]["Referer"] == "https://override"


@patch("boss_agent_cli.api._base_client.random.uniform", return_value=0)
@patch("boss_agent_cli.api._base_client.time.sleep")
@patch("boss_agent_cli.api._base_client.httpx.Client")
def test_request_retries_after_403_and_refreshes_token(mock_http_client_cls, mock_sleep, mock_uniform):
	auth = FakeAuthManager()
	first = FakeHttpxClient([FakeResponse(status_code=403, text="forbidden")])
	second = FakeHttpxClient([FakeResponse(payload={"code": 0, "zpData": {"ok": True}})])
	mock_http_client_cls.side_effect = [first, second]

	client = BossRecruiterClient(auth, cdp_url="http://127.0.0.1:9222")
	client._throttle.wait = lambda: None
	client._throttle.mark = lambda: None

	data = client._request("GET", ep.BOSS_FRIEND_LABELS_URL)

	assert data["zpData"]["ok"] is True
	assert auth.refresh_calls == ["http://127.0.0.1:9222"]
	assert mock_sleep.call_args_list[0].args[0] == 1
	assert second.calls[0]["kwargs"]["params"]["__zp_stoken__"] == "refreshed-1"


@patch("boss_agent_cli.api._base_client.random.uniform", return_value=0)
@patch("boss_agent_cli.api._base_client.time.sleep")
@patch("boss_agent_cli.api._base_client.httpx.Client")
def test_request_retries_after_stoken_expired_code(mock_http_client_cls, mock_sleep, mock_uniform):
	auth = FakeAuthManager()
	first = FakeHttpxClient([FakeResponse(payload={"code": ep.CODE_STOKEN_EXPIRED})])
	second = FakeHttpxClient([FakeResponse(payload={"code": 0, "zpData": {"ok": True}})])
	mock_http_client_cls.side_effect = [first, second]

	client = BossRecruiterClient(auth)
	client._throttle.wait = lambda: None
	client._throttle.mark = lambda: None

	data = client._request("GET", ep.BOSS_FRIEND_LABELS_URL)

	assert data["zpData"]["ok"] is True
	assert auth.refresh_calls == [None]
	assert mock_sleep.call_args_list[0].args[0] == 1


@patch("boss_agent_cli.api._base_client.time.sleep")
@patch("boss_agent_cli.api._base_client.httpx.Client")
def test_request_retries_after_rate_limited_code(mock_http_client_cls, mock_sleep):
	auth = FakeAuthManager()
	retrying = FakeHttpxClient(
		[
			FakeResponse(payload={"code": ep.CODE_RATE_LIMITED}),
			FakeResponse(payload={"code": 0, "zpData": {"ok": True}}),
		],
	)
	mock_http_client_cls.return_value = retrying

	client = BossRecruiterClient(auth)
	client._throttle.wait = lambda: None
	client._throttle.mark = lambda: None

	data = client._request("GET", ep.BOSS_FRIEND_LABELS_URL)

	assert data["zpData"]["ok"] is True
	assert auth.refresh_calls == []
	assert mock_sleep.call_args_list[0].args[0] == 10


@patch("boss_agent_cli.api._base_client.random.uniform", return_value=0)
@patch("boss_agent_cli.api._base_client.time.sleep")
@patch("boss_agent_cli.api._base_client.httpx.Client")
def test_request_raises_auth_error_after_max_403_retries(mock_http_client_cls, mock_sleep, mock_uniform):
	auth = FakeAuthManager()
	mock_http_client_cls.side_effect = [
		FakeHttpxClient([FakeResponse(status_code=403, text="forbidden")]),
		FakeHttpxClient([FakeResponse(status_code=403, text="forbidden")]),
		FakeHttpxClient([FakeResponse(status_code=403, text="forbidden")]),
		FakeHttpxClient([FakeResponse(status_code=403, text="forbidden")]),
	]

	client = BossRecruiterClient(auth)
	client._throttle.wait = lambda: None
	client._throttle.mark = lambda: None

	with pytest.raises(RecruiterAuthError, match="Token 刷新后仍被拒绝，请重新登录"):
		client._request("GET", ep.BOSS_FRIEND_LABELS_URL)

	assert auth.refresh_calls == [None, None, None]
