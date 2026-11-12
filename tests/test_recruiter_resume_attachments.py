from io import BytesIO
import json
import os
import shlex
from pathlib import Path
from unittest.mock import MagicMock, patch
from zipfile import ZipFile

from click.testing import CliRunner
import httpx
import pytest

from boss_agent_cli.api import recruiter_endpoints as ep
from boss_agent_cli.api.client import AccountRiskError, EnvironmentRiskError, PlatformRiskError
from boss_agent_cli.api.recruiter_client import BossRecruiterClient, RecruiterAuthError
from boss_agent_cli.api.recruiter_resume import ResumeValidationError, attachment_params, save_resume
from boss_agent_cli.auth.manager import AuthRequired
from boss_agent_cli.schema.data import SCHEMA_DATA
from boss_agent_cli.display import error_contract_for_code
from boss_agent_cli.main import cli
from boss_agent_cli.mcp_args import _build_args
from boss_agent_cli.platforms.zhipin_recruiter import BossRecruiterPlatform


def _hint(command: str) -> str:
	"""默认数据目录下的招聘者后继命令。"""
	return f"boss --data-dir {(Path.home() / '.boss-agent').resolve()} {command}"


def _assert_download_error_contract(payload: dict, code: str) -> None:
	error = payload["error"]
	assert error["recoverable"] is error_contract_for_code(code)[0]
	if code in {"AUTH_REQUIRED", "TOKEN_REFRESH_FAILED"}:
		assert shlex.split(error["recovery_action"]) == ["boss", "--data-dir", str((Path.home() / ".boss-agent").resolve()), "login"]
		assert payload["hints"]["next_actions"] == [error["recovery_action"]]
	else:
		assert error["recovery_action"] == error_contract_for_code(code)[1]


def test_removed_experiments_do_not_remove_native_or_attachment_commands():
	result = CliRunner().invoke(cli, ["hr", "--help"])
	assert result.exit_code == 0
	for command in ("request-resume-http", "reply-mqtt"):
		assert command not in result.output
	for command in ("request-resume", "reply", "accept-resume", "download-resume", "greet"):
		assert command in result.output
	for method in ("request_resume_http_by_friend", "reply_mqtt_by_friend"):
		assert not hasattr(BossRecruiterClient, method)
		assert not hasattr(BossRecruiterPlatform, method)


def _client(attachment: bool = False) -> BossRecruiterClient:
	client = object.__new__(BossRecruiterClient)
	body = {"type": 7, "dialog": {"type": 2, "operated": False}}
	if attachment:
		body = {"type": 12, "hyperLink": {"hyperLinkType": 1, "url": "bosszp://bosszhipin.app/openwith?type=openFile&id=resume-test&authType=1"}}
	client.chat_history = MagicMock(return_value={"code": 0, "zpData": {"messages": [{"mid": 456, "from": {"uid": 123, "source": 0}, "body": body}]}})
	client.friend_detail = MagicMock(return_value={"code": 0, "zpData": {"friendList": [{"uid": 123, "securityId": "test-security", "encryptUid": "test-geek"}]}})
	client._request = MagicMock(return_value={"code": 0, "zpData": {"status": 0}})
	client._browser_request = MagicMock()
	client._throttle = MagicMock()
	return client


def test_accept_uses_current_conversation_and_single_post():
	client = _client()
	assert client.accept_resume_by_friend(123, 456)["zpData"]["status"] == 0
	client.chat_history.assert_called_once_with(123, count=100, max_msg_id=457, retry=False)
	client.friend_detail.assert_called_once_with([123], retry=False)
	client._request.assert_called_once_with("POST", ep.BOSS_EXCHANGE_ACCEPT_URL, data={"mid": 456, "type": 3, "securityId": "test-security"}, retry=False, follow_redirects=False)
	client._browser_request.assert_not_called()


@pytest.mark.parametrize("change", ["wrong_sender", "wrong_mid", "phone", "processed", "unknown", "duplicate", "malformed"])
def test_accept_refuses_wrong_or_processed_message(change):
	client = _client()
	messages = client.chat_history.return_value["zpData"]["messages"]
	message = messages[0]
	if change == "wrong_sender":
		message["from"]["uid"] = 999
	elif change == "wrong_mid":
		message["mid"] = 789
	elif change == "phone":
		message["body"]["dialog"]["type"] = 0
	elif change == "processed":
		message["body"]["dialog"]["operated"] = True
	elif change == "unknown":
		message["body"]["dialog"].pop("operated")
	elif change == "duplicate":
		messages.append(message.copy())
	else:
		message["body"] = "unexpected"
	with pytest.raises(ResumeValidationError):
		client.accept_resume_by_friend(123, 456)
	client._request.assert_not_called()


@pytest.mark.parametrize("friends", [[], [{"uid": 999, "securityId": "wrong"}], [{"uid": 123}], [{"uid": 123, "friendSource": 1, "securityId": "wrong"}]])
def test_accept_never_uses_unrelated_or_incomplete_friend(friends):
	client = _client()
	client.friend_detail.return_value["zpData"]["friendList"] = friends
	with pytest.raises(ResumeValidationError):
		client.accept_resume_by_friend(123, 456)
	client._request.assert_not_called()


@pytest.mark.parametrize("method", ["chat_history", "friend_detail"])
def test_accept_stops_after_read_auth_failure(method):
	client = _client()
	getattr(client, method).return_value = {"code": 7}
	assert client.accept_resume_by_friend(123, 456) == {"code": 7}
	client._request.assert_not_called()


def _download_client(content=b"%PDF-1.7\nfixture", status=200):
	client = _client(attachment=True)
	client._request.return_value = {"code": 0, "zpData": {"isResumeVisible": True, "isCanPreview": False, "expired": False, "d": "temporary-test"}}
	requests = []
	def handler(request):
		requests.append(request)
		return httpx.Response(status, content=content, headers={"Location": "https://example.invalid/private"})
	transport = httpx.Client(transport=httpx.MockTransport(handler))
	client._get_client = MagicMock(return_value=transport)
	return client, transport, requests


def test_download_checks_permission_and_only_uses_fixed_host(tmp_path):
	client, transport, requests = _download_client()
	output = tmp_path / "resume.pdf"
	with transport:
		result = client.download_resume_by_friend(123, 456, output)
	assert result["code"] == 0
	assert output.read_bytes() == b"%PDF-1.7\nfixture"
	assert output.stat().st_mode & 0o777 == 0o600
	assert len(requests) == 1
	assert requests[0].url.host == "docdownload.zhipin.com"
	assert requests[0].url.params["d"] == "temporary-test"
	assert requests[0].url.params["id"] == "resume-test"
	assert "temporary-test" not in json.dumps(result)
	client._request.assert_called_once_with("GET", ep.BOSS_RESUME_PREVIEW_CHECK_URL, params={"geekId": "test-geek", "id": "resume-test", "authType": "1"}, retry=False, follow_redirects=False)
	client._browser_request.assert_not_called()


@pytest.mark.parametrize("detail", [{}, {"isResumeVisible": False}, {"isResumeVisible": True, "expired": True}, {"isResumeVisible": "true"}])
def test_download_denied_before_binary_request(tmp_path, detail):
	client, transport, requests = _download_client()
	client._request.return_value["zpData"] = detail
	with transport, pytest.raises(ResumeValidationError):
		client.download_resume_by_friend(123, 456, tmp_path / "resume.pdf")
	assert not requests
	assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("status,content", [(302, b""), (200, b"<html>login</html>"), (200, b'{"code":7}'), (200, b"")])
def test_download_rejects_redirects_and_non_files(tmp_path, status, content):
	client, transport, requests = _download_client(content, status)
	with transport, pytest.raises(ResumeValidationError):
		client.download_resume_by_friend(123, 456, tmp_path / "resume.pdf")
	assert len(requests) == 1
	assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("status", [401, 403])
def test_download_binary_auth_failure_is_not_invalid_param(tmp_path, status):
	client, transport, requests = _download_client(b"private-error", status)
	with transport, pytest.raises(AuthRequired):
		client.download_resume_by_friend(123, 456, tmp_path / "resume.pdf")
	assert len(requests) == 1
	client._throttle.mark.assert_called_once()
	client._browser_request.assert_not_called()
	assert not list(tmp_path.iterdir())


def test_download_does_not_accept_a_request_message(tmp_path):
	client = _client()
	with pytest.raises(ResumeValidationError):
		client.download_resume_by_friend(123, 456, tmp_path / "resume.pdf")
	client._request.assert_not_called()


def test_existing_output_is_preserved_without_platform_requests(tmp_path):
	client = _client(attachment=True)
	output = tmp_path / "resume.pdf"
	output.write_bytes(b"original")
	with pytest.raises(FileExistsError):
		client.download_resume_by_friend(123, 456, output)
	assert output.read_bytes() == b"original"
	client.chat_history.assert_not_called()


def test_save_resume_no_overwrite_and_no_temporary_leftovers(tmp_path):
	output = tmp_path / "resume.pdf"
	output.write_bytes(b"original")
	with pytest.raises(FileExistsError):
		save_resume(httpx.Response(200, content=b"%PDF-1.7"), output)
	assert output.read_bytes() == b"original"
	assert list(tmp_path.iterdir()) == [output]


def test_save_resume_does_not_need_hard_links(tmp_path):
	output = tmp_path / "resume.pdf"
	with patch("boss_agent_cli.api.recruiter_resume.os.link", side_effect=OSError("unsupported")) as link:
		save_resume(httpx.Response(200, content=b"%PDF-1.7"), output)
	link.assert_not_called()
	assert output.read_bytes() == b"%PDF-1.7"
	assert output.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("error_type", [OSError, KeyboardInterrupt])
def test_save_resume_removes_partial_file_and_closes_descriptor(tmp_path, error_type):
	output = tmp_path / "resume.pdf"
	descriptors = []
	real_open = open

	def failing_open(fd, *args, **kwargs):
		descriptors.append(fd)
		stream = real_open(fd, *args, **kwargs)
		stream.write(b"partial")
		stream.close()
		raise error_type("write interrupted")

	with patch("boss_agent_cli.api.recruiter_resume.open", side_effect=failing_open), pytest.raises(error_type):
		save_resume(httpx.Response(200, content=b"%PDF-1.7"), output)
	assert not list(tmp_path.iterdir())
	with pytest.raises(OSError):
		os.fstat(descriptors[0])


def test_save_resume_never_follows_existing_symlink(tmp_path):
	target = tmp_path / "original.pdf"
	target.write_bytes(b"original")
	output = tmp_path / "resume.pdf"
	output.symlink_to(target)
	with pytest.raises(FileExistsError):
		save_resume(httpx.Response(200, content=b"%PDF-1.7"), output)
	assert output.is_symlink()
	assert target.read_bytes() == b"original"


def test_save_resume_size_and_extension_limits(tmp_path):
	with patch("boss_agent_cli.api.recruiter_resume.MAX_RESUME_BYTES", 4), pytest.raises(ResumeValidationError):
		save_resume(httpx.Response(200, content=b"%PDF-1.7"), tmp_path / "resume.pdf")
	with pytest.raises(ResumeValidationError):
		save_resume(httpx.Response(200, content=b"%PDF-1.7"), tmp_path / "resume.docx")
	assert not list(tmp_path.iterdir())


def test_save_resume_docx_not_arbitrary_zip(tmp_path):
	content = BytesIO()
	with ZipFile(content, "w") as archive:
		archive.writestr("[Content_Types].xml", "fixture")
		archive.writestr("word/document.xml", "fixture")
	result = save_resume(httpx.Response(200, content=content.getvalue()), tmp_path / "resume.docx")
	assert result["format"] == "docx"


@pytest.mark.parametrize("query,expected", [("type=selectResumePreviewUrl&encryptId=enc&authType=0", {"id": "enc", "authType": "0"}), ("type=openFile&id=plain", {"id": "plain"})])
def test_attachment_link_mapping(query, expected):
	assert attachment_params({"body": {"hyperLink": {"hyperLinkType": 9, "url": "bosszp://bosszhipin.app/openwith?" + query}}}) == expected


def test_duplicate_attachment_params_are_rejected():
	with pytest.raises(ResumeValidationError):
		attachment_params({"body": {"hyperLink": {"hyperLinkType": 1, "url": "https://example.invalid/?id=a&id=b"}}})


def _invoke(*args):
	return CliRunner().invoke(cli, ["--json", "hr", *args])


def test_accept_confirmation_and_preview_do_not_access_auth():
	with patch("boss_agent_cli.commands.recruiter.accept_resume.AuthManager") as auth:
		result = _invoke("accept-resume", "123", "--message-id", "456")
		error = json.loads(result.output)["error"]
		assert error["code"] == "CONFIRMATION_REQUIRED"
		assert (error["recoverable"], error["recovery_action"]) == error_contract_for_code("CONFIRMATION_REQUIRED")
		result = _invoke("accept-resume", "123", "--message-id", "456", "--dry-run")
		assert json.loads(result.output)["data"]["accepted"] is False
		auth.assert_not_called()


def test_accept_validation_error_points_at_chat_lookup():
	"""平台数据无法确定附件目标时，要指向 hr chat 核对，而不是只报一句校验失败。"""
	module = "boss_agent_cli.commands.recruiter.accept_resume"
	with patch(module + ".AuthManager"), patch(module + ".get_recruiter_platform_instance") as factory:
		platform = factory.return_value.__enter__.return_value
		platform.accept_resume_by_friend.side_effect = ResumeValidationError("附件请求不匹配")
		result = _invoke("accept-resume", "123", "--message-id", "456", "--yes")
	error = json.loads(result.output)["error"]
	assert error["code"] == "INVALID_PARAM"
	assert _hint("hr chat") in error["recovery_action"]
	assert json.loads(result.output)["hints"]["next_actions"] == [_hint("hr chat")]


@pytest.mark.parametrize("status,success", [(0, True), (1, False), (2, False), (4, False), (None, False), (False, False)])
def test_accept_requires_both_success_codes(status, success):
	with patch("boss_agent_cli.commands.recruiter.accept_resume.AuthManager"), patch("boss_agent_cli.commands.recruiter.accept_resume.get_recruiter_platform_instance") as factory:
		platform = factory.return_value.__enter__.return_value
		platform.accept_resume_by_friend.return_value = {"code": 0, "zpData": {"status": status}}
		platform.is_success.return_value = True
		platform.unwrap_data.side_effect = lambda response: response["zpData"]
		result = _invoke("accept-resume", "123", "--message-id", "456", "--yes")
		assert json.loads(result.output)["ok"] is success
		if not success:
			error = json.loads(result.output)["error"]
			assert error["code"] == "RESUME_ACCEPT_RESULT_UNKNOWN"
			assert error["details"]["accepted"] is None
			assert (error["recoverable"], error["recovery_action"]) == error_contract_for_code(error["code"])
		platform.accept_resume_by_friend.assert_called_once_with(123, 456)


def test_accept_response_decode_failure_is_unknown_and_redacted():
	with patch("boss_agent_cli.commands.recruiter.accept_resume.AuthManager"), patch("boss_agent_cli.commands.recruiter.accept_resume.get_recruiter_platform_instance") as factory:
		platform = factory.return_value.__enter__.return_value
		platform.accept_resume_by_friend.side_effect = ValueError("private-token-in-response")
		result = _invoke("accept-resume", "123", "--message-id", "456", "--yes")
		assert json.loads(result.output)["error"]["details"]["accepted"] is None
		assert json.loads(result.output)["error"]["code"] == "RESUME_ACCEPT_RESULT_UNKNOWN"
		assert "private-token" not in result.output
		platform.accept_resume_by_friend.assert_called_once()


def test_accept_budget_stop_is_not_result_unknown():
	"""预算/时限在调度前停止：同意请求没有发出，不能报成「结果未知」。"""
	from boss_agent_cli.api.request_budget import RequestBudgetExceeded

	module = "boss_agent_cli.commands.recruiter.accept_resume"
	with patch(module + ".AuthManager"), patch(module + ".get_recruiter_platform_instance") as factory:
		platform = factory.return_value.__enter__.return_value
		platform.accept_resume_by_friend.side_effect = RequestBudgetExceeded("fixture budget stop")
		result = _invoke("accept-resume", "123", "--message-id", "456", "--yes")
	payload = json.loads(result.output)
	assert result.exit_code == 1
	assert payload["error"]["code"] == "REQUEST_BUDGET_EXHAUSTED"
	assert payload["error"]["details"] == {"request_sent": False}
	assert (payload["error"]["recoverable"], payload["error"]["recovery_action"]) == error_contract_for_code(
		"REQUEST_BUDGET_EXHAUSTED"
	)


def test_platform_adapter_and_mcp_mapping(tmp_path):
	client = MagicMock()
	platform = BossRecruiterPlatform(client)
	platform.accept_resume_by_friend(123, 456)
	client.accept_resume_by_friend.assert_called_once_with(123, 456)
	platform.download_resume_by_friend(123, 456, tmp_path / "resume.pdf")
	client.download_resume_by_friend.assert_called_once_with(123, 456, tmp_path / "resume.pdf")
	assert _build_args("boss_hr_accept_resume", {"friend_id": 123, "message_id": 456, "yes": "false"}) == ["hr", "accept-resume", "123", "--message-id", "456"]
	assert _build_args("boss_hr_accept_resume", {"friend_id": 123, "message_id": 456, "yes": True})[-1] == "--yes"
	assert _build_args("boss_hr_download_resume", {"friend_id": 123, "message_id": 456, "output": "resume.pdf"}) == ["hr", "download-resume", "123", "--message-id", "456", "--output", "resume.pdf"]


@pytest.mark.parametrize("status,body", [(403, "denied"), (200, '{"code":9}'), (200, '{"code":37}'), (200, '{"code":37,"message":"stoken expired"}'), (200, '{"code":37,"message":"环境异常"}'), (302, "redirect")])
def test_accept_native_transport_does_not_retry_or_refresh(status, body):
	client = _client()
	client._auth = MagicMock()
	client._auth.get_token.return_value = {"cookies": {}}
	requests = []
	def handler(request):
		requests.append(request)
		return httpx.Response(status, text=body, headers={"Location": "https://example.invalid/"})
	with httpx.Client(transport=httpx.MockTransport(handler)) as transport:
		client._get_client = MagicMock(return_value=transport)
		client._headers_for = MagicMock(return_value={})
		client._request = BossRecruiterClient._request.__get__(client)
		try:
			client.accept_resume_by_friend(123, 456)
		except PlatformRiskError as exc:
			if status == 403:
				assert isinstance(exc, AccountRiskError)
			else:
				assert json.loads(body)["code"] == 37 and "stoken" not in body
				assert isinstance(exc, EnvironmentRiskError)
		except (httpx.HTTPError, RecruiterAuthError) as exc:
			assert status in (403, 302), type(exc)
	assert len(requests) == 1
	client._auth.force_refresh.assert_not_called()
	client._browser_request.assert_not_called()


def test_download_cli_returns_only_file_metadata(tmp_path):
	with patch("boss_agent_cli.commands.recruiter.download_resume.AuthManager"), patch("boss_agent_cli.commands.recruiter.download_resume.get_recruiter_platform_instance") as factory:
		platform = factory.return_value.__enter__.return_value
		platform.download_resume_by_friend.return_value = {"code": 0, "zpData": {"path": str(tmp_path / "resume.pdf"), "bytes": 100, "format": "pdf"}}
		platform.is_success.return_value = True
		platform.unwrap_data.side_effect = lambda response: response["zpData"]
		result = _invoke("download-resume", "123", "--message-id", "456", "--output", str(tmp_path / "resume.pdf"))
		payload = json.loads(result.output)
		assert payload["data"]["downloaded"] is True
		# 落盘之后要给可执行的后继命令，操作者也要看得出文件在哪。
		assert payload["hints"]["next_actions"] == [_hint("hr chatmsg 123")]
		assert str((tmp_path / "resume.pdf").resolve()) in payload["hints"]["operator_actions"][0]
		platform.download_resume_by_friend.assert_called_once_with(123, 456, tmp_path / "resume.pdf")


def test_download_cli_expands_tilde_and_creates_parent(tmp_path, monkeypatch):
	"""MCP 子进程调用不经 shell，`~` 不会被展开；父目录也可能尚不存在。"""
	monkeypatch.setenv("HOME", str(tmp_path))
	monkeypatch.setenv("USERPROFILE", str(tmp_path))
	with patch("boss_agent_cli.commands.recruiter.download_resume.AuthManager"), patch("boss_agent_cli.commands.recruiter.download_resume.get_recruiter_platform_instance") as factory:
		platform = factory.return_value.__enter__.return_value
		platform.download_resume_by_friend.return_value = {"code": 0, "zpData": {"path": "x", "bytes": 100, "format": "pdf"}}
		platform.is_success.return_value = True
		platform.unwrap_data.side_effect = lambda response: response["zpData"]
		result = _invoke("download-resume", "123", "--message-id", "456", "--output", "~/resumes/2026/resume.pdf")
		assert result.exit_code == 0
		expected = tmp_path / "resumes" / "2026" / "resume.pdf"
		platform.download_resume_by_friend.assert_called_once_with(123, 456, expected)
		assert expected.parent.is_dir()


def test_download_cli_redacts_http_errors():
	with patch("boss_agent_cli.commands.recruiter.download_resume.AuthManager"), patch("boss_agent_cli.commands.recruiter.download_resume.get_recruiter_platform_instance") as factory:
		platform = factory.return_value.__enter__.return_value
		platform.download_resume_by_friend.side_effect = httpx.ReadTimeout("https://example.invalid/?d=secret-value")
		result = _invoke("download-resume", "123", "--message-id", "456", "--output", "resume.pdf")
		assert json.loads(result.output)["ok"] is False
		error = json.loads(result.output)["error"]
		assert error["code"] == "NETWORK_ERROR"
		assert (error["recoverable"], error["recovery_action"]) == error_contract_for_code("NETWORK_ERROR")
		assert "secret-value" not in result.output


def test_new_commands_are_discoverable_with_compliance():
	from boss_agent_cli.schema.data import SCHEMA_DATA
	from boss_agent_cli.mcp_tools import TOOLS, _compliance_command_for_tool
	for action in ("accept-resume", "download-resume"):
		assert action in SCHEMA_DATA["commands"]["hr"]["subcommands"]
		name = "boss_hr_" + action.replace("-", "_")
		tool = next(tool for tool in TOOLS if tool.name == name)
		assert "message_id" in tool.input_schema["required"]
		assert _compliance_command_for_tool(name) == "recruiter-" + action


@pytest.mark.parametrize("action", ["accept_resume", "download_resume"])
@pytest.mark.parametrize("error_type,code", [
	("AuthRequired", "AUTH_REQUIRED"),
	("RecruiterAuthError", "AUTH_REQUIRED"),
	("TokenRefreshFailed", "TOKEN_REFRESH_FAILED"),
	("AccountRiskError", "ACCOUNT_RISK"),
	("EnvironmentRiskError", "ENVIRONMENT_RISK"),
	("EnvironmentRiskLockedError", "ENVIRONMENT_RISK_LOCKED"),
])
def test_resume_auth_and_risk_failures_stop_without_leaking(action, error_type, code):
	from boss_agent_cli.api.client import AccountRiskError, EnvironmentRiskError, EnvironmentRiskLockedError
	from boss_agent_cli.auth.manager import AuthRequired, TokenRefreshFailed

	errors = {
		"AuthRequired": AuthRequired,
		"RecruiterAuthError": RecruiterAuthError,
		"TokenRefreshFailed": TokenRefreshFailed,
		"AccountRiskError": AccountRiskError,
		"EnvironmentRiskError": EnvironmentRiskError,
		"EnvironmentRiskLockedError": EnvironmentRiskLockedError,
	}
	module = "boss_agent_cli.commands.recruiter." + action
	with patch(module + ".AuthManager"), patch(module + ".get_recruiter_platform_instance") as factory:
		platform = factory.return_value.__enter__.return_value
		method = getattr(platform, action + "_by_friend")
		method.side_effect = errors[error_type]("private-token")
		args = ["--yes"] if action == "accept_resume" else ["--output", "resume.pdf"]
		result = _invoke(action.replace("_", "-"), "123", "--message-id", "456", *args)
		payload = json.loads(result.output)
		error = payload["error"]
		assert error["code"] == code
		if action == "download_resume":
			_assert_download_error_contract(payload, code)
		else:
			assert error["recoverable"] is False
			assert error["recovery_action"]
		if code in {"ACCOUNT_RISK", "ENVIRONMENT_RISK", "ENVIRONMENT_RISK_LOCKED"}:
			# 风控异常必须走统一停止契约，不能漏进 NETWORK_ERROR / 结果未知兜底。
			assert error["recovery_action"] == error_contract_for_code(code)[1]
			assert payload["hints"]["next_actions"]
		assert "private-token" not in result.output
		if action == "accept_resume":
			assert error["details"]["accepted"] is None
		method.assert_called_once()


@pytest.mark.parametrize(
	("side_effect", "message_fragment", "recovery_fragment", "expected_hints"),
	[
		(FileExistsError(), "输出文件已存在", "换一个 --output 文件名", None),
		(OSError("disk"), "无法保存文件", "父目录存在且可写", None),
		(ResumeValidationError("该消息不是附件简历请求，不会执行联系方式交换"), "附件简历请求", _hint("hr chat"), [_hint("hr chat")]),
	],
)
def test_download_resume_local_errors_give_actionable_recovery(
	side_effect, message_fragment, recovery_fragment, expected_hints
):
	"""本地文件与请求校验失败不能只回「修正参数」：要说明下一步怎么做。"""
	module = "boss_agent_cli.commands.recruiter.download_resume"
	with patch(module + ".AuthManager"), patch(module + ".get_recruiter_platform_instance") as factory:
		platform = factory.return_value.__enter__.return_value
		platform.download_resume_by_friend.side_effect = side_effect
		result = _invoke("download-resume", "123", "--message-id", "456", "--output", "resume.pdf")

	payload = json.loads(result.output)
	assert payload["error"]["code"] == "INVALID_PARAM"
	assert message_fragment in payload["error"]["message"]
	assert recovery_fragment in payload["error"]["recovery_action"]
	if expected_hints is None:
		assert payload["hints"] is None
	else:
		assert payload["hints"]["next_actions"] == expected_hints


def test_download_budget_stop_is_not_network_error():
	"""下载遇到预算/时限停止时不能提示「检查网络后重试」。"""
	from boss_agent_cli.api.request_budget import RequestBudgetExceeded

	module = "boss_agent_cli.commands.recruiter.download_resume"
	with patch(module + ".AuthManager"), patch(module + ".get_recruiter_platform_instance") as factory:
		platform = factory.return_value.__enter__.return_value
		platform.download_resume_by_friend.side_effect = RequestBudgetExceeded("fixture budget stop")
		result = _invoke("download-resume", "123", "--message-id", "456", "--output", "resume.pdf")
	payload = json.loads(result.output)
	assert payload["error"]["code"] == "REQUEST_BUDGET_EXHAUSTED"
	assert (payload["error"]["recoverable"], payload["error"]["recovery_action"]) == error_contract_for_code(
		"REQUEST_BUDGET_EXHAUSTED"
	)


@pytest.mark.parametrize("action", ["accept_resume", "download_resume"])
@pytest.mark.parametrize("response,code", [
	({"code": -1, "message": "private-token"}, "UNKNOWN"),
	({"code": 7}, "AUTH_REQUIRED"),
	({"code": 37, "message": "stoken expired"}, "TOKEN_REFRESH_FAILED"),
	({"code": 37, "message": "环境异常"}, "ENVIRONMENT_RISK"),
	({"code": 37}, "ENVIRONMENT_RISK"),
	({"code": 36}, "ACCOUNT_RISK"),
])
def test_resume_platform_errors_use_declared_codes_and_safe_recovery(action, response, code):
	module = "boss_agent_cli.commands.recruiter." + action
	client = MagicMock()
	method = getattr(client, action + "_by_friend")
	method.return_value = response
	with patch(module + ".AuthManager"), patch(module + ".get_recruiter_platform_instance") as factory:
		factory.return_value.__enter__.return_value = BossRecruiterPlatform(client)
		args = ["--yes"] if action == "accept_resume" else ["--output", "resume.pdf"]
		result = _invoke(action.replace("_", "-"), "123", "--message-id", "456", *args)
		payload = json.loads(result.output)
		error = payload["error"]
		if code == "UNKNOWN":
			code = "RESUME_ACCEPT_RESULT_UNKNOWN" if action == "accept_resume" else "NETWORK_ERROR"
		assert error["code"] == code
		assert code in SCHEMA_DATA["error_codes"]
		assert "private-token" not in result.output
		if action == "accept_resume":
			assert error["details"]["accepted"] is None
			assert error["recoverable"] is False
			if code in {"ACCOUNT_RISK", "ENVIRONMENT_RISK"}:
				# 响应字典形态的风控码同样给统一停止指引，不只让用户核对本次请求。
				assert error["recovery_action"] == error_contract_for_code(code)[1]
				assert payload["hints"]["next_actions"]
			else:
				assert "不要自动重试" in error["recovery_action"]
		else:
			_assert_download_error_contract(payload, code)
			if code in {"ACCOUNT_RISK", "ENVIRONMENT_RISK"}:
				assert payload["hints"]["next_actions"]
		method.assert_called_once()


def test_resume_download_endpoint_comes_from_yaml():
	assert ep.BOSS_RESUME_DOWNLOAD_URL == ep._url("boss_resume_download")
	assert ep.BOSS_RESUME_DOWNLOAD_URL == "https://docdownload.zhipin.com/wflow/zpgeek/download/download4boss/"
