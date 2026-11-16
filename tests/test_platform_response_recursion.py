"""平台响应深嵌套 / 非对象 JSON 不得被兜成可重试的 NETWORK_ERROR（Issue #1368）。

httpx 的 ``resp.json()`` 遇到极深嵌套会抛 ``RecursionError``（3.11–3.13；3.14 起
解析器不再受限），遇到非对象 JSON 则返回列表 / 标量。两者都属于「平台响应无法
解析」：同一响应重试必然复现，命令出口必须是不可恢复的 ``INTERNAL_ERROR``。
测试用 mock 让 ``.json()`` 直接抛 ``RecursionError``，不依赖解释器的解析深度上限。
"""

import json
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from boss_agent_cli.api.browser_client import BrowserSession, _PageRiskResponseTracker
from boss_agent_cli.api.client import BossClient
from boss_agent_cli.main import cli
from boss_agent_cli.platforms.zhipin import BossPlatform


class _FakeCookieJar:
	def __init__(self) -> None:
		self._data: dict[str, str] = {}

	def items(self):
		return self._data.items()

	def set(self, name: str, value: str) -> None:
		self._data[name] = value


class _FakeResponse:
	"""只实现 ``_request`` 会碰到的响应表面；``json()`` 按用例注入异常或载荷。"""

	status_code = 200
	url = "https://www.zhipin.com/wapi/zpgeek/search/joblist.json"
	text = ""

	def __init__(self, *, payload=None, error: Exception | None = None) -> None:
		self._payload = payload
		self._error = error
		self.headers = {"content-type": "application/json"}
		self.cookies = _FakeCookieJar()

	def json(self):
		if self._error is not None:
			raise self._error
		return self._payload

	def raise_for_status(self) -> None:
		return None


class _FakeHttpxClient:
	def __init__(self, response: _FakeResponse) -> None:
		self.response = response
		self.calls: list[tuple[str, str]] = []
		self.cookies = _FakeCookieJar()

	def request(self, method: str, url: str, headers=None, **kwargs):
		self.calls.append((method, url))
		return self.response

	def close(self) -> None:
		return None


class _FakeAuth:
	def __init__(self) -> None:
		self.token = {"cookies": {"wt2": "cookie"}, "stoken": "stoken", "user_agent": "ua"}
		self.refresh_calls = 0

	def get_token(self):
		return self.token

	def force_refresh(self, **kwargs) -> None:
		self.refresh_calls += 1


def _invoke_search(tmp_path, response: _FakeResponse):
	"""走真实的 BossClient._request / search pipeline，只替换登录态与平台入口。"""
	client = BossClient(_FakeAuth(), browser_source="httpx-only")
	http_client = _FakeHttpxClient(response)
	client._client = http_client
	client._throttle.wait = lambda: None
	client._throttle.mark = lambda: None

	auth = MagicMock()
	auth.account_fingerprint.return_value = "acct"
	platform = BossPlatform(client)
	with (
		patch("boss_agent_cli.commands.search.AuthManager", return_value=auth),
		patch("boss_agent_cli.commands.search.get_platform_instance", return_value=platform),
	):
		result = CliRunner().invoke(
			cli,
			[
				"--data-dir",
				str(tmp_path),
				"--json",
				"search",
				"golang",
			],
		)
	return result, http_client


def _single_error_envelope(result) -> dict:
	assert result.exit_code == 1, result.output
	lines = [line for line in result.stdout.splitlines() if line.strip()]
	assert len(lines) == 1, result.output
	assert "Traceback" not in result.output
	payload = json.loads(lines[0])
	assert payload["ok"] is False
	return payload


def test_search_deep_nested_response_is_internal_error_without_retry(tmp_path):
	"""200 + 深嵌套 JSON：RecursionError 被捕获，信封不可恢复且不重发请求。"""
	result, http_client = _invoke_search(
		tmp_path,
		_FakeResponse(error=RecursionError("maximum recursion depth exceeded")),
	)

	payload = _single_error_envelope(result)
	assert payload["error"]["code"] == "INTERNAL_ERROR"
	assert payload["error"]["recoverable"] is False
	# 解析失败发生在拿到响应之后：同一响应重试必然复现，不得重发。
	assert len(http_client.calls) == 1


def test_search_non_object_response_uses_same_error(tmp_path):
	"""非对象 JSON（如 ``[]``）与深嵌套走同一「平台响应无法解析」错误码。"""
	result, http_client = _invoke_search(
		tmp_path,
		_FakeResponse(payload=["not", "an", "object"]),
	)

	payload = _single_error_envelope(result)
	assert payload["error"]["code"] == "INTERNAL_ERROR"
	assert payload["error"]["recoverable"] is False
	assert len(http_client.calls) == 1


def test_fetch_ws_url_returns_none_on_recursion_error():
	"""CDP 探测响应深嵌套时与无效 JSON 一样返回 None，不把异常甩给调用方。"""
	mock_resp = MagicMock()
	mock_resp.json.side_effect = RecursionError("maximum recursion depth exceeded")

	with patch("httpx.get", return_value=mock_resp):
		assert BrowserSession._fetch_ws_url("http://127.0.0.1:9222") is None


def test_page_risk_tracker_treats_deep_nested_body_as_unparsed():
	"""页面动作观测到的平台响应正文深嵌套时按非 JSON 处理，不炸掉观测线程。"""
	tracker = _PageRiskResponseTracker()
	tracker._body_requests[1000] = "/wapi/zpgeek/search/joblist.json"
	msg = {"id": 1000, "result": {"body": '{"pad": [1]'}}

	with patch(
		"boss_agent_cli.api.browser_client.json.loads",
		side_effect=RecursionError("maximum recursion depth exceeded"),
	):
		assert tracker.on_message(msg) is None
	assert tracker.events == []
