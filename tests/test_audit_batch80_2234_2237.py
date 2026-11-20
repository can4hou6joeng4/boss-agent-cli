"""第十一轮审计批次：Issue #2234–#2237 的回归测试。

- #2234：无请求预算的客户端也必须把 Retry-After 截断到本地冷却上限。
- #2235：`probe_cdp` 把 `httpx.InvalidURL` 当成探测不可用，不打断登录降级链。
- #2236：Cookie 校验跟随重定向后按最终 URL 做风控判定并落风险锁。
- #2237：429 / 业务码 9 是限流而非登录态失效，报 RATE_LIMITED 而不是降级。

全部测试离线运行；不调用平台 API、浏览器或真实网络。
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest

from boss_agent_cli.api import endpoints
from boss_agent_cli.api._base_client import _MAX_COOLDOWN_SECONDS
from boss_agent_cli.api.client import BossClient, PlatformRateLimitError
from boss_agent_cli.auth.browser import probe_cdp
from boss_agent_cli.auth.manager import AuthManager
from boss_agent_cli.commands._login_errors import classify_login_error


class FakeCookieJar:
	def items(self):
		return {}.items()

	def set(self, name: str, value: str):
		pass

	def get(self, name: str):
		return None


class FakeResponse:
	def __init__(self, *, status_code: int = 200, payload: dict | None = None, headers: dict | None = None):
		self.status_code = status_code
		self._payload = payload if payload is not None else {"code": 0}
		self.text = ""
		self.headers = dict(headers or {})
		self.cookies = FakeCookieJar()

	def json(self):
		return self._payload

	def raise_for_status(self):
		if self.status_code >= 400:
			raise httpx.HTTPStatusError(
				"http error",
				request=httpx.Request("GET", endpoints.USER_INFO_URL),
				response=httpx.Response(self.status_code),
			)


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
		self.token = {"cookies": {"wt2": "cookie"}, "stoken": "stoken", "user_agent": "agent-ua"}

	def get_token(self):
		return self.token

	def force_refresh(self, cdp_url: str | None = None, browser_source: str | None = None):
		pass


def _client() -> BossClient:
	client = BossClient(FakeAuthManager())
	client._throttle.wait = lambda: None
	client._throttle.mark = lambda: None
	return client


# ── #2234：Retry-After 截断 ─────────────────────────────────────────


@pytest.mark.parametrize(
	"retry_after",
	(
		"86400",
		# HTTP-date 形式的远未来时间：retry_after_seconds 会算出 ~2.5e11 秒。
		"Sun, 06 Nov 9999 08:49:37 GMT",
	),
)
def test_budgetless_client_clamps_retry_after_to_local_cooldown_cap(retry_after: str) -> None:
	client = _client()
	assert client._request_budget is None
	client._client = FakeHttpxClient(
		[FakeResponse(status_code=429, payload={"code": 9}, headers={"retry-after": retry_after}) for _ in range(4)]
	)
	sleeps: list[float] = []

	def record(value, **_kwargs):
		sleeps.append(value)

	with patch("boss_agent_cli.api._base_client.cancellable_sleep", side_effect=record):
		client._request("GET", endpoints.USER_INFO_URL)

	assert len(sleeps) == 3
	assert all(0 < value <= _MAX_COOLDOWN_SECONDS for value in sleeps)
	assert sleeps[0] == _MAX_COOLDOWN_SECONDS


def test_budgetless_client_keeps_exponential_backoff_for_small_retry_after() -> None:
	client = _client()
	client._client = FakeHttpxClient(
		[FakeResponse(status_code=429, payload={"code": 9}, headers={"retry-after": "5"}) for _ in range(4)]
	)
	sleeps: list[float] = []

	def record(value, **_kwargs):
		sleeps.append(value)

	with patch("boss_agent_cli.api._base_client.cancellable_sleep", side_effect=record):
		client._request("GET", endpoints.USER_INFO_URL)

	# 第 0 轮取 max(5, 10)：指数项仍占主导，截断不影响正常退避。
	assert sleeps[0] == 10


# ── #2235：InvalidURL 视为探测不可用 ───────────────────────────────


def test_probe_cdp_treats_invalid_url_as_unavailable() -> None:
	with patch("httpx.get", side_effect=httpx.InvalidURL("invalid cdp url")):
		assert probe_cdp("http://[::1") is None


# ── #2236：跟随重定向后按最终 URL 做风控判定 ───────────────────────


class FakeFinalResponse:
	def __init__(self, *, url: str, status_code: int = 200, payload: dict | None = None, text: str = ""):
		self.url = url
		self.status_code = status_code
		self._payload = payload if payload is not None else {"code": 0}
		self.text = text
		self.headers = {"content-type": "text/html"}
		self.cookies = FakeCookieJar()

	def json(self):
		return self._payload

	def raise_for_status(self):
		if self.status_code >= 400:
			raise httpx.HTTPStatusError(
				"http error",
				request=httpx.Request("GET", endpoints.USER_INFO_URL),
				response=httpx.Response(self.status_code),
			)


def test_verify_cookie_follows_redirects_and_locks_risk_on_final_page(tmp_path) -> None:
	manager = AuthManager(tmp_path)
	final_url = "https://www.zhipin.com/web/passport/zp/403.html?code=31"
	response = FakeFinalResponse(url=final_url, text="<html></html>")
	with (
		patch("httpx.get", return_value=response) as mock_get,
		patch.object(manager, "_record_cdp_risk_lock") as mock_lock,
	):
		with pytest.raises(Exception) as excinfo:
			manager._verify_cookie({"cookies": {"wt2": "cookie"}})

	assert type(excinfo.value).__name__ == "EnvironmentRiskError"
	assert mock_lock.called
	assert mock_get.call_args.kwargs["follow_redirects"] is True


# ── #2237：限流不是登录态失效 ───────────────────────────────────────


@pytest.mark.parametrize(
	("status_code", "payload"),
	(
		(429, {"code": 9, "message": "请求过于频繁"}),
		(200, {"code": "9", "message": "请求过于频繁"}),
	),
)
def test_verify_cookie_rate_limit_raises_platform_rate_limit_error(tmp_path, status_code, payload) -> None:
	manager = AuthManager(tmp_path)
	response = FakeFinalResponse(url="https://www.zhipin.com/wapi/zpuser/wap/getUserInfo.json", status_code=status_code, payload=payload)
	with patch("httpx.get", return_value=response):
		with pytest.raises(PlatformRateLimitError):
			manager._verify_cookie({"cookies": {"wt2": "cookie"}})


def test_classify_login_error_maps_rate_limit_to_rate_limited_code(tmp_path) -> None:
	ctx = MagicMock()
	ctx.obj = {"json_output": True, "data_dir": tmp_path, "platform": "zhipin"}

	payload = classify_login_error(PlatformRateLimitError("limited"), ctx)

	assert payload["code"] == "RATE_LIMITED"
	assert payload["recoverable"] is True
	assert "限流" in payload["message"]
	assert any("等待" in action for action in payload["hints"]["operator_actions"])
