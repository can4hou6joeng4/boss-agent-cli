import json
from typing import Any
from pathlib import Path
from unittest.mock import ANY

import httpx
import pytest
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from boss_agent_cli.api import recruiter_endpoints as ep
from boss_agent_cli.api.recruiter_client import BossRecruiterClient
from boss_agent_cli.api.recruiter_mqtt import encode_message_read, encode_presence
from boss_agent_cli.commands.recruiter.recommendations import _read_state_after_greet
from boss_agent_cli.main import cli
from boss_agent_cli.mcp_args import _build_args
from boss_agent_cli.platforms.zhipin_recruiter import BossRecruiterPlatform
from boss_agent_cli.commands.recruiter.recommendations import _message_record, _candidate_record


def test_encode_message_read_matches_verified_proto_shape() -> None:
	payload = encode_message_read(user_id=1, message_id=2, user_source=0, read_time_ms=3)
	assert payload.hex() == "080642080801100218032800"


def test_message_lookup_never_falls_back_to_unrelated_record() -> None:
	assert _message_record({"result": [{"uid": 999, "messageId": 456}]}, friend_id=123) is None


def test_candidate_lookup_ignores_nested_recommendations() -> None:
	data = {"result": [{"uid": 999}], "moreRecommendations": [{"securityId": "s", "uid": 123}]}
	assert _candidate_record(data, geek_id="g", security_id="s") is None


def test_mcp_greet_confirmation_is_optional_and_not_coerced() -> None:
	from boss_agent_cli.mcp_tools import TOOLS
	tool = next(tool for tool in TOOLS if tool.name == "boss_hr_greet")
	assert "yes" not in tool.input_schema["required"]
	args = {"geek_id": "g", "job_id": "j", "expect_id": "e", "lid": "l", "security_id": "s", "message": "hello", "yes": "false"}
	assert "--yes" not in _build_args("boss_hr_greet", args)


def test_encode_presence_has_protocol_type_and_presence_field() -> None:
	payload = encode_presence(user_id=123, uniqid="123", client_ip="")
	assert payload.startswith(bytes.fromhex("080222"))
	assert b"4.92" in payload
	assert b"web" in payload


def test_recommend_geeks_uses_rich_recommendation_endpoint() -> None:
	client = object.__new__(BossRecruiterClient)
	client._request = MagicMock(return_value={"code": 0})

	result = client.recommend_geeks("job-enc", page=2)

	assert result == {"code": 0}
	args, kwargs = client._request.call_args
	assert args == ("GET", ep.BOSS_RECOMMEND_GEEK_LIST_URL)
	assert kwargs["params"]["jobId"] == "job-enc"
	assert kwargs["params"]["page"] == 2
	assert "jobid=job-enc" in kwargs["extra_headers"]["Referer"]


def test_start_chat_uses_verified_first_contact_payload() -> None:
	client = object.__new__(BossRecruiterClient)
	client._request = MagicMock(return_value={"code": 0})
	client._browser_request = MagicMock()

	client.start_chat(
		geek_id="geek",
		job_id="job",
		expect_id="expect",
		lid="lid",
		security_id="security",
		message="hello",
	)

	client._request.assert_called_once_with(
		"POST",
		ep.BOSS_CHAT_START_URL,
		data={
			"gid": "geek",
			"suid": "",
			"jid": "job",
			"expectId": "expect",
			"lid": "lid",
			"greet": "hello",
			"from": "",
			"securityId": "security",
			"customGreetingGuide": "-1",
		},
		retry=False,
	)
	client._browser_request.assert_not_called()


def test_recruiter_write_commands_refuse_without_yes() -> None:
	runner = CliRunner()
	result = runner.invoke(
		cli,
		[
			"--json",
			"hr",
			"greet",
			"--geek-id", "g",
			"--job-id", "j",
			"--expect-id", "e",
			"--lid", "l",
			"--security-id", "s",
			"--message", "hello",
		],
	)
	assert result.exit_code == 1
	assert "CONFIRMATION_REQUIRED" in result.output

def test_new_mcp_argument_mappings() -> None:
	assert _build_args("boss_hr_recommendations", {"job_id": "job", "page": 2}) == [
		"hr", "recommendations", "--job-id", "job", "--page", "2",
	]
	assert _build_args("boss_hr_greet", {
		"geek_id": "geek",
		"job_id": "job",
		"expect_id": "expect",
		"lid": "lid",
		"security_id": "security",
		"message": "hello",
		"yes": True,
	}) == [
		"hr", "greet",
		"--geek-id", "geek",
		"--job-id", "job",
		"--expect-id", "expect",
		"--lid", "lid",
		"--security-id", "security",
		"--message", "hello",
		"--yes",
	]


def _platform_mock() -> MagicMock:
	platform = MagicMock()
	platform.is_success.side_effect = lambda result: result.get("code") == 0
	platform.unwrap_data.side_effect = lambda result: result.get("zpData")
	platform.parse_error.side_effect = BossRecruiterPlatform(MagicMock()).parse_error
	return platform


def test_greet_read_state_skips_mqtt_when_conversation_has_no_unread() -> None:
	platform = _platform_mock()
	platform.friend_list.return_value = {
		"code": 0,
		"zpData": {"result": [{"securityId": "security", "uid": 123, "unreadMsgCount": 0}]},
	}

	result = _read_state_after_greet(
		platform,
		start_result={"code": 0, "zpData": {}},
		geek_id="geek",
		job_id="job",
		security_id="security",
	)

	assert result == {"status": "not_needed", "unread": 0}
	platform.mark_read.assert_not_called()


def test_greet_read_state_marks_latest_unread_in_same_action() -> None:
	platform = _platform_mock()
	platform.friend_list.return_value = {
		"code": 0, "zpData": {"result": [{"securityId": "security", "uid": 123, "unreadMsgCount": 1}]},
	}
	platform.last_messages.return_value = {
		"code": 0,
		"zpData": [{"uid": 123, "lastMsgInfo": {"messageId": 456}}],
	}
	platform.mark_read.return_value = {"code": 0, "zpData": {"published": True}}

	result = _read_state_after_greet(
		platform,
		start_result={"code": 0, "zpData": {}},
		geek_id="geek",
		job_id="job",
		security_id="security",
		allow_mqtt_session=True,
	)

	assert result == {"status": "published"}
	platform.mark_read.assert_called_once_with(peer_uid=123, message_id=456, user_source=0, deadline=ANY, allow_mqtt_session=True)
	platform.friend_list.assert_called_once()


def test_greet_read_state_failure_never_retries_greeting() -> None:
	platform = _platform_mock()
	platform.friend_list.return_value = {"code": 37, "message": "auth expired"}

	result = _read_state_after_greet(
		platform,
		start_result={"code": 0, "zpData": {}},
		geek_id="geek",
		job_id="job",
		security_id="security",
	)

	assert result == {"status": "failed", "reason": "conversation_lookup_failed", "error_code": "TOKEN_REFRESH_FAILED"}
	platform.start_chat.assert_not_called()


@patch("boss_agent_cli.commands.recruiter.recommendations.get_recruiter_platform_instance")
@patch("boss_agent_cli.commands.recruiter.recommendations.AuthManager")
@patch("boss_agent_cli.commands.recruiter.recommendations.run_read_receipt")
def test_greet_command_returns_partial_success_without_resending(mock_receipt: MagicMock, mock_auth_cls: MagicMock, mock_platform_factory: MagicMock, tmp_path: Path) -> None:
	platform = _platform_mock()
	platform.__enter__.return_value = platform
	platform.__exit__.return_value = None
	platform.start_chat.return_value = {"code": 0, "zpData": {}}
	platform.friend_list.side_effect = httpx.ReadTimeout("timeout")
	mock_platform_factory.return_value = platform
	mock_receipt.side_effect = lambda **kwargs: _run_mock_receipt(platform, **kwargs)
	runner = CliRunner()

	result = runner.invoke(cli, [
		"--data-dir", str(tmp_path),
		"--json", "--role", "recruiter", "hr", "greet",
		"--geek-id", "geek",
		"--job-id", "job",
		"--expect-id", "expect",
		"--lid", "lid",
		"--security-id", "security",
		"--message", "hello",
		"--yes",
	])

	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["data"]["sent"] is True
	assert parsed["data"]["partial_success"] is True
	assert parsed["data"]["read_state"]["status"] == "timeout"
	assert "禁止重新发送招呼" in parsed["hints"]["operator_actions"][0]
	platform.start_chat.assert_called_once()


@pytest.fixture
def greeting_args(tmp_path: Path) -> list[str]:
	return [
		"--data-dir", str(tmp_path), "--json", "hr", "greet",
		"--geek-id", "geek", "--job-id", "job", "--expect-id", "expect",
		"--lid", "lid", "--security-id", "security", "--message", "hello",
	]


@pytest.fixture
def greeting_platform() -> MagicMock:
	platform = _platform_mock()
	platform.__enter__.return_value = platform
	platform.start_chat.return_value = {"code": 0, "zpData": {"securityId": "security", "uid": 123, "unread": 0}}
	with patch("boss_agent_cli.commands.recruiter.recommendations.AuthManager"), patch("boss_agent_cli.commands.recruiter.recommendations.get_recruiter_platform_instance", return_value=platform), patch(
		"boss_agent_cli.commands.recruiter.recommendations.run_read_receipt", side_effect=lambda **kwargs: _run_mock_receipt(platform, **kwargs),
	):
		yield platform


def _run_mock_receipt(platform: MagicMock, **kwargs: Any) -> dict[str, Any]:
	for key in ("data_dir", "platform_name", "delay", "cdp_url"):
		kwargs.pop(key)
	return _read_state_after_greet(platform, **kwargs)


def test_dry_run_has_no_auth_network_or_cache(greeting_args: list[str], tmp_path: Path) -> None:
	with patch("boss_agent_cli.commands.recruiter.recommendations.AuthManager") as auth:
		result = CliRunner().invoke(cli, greeting_args + ["--dry-run", "--yes"])
		assert result.exit_code == 0
		assert json.loads(result.output)["data"] == {"dry_run": True, "sent": False, "geek_id": "geek", "job_id": "job", "message": "hello"}
		auth.assert_not_called()
	assert not (tmp_path / "cache" / "boss_agent.db").exists()


def test_greet_deduplicates_when_security_id_rotates(greeting_args: list[str], greeting_platform: MagicMock) -> None:
	first = CliRunner().invoke(cli, greeting_args + ["--yes"])
	assert first.exit_code == 0, first.output
	greeting_args[greeting_args.index("--security-id") + 1] = "rotated-security"
	second = CliRunner().invoke(cli, greeting_args + ["--yes"])
	assert second.exit_code == 1
	assert json.loads(second.output)["error"]["code"] == "ALREADY_GREETED"
	greeting_platform.start_chat.assert_called_once()


def test_greet_default_defers_mqtt_and_preserves_sent(greeting_args: list[str], greeting_platform: MagicMock) -> None:
	greeting_platform.start_chat.return_value = {"code": 0, "zpData": {"encryptFriendId": "geek", "friendId": 123, "unread": 1}}
	result = CliRunner().invoke(cli, greeting_args + ["--yes"])
	body = json.loads(result.output)
	assert result.exit_code == 0
	assert body["data"]["sent"] is True
	assert body["data"]["partial_success"] is True
	assert body["data"]["read_state"]["status"] == "deferred"
	assert any("网页连接" in action for action in body["hints"]["operator_actions"])
	greeting_platform.mark_read.assert_not_called()
	greeting_platform.last_messages.assert_not_called()


def test_dry_run_discloses_mqtt_risk_without_auth(greeting_args: list[str]) -> None:
	with patch("boss_agent_cli.commands.recruiter.recommendations.AuthManager") as auth:
		result = CliRunner().invoke(cli, greeting_args + ["--dry-run", "--allow-mqtt-session"])
	assert result.exit_code == 0
	assert any("挤掉网页" in action for action in json.loads(result.output)["hints"]["operator_actions"])
	auth.assert_not_called()


@pytest.mark.parametrize("failure", [httpx.ReadTimeout("lost response"), {"code": 9}, {"code": 37}])
def test_uncertain_or_rejected_send_cannot_be_automatically_retried(greeting_args: list[str], greeting_platform: MagicMock, failure: Any) -> None:
	if isinstance(failure, Exception):
		greeting_platform.start_chat.side_effect = failure
	else:
		greeting_platform.start_chat.return_value = failure
	first = CliRunner().invoke(cli, greeting_args + ["--yes"])
	body = json.loads(first.output)
	assert first.exit_code == 1
	assert body["error"]["recoverable"] is False
	assert body["error"]["details"]["sent"] is None
	assert "不要自动重发" in body["error"]["recovery_action"]
	second = CliRunner().invoke(cli, greeting_args + ["--yes"])
	assert json.loads(second.output)["error"]["code"] == "GREET_RESULT_UNKNOWN"
	greeting_platform.start_chat.assert_called_once()
	greeting_platform.friend_list.assert_not_called()


@pytest.mark.parametrize("failure,code", [
	({"code": 7}, "AUTH_REQUIRED"),
	({"code": 36}, "ACCOUNT_RISK"),
	({"code": 37}, "TOKEN_REFRESH_FAILED"),
	({"code": 9}, "RATE_LIMITED"),
])
def test_post_send_errors_stop_workflow_and_preserve_sent(greeting_args: list[str], greeting_platform: MagicMock, failure: dict, code: str) -> None:
	greeting_platform.start_chat.return_value = {"code": 0, "zpData": {}}
	greeting_platform.friend_list.return_value = failure
	result = CliRunner().invoke(cli, greeting_args + ["--yes"])
	body = json.loads(result.output)
	assert result.exit_code == 1
	assert body["error"]["code"] == code
	assert body["error"]["recoverable"] is False
	assert body["error"]["details"]["sent"] is True
	assert body["error"]["details"]["read_state"]["error_code"] == code
	assert body["hints"]["operator_actions"]
	greeting_platform.start_chat.assert_called_once()
	greeting_platform.mark_read.assert_not_called()


@pytest.mark.parametrize("unread", [None, -1, True])
def test_unknown_unread_without_permission_defers_receipt(unread: Any) -> None:
	platform = _platform_mock()
	platform.friend_list.return_value = {"code": 0, "zpData": {"result": [{"securityId": "s", "uid": 123, "unread": unread, "messageId": 456}]}}
	result = _read_state_after_greet(platform, start_result={"code": 0}, geek_id="g", job_id="j", security_id="s")
	assert result == {"status": "deferred", "reason": "mqtt_session_not_authorized", "unread": None}
	platform.mark_read.assert_not_called()


@pytest.mark.parametrize("published", [None, False, 1, "true"])
def test_success_code_without_publish_confirmation_is_not_completed(published: Any) -> None:
	platform = _platform_mock()
	platform.friend_list.return_value = {"code": 0, "zpData": {"result": [{"securityId": "s", "uid": 123, "unread": 1, "messageId": 456}]}}
	platform.mark_read.return_value = {"code": 0, "zpData": {"published": published}}
	result = _read_state_after_greet(platform, start_result={"code": 0}, geek_id="g", job_id="j", security_id="s", allow_mqtt_session=True)
	assert result == {"status": "unknown", "reason": "read_receipt_publish_unconfirmed"}
	platform.friend_list.assert_called_once()


def test_missing_unread_uses_matching_latest_message_without_readback() -> None:
	platform = _platform_mock()
	platform.friend_list.return_value = {"code": 0, "zpData": {"result": [
		{"encryptFriendId": "geek", "friendId": 123, "friendSource": 1},
	]}}
	platform.last_messages.return_value = {"code": 0, "zpData": [
		{"uid": 999, "lastMsgInfo": {"msgId": 777}},
		{"uid": 123, "lastMsgInfo": {"msgId": 456}},
	]}
	platform.mark_read.return_value = {"code": 0, "zpData": {"published": True}}
	result = _read_state_after_greet(platform, start_result={"code": 0}, geek_id="geek", job_id="job", security_id="s", allow_mqtt_session=True)
	assert result == {"status": "published"}
	platform.friend_list.assert_called_once_with(job_id="job", deadline=ANY)
	platform.last_messages.assert_called_once_with([123], deadline=ANY)
	platform.mark_read.assert_called_once_with(peer_uid=123, message_id=456, user_source=1, deadline=ANY, allow_mqtt_session=True)
	platform.start_chat.assert_not_called()


def test_start_response_with_target_and_message_needs_no_lookup() -> None:
	platform = _platform_mock()
	platform.mark_read.return_value = {"code": 0, "zpData": {"published": True}}
	result = _read_state_after_greet(
		platform, start_result={"code": 0, "zpData": {"encryptFriendId": "geek", "friendId": 123, "msgId": 456}},
		geek_id="geek", job_id="job", security_id="s", allow_mqtt_session=True,
	)
	assert result == {"status": "published"}
	platform.friend_list.assert_not_called()
	platform.last_messages.assert_not_called()
	platform.mark_read.assert_called_once()


@pytest.mark.parametrize("records", [[], [{"uid": 999, "lastMsgInfo": {"msgId": 456}}], [{"uid": 123}], [{"uid": 123, "msgId": 456}, {"uid": 123, "msgId": 789}]])
def test_unresolved_latest_message_never_publishes(records: list[dict[str, Any]]) -> None:
	platform = _platform_mock()
	platform.last_messages.return_value = {"code": 0, "zpData": records}
	result = _read_state_after_greet(
		platform, start_result={"code": 0, "zpData": {"encryptFriendId": "geek", "friendId": 123}},
		geek_id="geek", job_id="job", security_id="s", allow_mqtt_session=True,
	)
	assert result == {"status": "unknown", "reason": "message_id_unresolved"}
	platform.mark_read.assert_not_called()


def test_unresolved_conversation_never_publishes() -> None:
	platform = _platform_mock()
	platform.friend_list.return_value = {"code": 0, "zpData": {"result": [{"encryptFriendId": "other", "friendId": 999}]}}
	result = _read_state_after_greet(platform, start_result={"code": 0}, geek_id="geek", job_id="job", security_id="s", allow_mqtt_session=True)
	assert result == {"status": "unknown", "reason": "conversation_unresolved"}
	platform.mark_read.assert_not_called()
	platform.last_messages.assert_not_called()


def test_greet_publish_ack_completes_cleanup_without_readback(greeting_args: list[str], greeting_platform: MagicMock) -> None:
	greeting_platform.start_chat.return_value = {"code": 0, "zpData": {"encryptFriendId": "geek", "friendId": 123, "msgId": 456}}
	greeting_platform.mark_read.return_value = {"code": 0, "zpData": {"published": True}}
	result = CliRunner().invoke(cli, greeting_args + ["--yes", "--allow-mqtt-session"])
	body = json.loads(result.output)
	assert result.exit_code == 0
	assert body["data"]["sent"] is True
	assert body["data"]["partial_success"] is False
	assert body["data"]["read_state"] == {"status": "published"}
	greeting_platform.start_chat.assert_called_once()
	greeting_platform.mark_read.assert_called_once()
	greeting_platform.friend_list.assert_not_called()
	second = CliRunner().invoke(cli, greeting_args + ["--yes", "--allow-mqtt-session"])
	assert json.loads(second.output)["error"]["code"] == "ALREADY_GREETED"
	greeting_platform.start_chat.assert_called_once()
	greeting_platform.mark_read.assert_called_once()


def test_card_id_never_overrides_message_id() -> None:
	from boss_agent_cli.commands.recruiter.recommendations import _message_id
	assert _message_id({"id": 999, "jobInfo": {"id": 888}, "lastMsgInfo": {"messageId": 456}}) == 456
	assert _message_id({"id": 999, "jobInfo": {"id": 888}}) is None


def test_recruiter_reservation_is_atomic_and_separate_from_candidate_records(tmp_path: Path) -> None:
	from boss_agent_cli.cache.store import CacheStore
	with CacheStore(tmp_path / "cache.db") as first, CacheStore(tmp_path / "cache.db") as second:
		first.record_greet("security", "job")
		assert first.claim_recruiter_greet("geek", "job") is None
		assert second.claim_recruiter_greet("geek", "job") == "pending"
		first.record_recruiter_greet("geek", "job")
		assert second.claim_recruiter_greet("geek", "job") == "sent"
		assert second.claim_recruiter_greet("another-geek", "job") is None


def test_budget_exhaustion_prevents_receipt():
	platform = _platform_mock()
	platform.friend_list.return_value = {"code": 0, "zpData": {"result": [{"securityId": "s", "uid": 123, "unread": 1, "messageId": 456}]}}
	with patch("boss_agent_cli.api.httpx_helpers.time.monotonic", side_effect=[100, 100, 126]):
		result = _read_state_after_greet(platform, start_result={"code": 0}, geek_id="g", job_id="j", security_id="s", timeout=25, allow_mqtt_session=True)
	assert result["status"] == "timeout"
	platform.mark_read.assert_not_called()


def test_browser_source_error_preserves_sent(greeting_args, greeting_platform):
	from boss_agent_cli.api.browser_source import BrowserSourceUnavailable, resolve_policy
	greeting_platform.start_chat.return_value = {"code": 0, "zpData": {}}
	greeting_platform.friend_list.side_effect = BrowserSourceUnavailable(resolve_policy("existing-browser"))
	result = CliRunner().invoke(cli, greeting_args + ["--yes"])
	body = json.loads(result.output)
	assert body["error"]["code"] == "BROWSER_SESSION_NOT_FOUND"
	assert body["error"]["details"]["sent"] is True
	assert body["error"]["recoverable"] is False


def test_unexpected_cleanup_error_never_loses_sent(greeting_args, greeting_platform):
	greeting_platform.start_chat.return_value = {"code": 0, "zpData": {}}
	greeting_platform.friend_list.side_effect = TypeError("bad adapter")
	result = CliRunner().invoke(cli, greeting_args + ["--yes"])
	body = json.loads(result.output)
	assert body["error"]["details"]["sent"] is True
	assert body["error"]["recoverable"] is False


def test_compliance_precedes_confirmation(greeting_args):
	with patch("boss_agent_cli.commands.recruiter.recommendations.require_compliance_allowed", return_value=False) as compliance:
		result = CliRunner().invoke(cli, greeting_args)
	compliance.assert_called_once()
	assert "CONFIRMATION_REQUIRED" not in result.output
