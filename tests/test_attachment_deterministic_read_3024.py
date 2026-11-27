"""附件简历命令：写前只读确定性失败的错误分类（Issue #3024）。

``PlatformResponseInvalid`` / ``PlatformStatusRejected`` 只会在 ``write_possible=False``
的读请求上抛出；从同意 / 下载路径逃逸时，写请求必然没有发出。命令层不能把它们
兜成可重试的 ``NETWORK_ERROR``，更不能报成「同意结果未知」。
"""

import json
from unittest.mock import MagicMock, patch

import httpx
from click.testing import CliRunner

from boss_agent_cli.api import recruiter_endpoints as ep
from boss_agent_cli.api.recruiter_client import BossRecruiterClient
from boss_agent_cli.display import error_contract_for_code
from boss_agent_cli.main import cli
from boss_agent_cli.platforms.zhipin_recruiter import BossRecruiterPlatform

_REQUEST_MESSAGE = {
	"mid": 456,
	"from": {"uid": 123, "source": 0},
	"body": {"type": 7, "dialog": {"type": 2, "operated": False}},
}
_ATTACHMENT_MESSAGE = {
	"mid": 456,
	"from": {"uid": 123, "source": 0},
	"body": {
		"type": 12,
		"hyperLink": {
			"hyperLinkType": 1,
			"url": "bosszp://bosszhipin.app/openwith?type=openFile&id=resume-test&authType=1",
		},
	},
}
_FRIEND = {"uid": 123, "securityId": "test-security", "encryptUid": "test-geek"}


def _auth() -> MagicMock:
	auth = MagicMock()
	auth.get_token.return_value = {"cookies": {"wt2": "fake"}, "stoken": "s", "user_agent": "UA"}
	return auth


def _client() -> BossRecruiterClient:
	return BossRecruiterClient(_auth(), delay=(0, 0))


def _json(request: httpx.Request, payload: dict) -> httpx.Response:
	return httpx.Response(200, json=payload, request=request)


def _raw(request: httpx.Request, *, status: int = 200, content: bytes = b"") -> httpx.Response:
	return httpx.Response(status, content=content, request=request)


def _route(handler):
	"""按请求路径分发到测试提供的响应，并记录 method + path。"""
	calls: list[tuple[str, str]] = []

	def dispatch(self, method: str, url, **kwargs) -> httpx.Response:
		path = httpx.URL(url).path
		calls.append((method, path))
		return handler(httpx.Request(method, url), path)

	return dispatch, calls


def _invoke(module: str, args: list[str], client: BossRecruiterClient, handler) -> tuple[dict, list[tuple[str, str]]]:
	dispatch, calls = _route(handler)
	with patch(f"boss_agent_cli.commands.recruiter.{module}.AuthManager"), \
			patch(f"boss_agent_cli.commands.recruiter.{module}.get_recruiter_platform_instance") as factory, \
			patch.object(httpx.Client, "request", dispatch):
		factory.return_value.__enter__.return_value = BossRecruiterPlatform(client)
		result = CliRunner().invoke(cli, args)
	assert result.exception is None or isinstance(result.exception, SystemExit), result.exception
	return json.loads(result.output), calls


def _accept(handler, tmp_path) -> tuple[dict, list[tuple[str, str]]]:
	args = [
		"--data-dir", str(tmp_path / "data"), "--json",
		"hr", "accept-resume", "123", "--message-id", "456", "--yes",
	]
	return _invoke("accept_resume", args, _client(), handler)


def _download(handler, tmp_path) -> tuple[dict, list[tuple[str, str]]]:
	args = [
		"--data-dir", str(tmp_path / "data"), "--json",
		"hr", "download-resume", "123", "--message-id", "456",
		"--output", str(tmp_path / "resumes" / "resume.pdf"),
	]
	return _invoke("download_resume", args, _client(), handler)


def _assert_no_write_unknown(payload: dict) -> None:
	details = payload["error"].get("details") or {}
	assert details.get("outcome_unknown") is not True
	assert details.get("replay_safe") is not False


# ── accept-resume ─────────────────────────────────────────


def test_accept_history_parse_failure_is_deterministic_not_unknown(tmp_path):
	def handler(request, path):
		assert path == httpx.URL(ep.BOSS_CHAT_HISTORY_URL).path
		return _raw(request, content=b"<html>login</html>")

	payload, calls = _accept(handler, tmp_path)

	assert payload["ok"] is False
	assert payload["error"]["code"] == "INTERNAL_ERROR"
	assert payload["error"]["recoverable"] is False
	_assert_no_write_unknown(payload)
	# 只有写前只读请求，同意 POST 从未发出。
	assert calls == [("GET", httpx.URL(ep.BOSS_CHAT_HISTORY_URL).path)]


def test_accept_history_parameter_rejection_is_invalid_param(tmp_path):
	def handler(request, path):
		return _raw(request, status=400, content=b'{"code": 400}')

	payload, calls = _accept(handler, tmp_path)

	assert payload["error"]["code"] == "INVALID_PARAM"
	assert payload["error"]["recoverable"] is error_contract_for_code("INVALID_PARAM")[0]
	_assert_no_write_unknown(payload)
	assert calls == [("GET", httpx.URL(ep.BOSS_CHAT_HISTORY_URL).path)]


def test_accept_friend_detail_parse_failure_is_deterministic_not_unknown(tmp_path):
	def handler(request, path):
		if path == httpx.URL(ep.BOSS_CHAT_HISTORY_URL).path:
			return _json(request, {"code": 0, "zpData": {"messages": [_REQUEST_MESSAGE]}})
		if path == httpx.URL(ep.BOSS_FRIEND_DETAIL_URL).path:
			return _raw(request, content=b"not-json")
		raise AssertionError(f"unexpected request {path}")

	payload, calls = _accept(handler, tmp_path)

	assert payload["error"]["code"] == "INTERNAL_ERROR"
	assert payload["error"]["recoverable"] is False
	_assert_no_write_unknown(payload)
	assert calls == [
		("GET", httpx.URL(ep.BOSS_CHAT_HISTORY_URL).path),
		("POST", httpx.URL(ep.BOSS_FRIEND_DETAIL_URL).path),
	]


def test_accept_precheck_read_timeout_is_retryable_without_unknown_marker(tmp_path):
	def handler(request, path):
		raise httpx.ReadTimeout("read timed out")

	payload, calls = _accept(handler, tmp_path)

	assert payload["error"]["code"] == "NETWORK_ERROR"
	assert payload["error"]["recoverable"] is True
	_assert_no_write_unknown(payload)
	assert calls == [("GET", httpx.URL(ep.BOSS_CHAT_HISTORY_URL).path)]


def test_accept_precheck_risk_still_stops_without_release(tmp_path):
	def handler(request, path):
		return _json(request, {"code": 36, "message": "账号存在风险"})

	payload, calls = _accept(handler, tmp_path)

	assert payload["error"]["code"] == "ACCOUNT_RISK"
	assert payload["error"]["recoverable"] is False
	assert calls == [("GET", httpx.URL(ep.BOSS_CHAT_HISTORY_URL).path)]


# ── download-resume ───────────────────────────────────────


def _download_routes(*, history=None, friend=None, preview=None):
	def handler(request, path):
		if path == httpx.URL(ep.BOSS_CHAT_HISTORY_URL).path:
			return history(request)
		if path == httpx.URL(ep.BOSS_FRIEND_DETAIL_URL).path:
			return friend(request)
		if path == httpx.URL(ep.BOSS_RESUME_PREVIEW_CHECK_URL).path:
			return preview(request)
		raise AssertionError(f"unexpected request {path}")

	return handler


def _valid_history(request):
	return _json(request, {"code": 0, "zpData": {"messages": [_ATTACHMENT_MESSAGE]}})


def _valid_friend(request):
	return _json(request, {"code": 0, "zpData": {"friendList": [_FRIEND]}})


def test_download_history_parse_failure_is_deterministic_not_network(tmp_path):
	payload, calls = _download(_download_routes(history=lambda request: _raw(request, content=b"<html>")), tmp_path)

	assert payload["error"]["code"] == "INTERNAL_ERROR"
	assert payload["error"]["recoverable"] is False
	assert calls == [("GET", httpx.URL(ep.BOSS_CHAT_HISTORY_URL).path)]
	assert not (tmp_path / "resumes" / "resume.pdf").exists()


def test_download_history_parameter_rejection_is_invalid_param(tmp_path):
	payload, calls = _download(
		_download_routes(history=lambda request: _raw(request, status=400, content=b'{"code": 400}')),
		tmp_path,
	)

	assert payload["error"]["code"] == "INVALID_PARAM"
	assert payload["error"]["recoverable"] is error_contract_for_code("INVALID_PARAM")[0]
	assert calls == [("GET", httpx.URL(ep.BOSS_CHAT_HISTORY_URL).path)]
	assert not (tmp_path / "resumes" / "resume.pdf").exists()


def test_download_friend_detail_parse_failure_is_deterministic(tmp_path):
	payload, calls = _download(
		_download_routes(history=_valid_history, friend=lambda request: _raw(request, content=b"not-json")),
		tmp_path,
	)

	assert payload["error"]["code"] == "INTERNAL_ERROR"
	assert payload["error"]["recoverable"] is False
	assert calls == [
		("GET", httpx.URL(ep.BOSS_CHAT_HISTORY_URL).path),
		("POST", httpx.URL(ep.BOSS_FRIEND_DETAIL_URL).path),
	]
	assert not (tmp_path / "resumes" / "resume.pdf").exists()


def test_download_preview_parse_failure_is_deterministic(tmp_path):
	payload, calls = _download(
		_download_routes(
			history=_valid_history,
			friend=_valid_friend,
			preview=lambda request: _raw(request, content=b"not-json"),
		),
		tmp_path,
	)

	assert payload["error"]["code"] == "INTERNAL_ERROR"
	assert payload["error"]["recoverable"] is False
	assert calls == [
		("GET", httpx.URL(ep.BOSS_CHAT_HISTORY_URL).path),
		("POST", httpx.URL(ep.BOSS_FRIEND_DETAIL_URL).path),
		("GET", httpx.URL(ep.BOSS_RESUME_PREVIEW_CHECK_URL).path),
	]
	assert not (tmp_path / "resumes" / "resume.pdf").exists()


def test_download_transient_server_error_remains_retryable(tmp_path):
	payload, _ = _download(
		_download_routes(history=lambda request: _raw(request, status=503, content=b"busy")),
		tmp_path,
	)

	assert payload["error"]["code"] == "NETWORK_ERROR"
	assert payload["error"]["recoverable"] is True
