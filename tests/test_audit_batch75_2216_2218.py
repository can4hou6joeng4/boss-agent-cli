"""第六轮审计批次：Issue #2216–#2218 的回归测试。

- #2216：写请求遇到「确定未发出」的传输异常不得报成 ACTION_UNCONFIRMED。
- #2217：httpx-only 策略下 job_card 不回退不存在的浏览器通道，保留原始错误。
- #2218：stoken 过期判断容忍字符串业务码 "37"。
"""

import httpx
import pytest
from unittest.mock import patch

from boss_agent_cli.api import endpoints
from boss_agent_cli.api.client import BossClient


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
	def __init__(self, *, status_code: int = 200, payload: dict | None = None, text: str = ""):
		self.status_code = status_code
		self._payload = payload if payload is not None else {"code": 0}
		self.text = text
		self.cookies = FakeCookieJar()

	def json(self):
		return self._payload

	def raise_for_status(self):
		if self.status_code >= 400:
			raise RuntimeError(f"HTTP {self.status_code}")


class RaisingHttpxClient:
	"""request() 直接抛指定异常的 httpx 替身。"""

	def __init__(self, error: Exception):
		self.error = error
		self.calls = 0
		self.cookies = FakeCookieJar()

	def request(self, method: str, url: str, headers: dict | None = None, **kwargs):
		self.calls += 1
		raise self.error

	def close(self):
		pass


class FakeHttpxClient:
	def __init__(self, responses: list[FakeResponse]):
		self.responses = list(responses)
		self.calls: list[dict] = []
		self.cookies = FakeCookieJar()

	def request(self, method: str, url: str, headers: dict | None = None, **kwargs):
		self.calls.append({"method": method, "url": url, "kwargs": kwargs})
		return self.responses.pop(0)

	def close(self):
		pass


class FakeAuthManager:
	def __init__(self):
		self.token = {"cookies": {"wt2": "cookie"}, "stoken": "initial-stoken", "user_agent": "agent-ua"}
		self.refresh_calls: list[str | None] = []

	def get_token(self):
		return self.token

	def force_refresh(self, cdp_url: str | None = None, browser_source: str | None = None):
		self.refresh_calls.append(cdp_url)
		self.token = {**self.token, "stoken": f"refreshed-{len(self.refresh_calls)}"}


def _client(auth: FakeAuthManager, **kwargs) -> BossClient:
	client = BossClient(auth, **kwargs)
	client._throttle.wait = lambda: None
	client._throttle.mark = lambda: None
	return client


# ── #2216：确定未发出的传输异常保留原异常 ────────────────────────────


@pytest.mark.parametrize(
	"error",
	[
		httpx.ConnectError("connection refused"),
		httpx.ConnectTimeout("connect timed out"),
		httpx.PoolTimeout("pool timed out"),
		httpx.ProxyError("proxy handshake failed"),
		httpx.UnsupportedProtocol("unsupported protocol"),
		httpx.LocalProtocolError("local protocol error"),
	],
)
def test_write_request_unsent_transport_errors_are_not_reported_as_unconfirmed(error):
	client = _client(FakeAuthManager())
	http_client = RaisingHttpxClient(error)
	client._client = http_client

	with pytest.raises(type(error)) as excinfo:
		client.friend_label("friend-1", 1)

	assert excinfo.value is error
	assert http_client.calls == 1


@pytest.mark.parametrize(
	"error",
	[
		httpx.ReadTimeout("read timed out"),
		httpx.WriteTimeout("write timed out"),
		httpx.ReadError("read failed"),
		httpx.WriteError("write failed"),
		httpx.RemoteProtocolError("server disconnected"),
	],
)
def test_write_request_maybe_sent_transport_errors_stay_unconfirmed(error):
	client = _client(FakeAuthManager())
	client._client = RaisingHttpxClient(error)

	data = client.friend_label("friend-1", 1)

	assert data["__cli_error_code__"] == "ACTION_UNCONFIRMED"
	assert data["__cli_error_details__"]["outcome"] == "unknown"
	assert data["__cli_error_details__"]["replay_safe"] is False
	assert data["__cli_error_details__"]["reason"] == "transport_error"


def test_read_request_unsent_transport_error_keeps_retryable_contract():
	"""读请求不受写侧分支影响：连接失败原样上抛，由调用方按可重试错误收口。"""
	client = _client(FakeAuthManager())
	error = httpx.ConnectError("connection refused")
	client._client = RaisingHttpxClient(error)

	with pytest.raises(httpx.ConnectError):
		client._request("GET", endpoints.USER_INFO_URL)


# ── #2217：httpx-only 下 job_card 不回退浏览器 ──────────────────────


def test_job_card_httpx_only_propagates_original_error():
	client = _client(FakeAuthManager(), browser_source="httpx-only")
	error = httpx.HTTPStatusError(
		"server error", request=httpx.Request("GET", endpoints.JOB_CARD_URL), response=httpx.Response(500)
	)
	client._client = RaisingHttpxClient(error)

	assert client.browser_enabled() is False
	with pytest.raises(httpx.HTTPStatusError) as excinfo:
		client.job_card("security-id")

	assert excinfo.value is error


def test_job_card_falls_back_to_browser_when_channel_available():
	client = _client(FakeAuthManager())
	sentinel = {"code": 0, "zpData": {"jobName": "stub"}}
	client.job_card_httpx = lambda security_id, lid="": (_ for _ in ()).throw(httpx.ReadTimeout("read timed out"))
	client._browser_request = lambda method, url, **kwargs: sentinel

	assert client.job_card("security-id") is sentinel


def test_job_card_httpx_only_does_not_mask_request_stop():
	from boss_agent_cli.api.request_budget import RequestBudgetExceeded

	client = _client(FakeAuthManager(), browser_source="httpx-only")
	client.job_card_httpx = lambda security_id, lid="": (_ for _ in ()).throw(RequestBudgetExceeded("budget"))

	with pytest.raises(RequestBudgetExceeded):
		client.job_card("security-id")


# ── #2218：字符串业务码 "37" 触发 token 刷新 ────────────────────────


def test_should_refresh_token_response_accepts_string_code():
	client = _client(FakeAuthManager())

	assert client._should_refresh_token_response({"code": "37", "message": "stoken 已过期"}) is True
	assert client._should_refresh_token_response({"code": 37, "message": "stoken 已过期"}) is True
	assert client._should_refresh_token_response({"code": "37", "message": "环境异常"}) is False
	assert client._should_refresh_token_response({"code": 0}) is False


@patch("boss_agent_cli.api._base_client.cancellable_sleep")
@patch("boss_agent_cli.api._base_client.httpx.Client")
def test_request_refreshes_after_string_code_37(mock_http_client_cls, mock_sleep):
	auth = FakeAuthManager()
	first = FakeHttpxClient([FakeResponse(payload={"code": "37", "message": "stoken 已过期"})])
	second = FakeHttpxClient([FakeResponse(payload={"code": 0, "zpData": {"ok": True}})])
	mock_http_client_cls.side_effect = [first, second]

	client = _client(auth)
	data = client._request("GET", endpoints.USER_INFO_URL)

	assert data["zpData"]["ok"] is True
	assert auth.refresh_calls == [None]
	assert second.calls[0]["kwargs"]["params"]["__zp_stoken__"] == "refreshed-1"
