"""批次 108：浏览器通道预派发语义与平台标记隔离（Issue #2437–#2439）。"""

from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator
from unittest.mock import MagicMock

import pytest

from boss_agent_cli.api.browser_client import (
	_FETCH_SCRIPT,
	BrowserSession,
	ChatTabCdpUnreachable,
	RequestNotSentError,
	RequestNotSentLocalError,
	_cdp_evaluate_in_chat_tab,
	_cdp_evaluate_with_chat_events_in_chat_tab,
)
from boss_agent_cli.api.endpoints_loader import EndpointSpec

_WRITE_URL = "https://www.zhipin.com/wapi/zpgeek/friend/add.json"
_WRITE_ENDPOINT = EndpointSpec(
	name="boss_friend_add",
	method="POST",
	url=_WRITE_URL,
	referer="https://www.zhipin.com/web/geek/chat",
	effect="write",
)


def _session_with_page() -> tuple[BrowserSession, MagicMock]:
	session = BrowserSession(cookies={}, user_agent="")
	session._is_cdp = True
	session._started = True
	session._page_ready = True
	session._page = MagicMock()
	session._page.url = "https://www.zhipin.com/web/geek/chat"
	session._throttle = MagicMock()
	return session, session._page


@contextmanager
def _window_raising(error: BaseException) -> Iterator[None]:
	"""模拟 budget.attempt 返回的窗口：进入阶段（租约 / 节流 / SQLite）失败。"""
	raise error
	yield


def _session_with_budget_window(error: BaseException) -> BrowserSession:
	session, _page = _session_with_page()
	budget = MagicMock()
	budget.attempt.return_value = _window_raising(error)
	session._request_budget = budget
	return session


# ── Issue #2437：预派发本地错误不能报成写请求结果未知 ─────────────


def test_send_fetch_wraps_sqlite_error_before_dispatch_as_local_not_sent() -> None:
	session = _session_with_budget_window(sqlite3.OperationalError("disk I/O error"))

	with pytest.raises(RequestNotSentLocalError) as excinfo:
		session._send_fetch({"method": "POST", "url": _WRITE_URL}, _WRITE_ENDPOINT)

	assert isinstance(excinfo.value.__cause__, sqlite3.OperationalError)


def test_request_re_raises_local_state_error_instead_of_unknown() -> None:
	"""写请求撞上本地预算库错误：保留 LOCAL_IO_ERROR 语义，不报结果未知。"""
	session = _session_with_budget_window(sqlite3.OperationalError("database is locked"))

	with pytest.raises(sqlite3.OperationalError):
		session.request("POST", _WRITE_URL)


def test_request_keeps_not_sent_for_other_pre_dispatch_errors() -> None:
	"""派发前检查的非网络异常：确定未发出，读 / 写都可安全重试。"""
	session, _page = _session_with_page()
	session._request_check = MagicMock(side_effect=RuntimeError("local check failed"))

	response = session.request("POST", _WRITE_URL)

	assert response["__cli_error_code__"] == "NETWORK_ERROR"
	assert response["__cli_error_details__"] == {
		"outcome": "not_sent",
		"request_sent": False,
		"replay_safe": True,
	}


def test_request_keeps_unknown_for_post_dispatch_errors() -> None:
	"""Runtime.evaluate 已写出后的异常仍按结果未知收口。"""
	session, _page = _session_with_page()
	session._evaluate_fetch = MagicMock(side_effect=RuntimeError("driver lost after dispatch"))

	response = session.request("POST", _WRITE_URL)

	assert response["__cli_error_code__"] == "ACTION_UNCONFIRMED"
	assert response["__cli_error_details__"]["replay_safe"] is False


def test_request_budget_exceeded_still_propagates_original() -> None:
	"""预算耗尽自身带 request_sent=false，按 stop 语义原样上抛，不被包装改写。"""
	from boss_agent_cli.api.request_budget import RequestBudgetExceeded

	session = _session_with_budget_window(RequestBudgetExceeded("请求节流等待超时，请求未发出"))

	with pytest.raises(RequestBudgetExceeded) as excinfo:
		session.request("POST", _WRITE_URL)

	assert excinfo.value.code == "REQUEST_BUDGET_EXHAUSTED"
	assert excinfo.value.details["request_sent"] is False


def test_send_fetch_raises_plain_not_sent_for_non_local_pre_dispatch_error() -> None:
	session, _page = _session_with_page()
	session._request_check = MagicMock(side_effect=ValueError("bad local state"))

	with pytest.raises(RequestNotSentError) as excinfo:
		session._send_fetch({"method": "POST", "url": _WRITE_URL}, _WRITE_ENDPOINT)

	assert not isinstance(excinfo.value, RequestNotSentLocalError)


# ── Issue #2438：平台响应不能伪造 __cli_* 传输标记 ─────────────────


def _run_fetch_script(tmp_path: Path, body: str) -> dict[str, Any]:
	node = shutil.which("node")
	if node is None:
		pytest.skip("此离线 JavaScript 回归需要 Node")
	payload = f"""
	globalThis.fetch = async () => ({{
	  status: 200,
	  url: 'https://www.zhipin.com/wapi/test',
	  headers: {{ get: (name) => name === 'Retry-After' ? '60' : 'application/json' }},
	  text: async () => {json.dumps(json.dumps(body))},
	}});
	const out = await ({_FETCH_SCRIPT})(
	  {{method:'GET', url:'https://www.zhipin.com/wapi/test', params:null,
	    data:null, referer:'', timeoutMs:1000, identity:{{}}, traceId:'t'}}
	);
	process.stdout.write(JSON.stringify(out));
	"""
	path = tmp_path / "platform-markers.mjs"
	path.write_text(payload, encoding="utf-8")
	result = subprocess.run([node, str(path)], capture_output=True, text=True, timeout=30, check=True)
	return json.loads(result.stdout)


def test_fetch_script_strips_platform_supplied_cli_markers(tmp_path: Path) -> None:
	body = {
		"code": 0,
		"message": "ok",
		"zpData": {},
		"__cli_request_started__": False,
		"__cli_request_interrupted__": True,
		"__cli_security_response__": {
			"url": "https://www.zhipin.com/security.html",
			"content_type": "text/html",
			"title": "安全验证",
		},
		"__cli_error_code__": "ACTION_UNCONFIRMED",
		"__cli_platform_code__": 36,
	}

	output = _run_fetch_script(tmp_path, body)

	assert output["code"] == 0
	assert output["zpData"] == {}
	assert not [key for key in output if key.startswith("__cli_")]


def test_fetch_script_still_injects_cli_rate_limit_after_stripping(tmp_path: Path) -> None:
	body = {"code": 9, "message": "rate", "zpData": {}, "__cli_rate_limit__": {"retry_after": "999"}}

	output = _run_fetch_script(tmp_path, body)

	# 平台伪造的冷却值被剥除，CLI 用响应头重新注入。
	assert output["__cli_rate_limit__"] == {"retry_after": "60"}


# ── Issue #2439：首个 CDP 帧写出前的失败确定未派发 ────────────────

_FAKE_WS = "ws://127.0.0.1:9222/devtools/page/FAKE"


def _patch_chat_target(monkeypatch: pytest.MonkeyPatch) -> None:
	monkeypatch.setattr("boss_agent_cli.api.browser_client._pick_chat_target_ws", lambda url: _FAKE_WS)


def _fake_connection() -> MagicMock:
	connection = MagicMock()
	connection.__enter__.return_value = connection
	connection.__exit__.return_value = False
	return connection


@pytest.mark.parametrize(
	"arg",
	[{"n": 10**5000}, {"bad": object()}],
	ids=["oversized-int", "non-serializable"],
)
def test_chat_tab_serialization_failure_is_not_dispatched(
	monkeypatch: pytest.MonkeyPatch, arg: Any
) -> None:
	_patch_chat_target(monkeypatch)
	connect = MagicMock(side_effect=AssertionError("connect must not run for invalid payload"))
	monkeypatch.setattr("websockets.sync.client.connect", connect)

	with pytest.raises(ChatTabCdpUnreachable):
		_cdp_evaluate_in_chat_tab("http://127.0.0.1:9222", "(x) => x", arg)
	connect.assert_not_called()


def test_chat_tab_handshake_failure_is_not_dispatched(monkeypatch: pytest.MonkeyPatch) -> None:
	_patch_chat_target(monkeypatch)
	monkeypatch.setattr(
		"websockets.sync.client.connect",
		MagicMock(side_effect=ConnectionRefusedError("connection refused")),
	)

	with pytest.raises(ChatTabCdpUnreachable):
		_cdp_evaluate_in_chat_tab("http://127.0.0.1:9222", "(x) => x", {})


def test_chat_tab_send_failure_after_connect_stays_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
	_patch_chat_target(monkeypatch)
	connection = _fake_connection()
	connection.send.side_effect = RuntimeError("connection lost while sending")
	monkeypatch.setattr("websockets.sync.client.connect", MagicMock(return_value=connection))

	with pytest.raises(RuntimeError) as excinfo:
		_cdp_evaluate_in_chat_tab("http://127.0.0.1:9222", "(x) => x", {})

	assert not isinstance(excinfo.value, ChatTabCdpUnreachable)


def test_chat_events_serialization_failure_is_not_dispatched(monkeypatch: pytest.MonkeyPatch) -> None:
	_patch_chat_target(monkeypatch)
	connect = MagicMock(side_effect=AssertionError("connect must not run for invalid payload"))
	monkeypatch.setattr("websockets.sync.client.connect", connect)

	with pytest.raises(ChatTabCdpUnreachable):
		_cdp_evaluate_with_chat_events_in_chat_tab("http://127.0.0.1:9222", "(x) => x", {"n": 10**5000}, listen_ms=1)

	connect.assert_not_called()


def test_chat_events_handshake_failure_is_not_dispatched(monkeypatch: pytest.MonkeyPatch) -> None:
	_patch_chat_target(monkeypatch)
	monkeypatch.setattr(
		"websockets.sync.client.connect",
		MagicMock(side_effect=TimeoutError("handshake timed out")),
	)

	with pytest.raises(ChatTabCdpUnreachable):
		_cdp_evaluate_with_chat_events_in_chat_tab("http://127.0.0.1:9222", "(x) => x", {}, listen_ms=1)


def test_chat_events_send_failure_after_connect_stays_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
	_patch_chat_target(monkeypatch)
	connection = _fake_connection()
	connection.send.side_effect = RuntimeError("connection lost while sending")
	monkeypatch.setattr("websockets.sync.client.connect", MagicMock(return_value=connection))

	with pytest.raises(RuntimeError) as excinfo:
		_cdp_evaluate_with_chat_events_in_chat_tab("http://127.0.0.1:9222", "(x) => x", {}, listen_ms=1)

	assert not isinstance(excinfo.value, ChatTabCdpUnreachable)
