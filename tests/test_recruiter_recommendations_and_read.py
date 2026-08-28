import json
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from boss_agent_cli.api import recruiter_endpoints as ep
from boss_agent_cli.api.recruiter_client import BossRecruiterClient
from boss_agent_cli.api.recruiter_mqtt import encode_message_read, encode_presence
from boss_agent_cli.commands.recruiter.recommendations import _read_state_after_greet
from boss_agent_cli.main import cli
from boss_agent_cli.mcp_args import _build_args


def test_encode_message_read_matches_verified_proto_shape() -> None:
	payload = encode_message_read(user_id=1, message_id=2, user_source=0, read_time_ms=3)
	assert payload.hex() == "080642080801100218032800"


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
		"code": 0,
		"zpData": {"result": [{"securityId": "security", "uid": 123, "unreadMsgCount": 1}]},
	}
	platform.last_messages.return_value = {
		"code": 0,
		"zpData": [{"uid": 123, "lastMsgInfo": {"messageId": 456}}],
	}
	platform.mark_read.return_value = {"code": 0, "zpData": {"ok": True}}

	result = _read_state_after_greet(
		platform,
		start_result={"code": 0, "zpData": {}},
		geek_id="geek",
		job_id="job",
		security_id="security",
	)

	assert result == {"status": "cleared"}
	platform.mark_read.assert_called_once_with(peer_uid=123, message_id=456, user_source=0)


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

	assert result == {"status": "failed", "reason": "conversation_lookup_failed"}
	platform.start_chat.assert_not_called()


@patch("boss_agent_cli.commands.recruiter.recommendations.get_recruiter_platform_instance")
@patch("boss_agent_cli.commands.recruiter.recommendations.AuthManager")
def test_greet_command_returns_partial_success_without_resending(mock_auth_cls: MagicMock, mock_platform_factory: MagicMock) -> None:
	platform = _platform_mock()
	platform.__enter__.return_value = platform
	platform.__exit__.return_value = None
	platform.start_chat.return_value = {"code": 0, "zpData": {}}
	platform.friend_list.return_value = {"code": 9, "message": "too fast"}
	mock_platform_factory.return_value = platform
	runner = CliRunner()

	result = runner.invoke(cli, [
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
	assert parsed["data"]["read_state"] == {"status": "failed", "reason": "conversation_lookup_failed"}
	assert "禁止重新发送招呼" in parsed["data"]["warning"]
	platform.start_chat.assert_called_once()
