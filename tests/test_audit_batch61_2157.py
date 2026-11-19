"""读请求的 HTTP 状态错误不得把 __zp_stoken__ 带进错误信封（Issue #2157）。

GET 请求统一注入 ``__zp_stoken__`` 查询参数，而 httpx 的 ``HTTPStatusError`` 文本
包含完整请求 URL。读路径原样上抛后，``handle_auth_errors`` 的兜底会把 ``str(exc)``
写进 ``error.message``，stoken 因此进入 stdout 信封与 Agent 记录。
"""

from __future__ import annotations

import json

import httpx
import pytest

from boss_agent_cli.api import endpoints
from boss_agent_cli.api.client import BossClient
from boss_agent_cli.output import redact_sensitive_text

_STOKEN = "secret-token-2157"
_FULL_URL = f"https://www.zhipin.com/wapi/zpuser/wap/getUserInfo.json?__zp_stoken__={_STOKEN}&page=1"


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
	def __init__(self, *, status_code: int = 200, payload: dict | None = None, text: str = "") -> None:
		self.status_code = status_code
		self._payload = payload if payload is not None else {"code": 0}
		self.text = text
		self.cookies = FakeCookieJar()
		self.url = _FULL_URL
		self.request = httpx.Request("GET", _FULL_URL)

	def json(self):
		return self._payload

	def raise_for_status(self) -> None:
		if self.status_code >= 400:
			raise httpx.HTTPStatusError(
				f"Server error '{self.status_code} Internal Server Error' for url '{_FULL_URL}'",
				request=self.request,
				response=self,
			)


class FakeHttpxClient:
	def __init__(self, responses: list[FakeResponse]):
		self.responses = list(responses)
		self.calls: list[dict] = []
		self.cookies = FakeCookieJar()

	def request(self, method: str, url: str, headers: dict | None = None, **kwargs):
		self.calls.append({"method": method, "url": url, "headers": headers, "kwargs": kwargs})
		return self.responses.pop(0)

	def close(self) -> None:
		pass


class FakeAuthManager:
	def __init__(self) -> None:
		self.token = {"cookies": {"wt2": "cookie"}, "stoken": _STOKEN, "user_agent": "agent-ua"}

	def get_token(self):
		return self.token

	def force_refresh(self, cdp_url: str | None = None, browser_source: str | None = None) -> None:
		self.token = {**self.token, "stoken": "refreshed"}


def _client(response: FakeResponse) -> tuple[BossClient, FakeHttpxClient]:
	client = BossClient(FakeAuthManager())
	http_client = FakeHttpxClient([response])
	client._client = http_client
	client._throttle.wait = lambda: None
	client._throttle.mark = lambda: None
	return client, http_client


def test_read_status_error_message_omits_stoken() -> None:
	"""500 的读响应：异常类型与状态码保留，文本与请求属性都不含 stoken。"""
	client, _http = _client(FakeResponse(status_code=500))

	with pytest.raises(httpx.HTTPStatusError) as excinfo:
		client._request("GET", endpoints.USER_INFO_URL)

	error = excinfo.value
	text = str(error)
	assert _STOKEN not in text
	assert "__zp_stoken__" not in text
	# 诊断信息保留：方法、路径、状态码。
	assert "GET" in text
	assert "/wapi/zpuser/wap/getUserInfo.json" in text
	assert "HTTP 500" in text
	assert error.response.status_code == 500
	assert error.request.url.query == b""
	# 原异常不作为上下文出现在 traceback 里。
	assert error.__cause__ is None
	assert error.__suppress_context__ is True


def test_write_status_error_result_stays_unconfirmed_without_stoken() -> None:
	"""写路径仍返回「结果未确认」，且返回体里不出现 stoken（#2152 契约不变）。"""
	client, _http = _client(FakeResponse(status_code=500))

	result = client.friend_label("test-friend", 1)

	assert result["__cli_error_code__"] == "ACTION_UNCONFIRMED"
	assert result["__cli_error_details__"] == {
		"outcome": "unknown",
		"replay_safe": False,
		"reason": "http_status_error",
	}
	assert _STOKEN not in json.dumps(result, ensure_ascii=False)


def test_free_text_redaction_covers_zp_stoken_query_param() -> None:
	"""信封与 stderr 的文本脱敏第二道防线：`__zp_stoken__=...` 同样遮盖。"""
	text = f"请求失败: Server error '500' for url '{_FULL_URL}'"

	redacted = redact_sensitive_text(text)

	assert _STOKEN not in redacted
	assert "__zp_stoken__=[REDACTED]" in redacted
