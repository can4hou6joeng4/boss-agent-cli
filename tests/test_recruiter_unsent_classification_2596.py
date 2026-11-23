"""招聘者写请求未发出与只读前置失败的确定性分类（Issue #2596 / #2597）。"""

import json
from typing import Any
from unittest.mock import MagicMock, patch

import httpx
import pytest
from click.testing import CliRunner

from boss_agent_cli.api._base_client import RequestTextEncodingError, was_request_provably_unsent
from boss_agent_cli.api.recruiter_client import BossRecruiterClient
from boss_agent_cli.main import cli
from boss_agent_cli.platforms.zhipin_recruiter import BossRecruiterPlatform
from boss_agent_cli.services.candidate_actions import candidate_action_provably_unsent


def _auth() -> MagicMock:
	auth = MagicMock()
	auth.get_token.return_value = {"cookies": {"wt2": "fake"}, "stoken": "s", "user_agent": "UA"}
	return auth


def _client() -> BossRecruiterClient:
	return BossRecruiterClient(_auth(), delay=(0, 0))


def _response(payload: dict[str, Any], method: str = "POST") -> httpx.Response:
	return httpx.Response(
		200,
		json=payload,
		request=httpx.Request(method, "https://www.zhipin.com/test"),
	)


def _invoke(module: str, args: list[str], client: Any) -> dict[str, Any]:
	with patch(f"boss_agent_cli.commands.recruiter.{module}.AuthManager"), \
			patch(f"boss_agent_cli.commands.recruiter.{module}.get_recruiter_platform_instance") as factory:
		factory.return_value.__enter__.return_value = BossRecruiterPlatform(client)
		result = CliRunner().invoke(cli, args)
	envelope = json.loads(result.output)
	return {"exit": result.exit_code, "envelope": envelope}


def _greet_args(data_dir: Any, message: str = "你好") -> list[str]:
	return [
		"--data-dir", str(data_dir), "--json", "hr", "greet",
		"--geek-id", "g", "--job-id", "j", "--expect-id", "e", "--lid", "l",
		"--security-id", "s", "--message", message, "--yes",
	]


def test_unsent_transport_errors_are_recognized():
	assert was_request_provably_unsent(httpx.ConnectError("refused")) is True
	assert was_request_provably_unsent(httpx.ReadTimeout("slow")) is False
	assert candidate_action_provably_unsent(httpx.ConnectError("refused")) is True
	assert candidate_action_provably_unsent(httpx.ReadTimeout("slow")) is False
	assert candidate_action_provably_unsent(RequestTextEncodingError("bad")) is True


def test_greet_connect_error_releases_reservation_and_allows_retry(tmp_path):
	def request(self: Any, method: str, url: str, **kwargs: Any) -> httpx.Response:
		raise httpx.ConnectError("refused")

	with patch.object(httpx.Client, "request", request):
		first = _invoke("recommendations", _greet_args(tmp_path), _client())

	error = first["envelope"]["error"]
	assert first["exit"] == 1
	assert error["code"] == "NETWORK_ERROR"
	assert error["recoverable"] is True

	retry_client = MagicMock()
	retry_client.is_success.return_value = True
	retry_client.unwrap_data.return_value = {}
	retry_client.start_chat.return_value = {"code": 0, "zpData": {}}
	second = _invoke("recommendations", _greet_args(tmp_path), retry_client)
	assert second["envelope"]["ok"] is True
	assert retry_client.start_chat.called


def test_greet_unencodable_message_is_rejected_before_claim(tmp_path):
	client = MagicMock()
	result = _invoke("recommendations", _greet_args(tmp_path, "hi\udcff"), client)

	assert result["envelope"]["error"]["code"] == "INVALID_PARAM"
	assert "UTF-8" in result["envelope"]["error"]["message"]
	client.start_chat.assert_not_called()


def test_location_connect_error_releases_reservation_and_allows_retry(tmp_path):
	friend_detail = {
		"code": 0,
		"zpData": {"friendList": [{
			"friendId": 5, "uid": 5, "securityId": "sec", "jobId": 77, "name": "n",
		}]},
	}

	def request(self: Any, method: str, url: str, **kwargs: Any) -> httpx.Response:
		if "sendJobLocation" in url:
			raise httpx.ConnectError("refused")
		return _response(friend_detail, method)

	with patch.object(httpx.Client, "request", request):
		first = _invoke("location", ["--data-dir", str(tmp_path), "--json", "hr", "location", "5", "--yes"], _client())

	error = first["envelope"]["error"]
	assert first["exit"] == 1
	assert error["code"] == "NETWORK_ERROR"
	assert error["recoverable"] is True

	retry_client = MagicMock()
	retry_client.is_success.return_value = True
	retry_client.unwrap_data.return_value = {}
	retry_client.friend_detail.return_value = friend_detail
	retry_client.send_job_location.return_value = {"code": 0, "zpData": {}}
	second = _invoke("location", ["--data-dir", str(tmp_path), "--json", "hr", "location", "5", "--yes"], retry_client)
	assert second["envelope"]["ok"] is True
	assert retry_client.send_job_location.called


@pytest.mark.parametrize(
	("payload", "expected_code", "expected_recoverable"),
	[
		({"code": 9, "message": "操作频繁"}, "RATE_LIMITED", True),
		({"code": 1, "message": "参数错误"}, "NETWORK_ERROR", True),
	],
)
def test_accept_resume_precheck_failure_is_read_only(tmp_path, payload, expected_code, expected_recoverable):
	calls: list[str] = []

	def request(self: Any, method: str, url: str, **kwargs: Any) -> httpx.Response:
		calls.append(method)
		return _response(payload, method)

	with patch.object(httpx.Client, "request", request), patch("time.sleep"):
		result = _invoke(
			"accept_resume",
			["--data-dir", str(tmp_path), "--json", "hr", "accept-resume", "5", "--message-id", "9", "--yes"],
			_client(),
		)

	error = result["envelope"]["error"]
	assert error["code"] == expected_code
	assert error["recoverable"] is expected_recoverable
	assert error["details"]["accepted"] is False
	assert calls == ["GET"]


def test_accept_resume_precheck_connect_error_is_retryable(tmp_path):
	calls: list[str] = []

	def request(self: Any, method: str, url: str, **kwargs: Any) -> httpx.Response:
		calls.append(method)
		raise httpx.ConnectError("refused")

	with patch.object(httpx.Client, "request", request):
		result = _invoke(
			"accept_resume",
			["--data-dir", str(tmp_path), "--json", "hr", "accept-resume", "5", "--message-id", "9", "--yes"],
			_client(),
		)

	error = result["envelope"]["error"]
	assert error["code"] == "NETWORK_ERROR"
	assert error["recoverable"] is True
	assert calls == ["GET"]
