"""批次 94 审计修复：浏览器通道的发送前失败识别与有界扫描（Issue #2379–#2381）。"""

from __future__ import annotations

import json
import shutil
import subprocess
from unittest.mock import MagicMock

import pytest

from boss_agent_cli.api.browser_client import (
	_FETCH_SCRIPT,
	BrowserSession,
	RequestNotSentError,
	_carve_utf8_bits,
	_is_context_destroyed_error,
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
_TARGET_CLOSED_TEXT = "Target page, context or browser has been closed"


def _session_with_page() -> tuple[BrowserSession, MagicMock]:
	session = BrowserSession(cookies={}, user_agent="")
	session._is_cdp = True
	session._started = True
	session._page_ready = True
	session._page = MagicMock()
	session._page.url = "https://www.zhipin.com/web/geek/chat"
	session._throttle = MagicMock()
	return session, session._page


# ── Issue #2379：页签关闭文案命中上下文销毁标记 ─────────────────────


def test_target_closed_error_text_is_context_destroyed() -> None:
	assert _is_context_destroyed_error(RuntimeError(_TARGET_CLOSED_TEXT))
	assert _is_context_destroyed_error(RuntimeError("Target page, context or browser has been closed."))
	assert not _is_context_destroyed_error(RuntimeError("Evaluation failed: ReferenceError"))


def test_request_maps_closed_session_to_retryable_not_sent() -> None:
	"""写请求在 CDP 会话建立阶段撞上页签关闭：确定未发出，可重试。"""
	session, _page = _session_with_page()
	session._context = MagicMock()
	session._context.new_cdp_session.side_effect = RuntimeError(_TARGET_CLOSED_TEXT)

	response = session.request("POST", _WRITE_URL)

	assert response["code"] == -1
	assert response["__cli_error_code__"] == "NETWORK_ERROR"
	assert response["__cli_error_details__"] == {
		"outcome": "not_sent",
		"request_sent": False,
		"replay_safe": True,
	}


# ── Issue #2380：发送前失败回传 request_sent=false ─────────────────


def test_evaluate_fetch_wraps_session_failure_as_not_sent() -> None:
	session, _page = _session_with_page()
	session._context = MagicMock()
	session._context.new_cdp_session.side_effect = RuntimeError(_TARGET_CLOSED_TEXT)

	with pytest.raises(RequestNotSentError):
		session._evaluate_fetch({"method": "GET", "url": "https://example.invalid/"})

	session._context.new_cdp_session.return_value.send.assert_not_called()


@pytest.mark.parametrize(
	"payload",
	[{"n": 10**5000}, {"bad": object()}],
	ids=["oversized-int", "non-serializable"],
)
def test_evaluate_fetch_wraps_serialization_failure_as_not_sent(payload: dict[str, object]) -> None:
	"""载荷在 Python 侧序列化失败：CDP 消息尚未写出，确定未发出。"""
	session, _page = _session_with_page()
	session._context = MagicMock()
	cdp = session._context.new_cdp_session.return_value

	with pytest.raises(RequestNotSentError):
		session._evaluate_fetch(payload)

	cdp.send.assert_not_called()
	cdp.detach.assert_called_once()


def test_send_fetch_turns_started_false_into_not_sent() -> None:
	session, _page = _session_with_page()
	session._context = MagicMock()
	session._evaluate_fetch = MagicMock(
		return_value={
			"code": -1,
			"__cli_request_interrupted__": True,
			"__cli_request_started__": False,
		}
	)

	response = session._send_fetch({"method": "POST"}, _WRITE_ENDPOINT)

	assert response["__cli_error_code__"] == "NETWORK_ERROR"
	assert response["__cli_error_details__"]["request_sent"] is False
	assert response["__cli_error_details__"]["replay_safe"] is True


@pytest.mark.parametrize("started", [None, True, "false", 0], ids=["missing", "true", "str", "int"])
def test_send_fetch_keeps_unknown_when_started_evidence_is_not_false(started: object) -> None:
	"""只有布尔 False 才是「未发出」证据；缺少证据或类型漂移仍按结果未知收口。"""
	session, _page = _session_with_page()
	session._context = MagicMock()
	result: dict[str, object] = {"code": -1, "__cli_request_interrupted__": True}
	if started is not None:
		result["__cli_request_started__"] = started
	session._evaluate_fetch = MagicMock(return_value=result)

	response = session._send_fetch({"method": "POST"}, _WRITE_ENDPOINT)

	assert response["__cli_error_code__"] == "ACTION_UNCONFIRMED"
	assert response["__cli_error_details__"]["replay_safe"] is False


def test_send_fetch_does_not_leak_started_marker_on_success() -> None:
	session, _page = _session_with_page()
	session._context = MagicMock()
	session._evaluate_fetch = MagicMock(
		return_value={"code": 0, "zpData": {"ok": True}, "__cli_request_started__": True}
	)

	response = session._send_fetch({"method": "GET"}, None)

	assert response == {"code": 0, "zpData": {"ok": True}}


def test_fetch_script_reports_started_evidence(tmp_path) -> None:
	node = shutil.which("node")
	if node is None:
		pytest.skip("此离线 JavaScript 回归需要 Node")
	probe = f"""
	globalThis.URLSearchParams = undefined;
	let fetchCalled = false;
	globalThis.fetch = async () => {{ fetchCalled = true; throw new Error('unreachable'); }};
	const beforeStart = await ({_FETCH_SCRIPT})(
		{{method:'GET', url:'https://www.zhipin.com/wapi/test', params:{{q:'1'}},
		  data:null, referer:'', timeoutMs:100, identity:{{}}, traceId:'t'}}
	);
	globalThis.URLSearchParams = (class {{ append() {{}} toString() {{ return ''; }} }});
	globalThis.fetch = async () => {{ fetchCalled = true; throw new Error('network down'); }};
	const afterStart = await ({_FETCH_SCRIPT})(
		{{method:'GET', url:'https://www.zhipin.com/wapi/test', params:null,
		  data:null, referer:'', timeoutMs:100, identity:{{}}, traceId:'t'}}
	);
	process.stdout.write(JSON.stringify({{beforeStart, afterStart, fetchCalled}}));
	"""
	path = tmp_path / "started-evidence.mjs"
	path.write_text(probe, encoding="utf-8")
	result = subprocess.run([node, str(path)], capture_output=True, text=True, timeout=30, check=True)
	output = json.loads(result.stdout)

	# fetch 调用前构造失败：明确回传 started=false。
	assert output["beforeStart"]["__cli_request_interrupted__"] is True
	assert output["beforeStart"]["__cli_request_started__"] is False
	# fetch 已开始后失败：started=true，结果未知。
	assert output["afterStart"]["__cli_request_interrupted__"] is True
	assert output["afterStart"]["__cli_request_started__"] is True
	assert output["fetchCalled"] is True


# ── Issue #2381：UTF-8 片段扫描有界 ────────────────────────────────


def test_carve_utf8_bits_does_not_scan_past_limit() -> None:
	payload = b"\x00" * 9000 + "可打印文本很长".encode()
	assert _carve_utf8_bits(payload) == []


def test_carve_utf8_bits_stops_after_fragment_limit() -> None:
	payload = b"\x00".join([b"abcdef"] * 20)

	assert _carve_utf8_bits(payload) == ["abcdef"] * 10
	assert _carve_utf8_bits(payload, limit=3) == ["abcdef"] * 3


def test_carve_utf8_bits_large_binary_frame_is_bounded() -> None:
	# 1MB 全零帧：扫描上限 8KB 内没有可打印片段，直接返回空列表，
	# 不会按 78µs/字节 的旧路径卡住数十秒（Issue #2381）。
	assert _carve_utf8_bits(b"\x00" * (1024 * 1024)) == []
