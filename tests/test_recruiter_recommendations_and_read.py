"""推荐牛人及首次招呼安全契约；所有平台调用使用替身。"""
import importlib.util
import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from click.testing import CliRunner
import httpx
import pytest

from boss_agent_cli.api import recruiter_endpoints as ep
from boss_agent_cli.api.client import AccountRiskError
from boss_agent_cli.api.recruiter_client import BossRecruiterClient, RecruiterAuthError
from boss_agent_cli.auth.manager import AuthRequired, TokenRefreshFailed
from boss_agent_cli.display import error_contract_for_code
from boss_agent_cli.main import cli
from boss_agent_cli.mcp_args import _build_args
from boss_agent_cli.platforms.zhipin_recruiter import BossRecruiterPlatform


def test_recommendations_cli_returns_full_cards() -> None:
	client = MagicMock()
	cards = {"geekList": [{"geekName": "测试候选人", "encryptGeekId": "geek", "securityId": "security"}], "hasMore": True}
	client.recommend_geeks.return_value = {"code": 0, "zpData": cards}
	with patch("boss_agent_cli.commands.recruiter.recommendations.AuthManager"), patch("boss_agent_cli.commands.recruiter.recommendations.get_recruiter_platform_instance") as factory:
		factory.return_value.__enter__.return_value = BossRecruiterPlatform(client)
		result = CliRunner().invoke(cli, ["--json", "hr", "recommendations", "--job-id", "job", "--page", "2"])
	assert result.exit_code == 0
	payload = json.loads(result.output)
	assert payload["ok"] is True
	assert payload["data"] == cards
	assert payload["command"] == "recruiter-recommendations"
	client.recommend_geeks.assert_called_once_with("job", page=2)
	client.start_chat.assert_not_called()


@pytest.mark.parametrize("response,code", [({"code": 7}, "AUTH_REQUIRED"), ({"code": 37, "message": "环境异常"}, "ENVIRONMENT_RISK")])
def test_recommendations_cli_reports_platform_failure(response: dict[str, Any], code: str) -> None:
	client = MagicMock()
	client.recommend_geeks.return_value = response
	with patch("boss_agent_cli.commands.recruiter.recommendations.AuthManager"), patch("boss_agent_cli.commands.recruiter.recommendations.get_recruiter_platform_instance") as factory:
		factory.return_value.__enter__.return_value = BossRecruiterPlatform(client)
		result = CliRunner().invoke(cli, ["--json", "hr", "recommendations", "--job-id", "job"])
	assert result.exit_code == 1
	payload = json.loads(result.output)
	assert payload["ok"] is False
	assert payload["error"]["code"] == code
	assert (payload["error"]["recoverable"], payload["error"]["recovery_action"]) == error_contract_for_code(code)
	client.recommend_geeks.assert_called_once_with("job", page=1)
	client.start_chat.assert_not_called()


def test_mcp_greet_confirmation_is_optional_and_not_coerced() -> None:
	from boss_agent_cli.mcp_tools import TOOLS
	tool = next(tool for tool in TOOLS if tool.name == "boss_hr_greet")
	assert "yes" not in tool.input_schema["required"]
	args = {"geek_id": "g", "job_id": "j", "expect_id": "e", "lid": "l", "security_id": "s", "message": "hello", "yes": "false"}
	assert "--yes" not in _build_args("boss_hr_greet", args)



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
	platform.is_success.side_effect = BossRecruiterPlatform(MagicMock()).is_success
	platform.unwrap_data.side_effect = lambda result: result.get("zpData")
	platform.parse_error.side_effect = BossRecruiterPlatform(MagicMock()).parse_error
	return platform



@pytest.fixture
def greeting_args(tmp_path: Path) -> list[str]:
	return [
		"--data-dir", str(tmp_path), "--json", "hr", "greet",
		"--geek-id", "geek", "--job-id", "job", "--expect-id", "expect",
		"--lid", "lid", "--security-id", "security", "--message", "hello",
	]



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



def test_recruiter_reservation_is_atomic_and_separate_from_candidate_records(tmp_path: Path) -> None:
	from boss_agent_cli.cache.store import CacheStore
	with CacheStore(tmp_path / "cache.db") as first, CacheStore(tmp_path / "cache.db") as second:
		first.record_greet("security", "job")
		assert first.claim_recruiter_greet("geek", "job") is None
		assert second.claim_recruiter_greet("geek", "job") == "pending"
		first.record_recruiter_greet("geek", "job")
		assert second.claim_recruiter_greet("geek", "job") == "sent"
		assert second.claim_recruiter_greet("another-geek", "job") is None


@pytest.mark.parametrize("message", ["访问环境存在异常", "环境异常", ""])
def test_greet_environment_risk_does_not_suggest_more_requests(
	greeting_args: list[str], greeting_platform: MagicMock, message: str,
) -> None:
	greeting_platform.start_chat.return_value = {"code": 37, "message": message}
	result = CliRunner().invoke(cli, greeting_args + ["--yes"])
	body = json.loads(result.output)
	assert result.exit_code == 1
	assert body["error"]["code"] == "ENVIRONMENT_RISK"
	assert body["error"]["recoverable"] is False
	assert body["error"]["details"]["sent"] is None
	assert error_contract_for_code("ENVIRONMENT_RISK")[1] in body["error"]["recovery_action"]
	assert "不要自动重发" in body["error"]["recovery_action"]
	assert not body["hints"].get("next_actions")
	assert "boss hr chat" not in json.dumps(body["hints"])
	greeting_platform.start_chat.assert_called_once()
	greeting_platform.friend_list.assert_not_called()



def test_compliance_precedes_confirmation(greeting_args):
	with patch("boss_agent_cli.commands.recruiter.recommendations.require_compliance_allowed", return_value=False) as compliance:
		result = CliRunner().invoke(cli, greeting_args)
	compliance.assert_called_once()
	assert "CONFIRMATION_REQUIRED" not in result.output


@pytest.fixture
def greeting_platform():
	platform = _platform_mock()
	platform.__enter__.return_value = platform
	platform.start_chat.return_value = {"code": 0, "zpData": {}}
	with patch("boss_agent_cli.commands.recruiter.recommendations.AuthManager"), patch(
		"boss_agent_cli.commands.recruiter.recommendations.get_recruiter_platform_instance", return_value=platform,
	):
		yield platform


def test_rejected_greeting_preserves_safe_platform_diagnostics(greeting_args, greeting_platform):
	greeting_platform.start_chat.return_value = {"code": 9, "message": "额度不足 cookie=private", "zpData": {"token": "secret"}}
	result = CliRunner().invoke(cli, greeting_args + ["--yes"])
	details = json.loads(result.output)["error"]["details"]
	assert details["platform_code"] == 9
	assert "额度不足" in details["platform_message"]
	assert "private" not in result.output
	assert "secret" not in result.output
	assert details["sent"] is None
	greeting_platform.start_chat.assert_called_once()


def test_code_zero_quota_page_is_unsent_and_cannot_be_retried(greeting_args, greeting_platform):
	# 脱敏后的实测业务字段：外层成功，但当前职位的免费开聊权益用完。
	greeting_platform.start_chat.return_value = {
		"code": 0, "message": "Success", "__cli_endpoint_hint__": ep.BOSS_CHAT_START_URL,
		"zpData": {"chat": 0, "status": 3, "newfriend": 0,
			"limitTitle": "今日主动沟通人数已达上限", "stateDesc": "该职位今日主动沟通已达5人"},
	}
	first = CliRunner().invoke(cli, greeting_args + ["--yes"])
	body = json.loads(first.output)
	assert first.exit_code == 1
	assert body["error"]["code"] == "GREET_LIMIT"
	assert body["error"]["details"]["sent"] is False
	assert body["error"]["details"]["platform_code"] == 0
	assert "5人" in body["error"]["message"]
	second = CliRunner().invoke(cli, greeting_args + ["--yes"])
	assert json.loads(second.output)["error"]["details"]["sent"] is False
	assert json.loads(second.output)["error"]["code"] == "GREET_LIMIT"
	greeting_platform.start_chat.assert_called_once()
	greeting_platform.friend_list.assert_not_called()


def test_quota_cache_failure_preserves_unsent_and_pending(greeting_args, greeting_platform):
	greeting_platform.start_chat.return_value = {
		"code": 0, "__cli_endpoint_hint__": ep.BOSS_CHAT_START_URL,
		"zpData": {"chat": 0, "status": 3, "limitTitle": "今日主动沟通人数已达上限"},
	}
	with patch("boss_agent_cli.cache.store.CacheStore.record_recruiter_greet", side_effect=OSError("private")):
		first = CliRunner().invoke(cli, greeting_args + ["--yes"])
	assert json.loads(first.output)["error"]["details"]["sent"] is False
	second = CliRunner().invoke(cli, greeting_args + ["--yes"])
	assert json.loads(second.output)["error"]["code"] == "GREET_RESULT_UNKNOWN"
	assert "private" not in first.output
	greeting_platform.start_chat.assert_called_once()


def test_successful_greeting_never_looks_up_or_marks_messages(greeting_args, greeting_platform):
	result = CliRunner().invoke(cli, greeting_args + ["--yes"])
	body = json.loads(result.output)
	assert body["ok"] is True
	assert body["data"] == {"geek_id": "geek", "job_id": "job", "sent": True}
	greeting_platform.start_chat.assert_called_once()
	greeting_platform.friend_list.assert_not_called()
	greeting_platform.last_messages.assert_not_called()
	greeting_platform.mark_read.assert_not_called()


def test_cache_write_failure_preserves_sent_and_blocks_resend(greeting_args, greeting_platform):
	with patch("boss_agent_cli.cache.store.CacheStore.record_recruiter_greet", side_effect=OSError("private")):
		first = CliRunner().invoke(cli, greeting_args + ["--yes"])
	body = json.loads(first.output)
	assert body["error"]["details"]["sent"] is True
	assert body["error"]["recoverable"] is False
	second = CliRunner().invoke(cli, greeting_args + ["--yes"])
	assert json.loads(second.output)["error"]["code"] == "GREET_RESULT_UNKNOWN"
	greeting_platform.start_chat.assert_called_once()


@pytest.mark.parametrize("failure,code", [
	(RecruiterAuthError("private"), "AUTH_REQUIRED"),
	(AuthRequired("private"), "AUTH_REQUIRED"),
	(TokenRefreshFailed("private"), "TOKEN_REFRESH_FAILED"),
	(AccountRiskError("private"), "ACCOUNT_RISK"),
])
def test_greeting_auth_and_risk_are_not_network_errors(greeting_args, greeting_platform, failure, code):
	greeting_platform.start_chat.side_effect = failure
	result = CliRunner().invoke(cli, greeting_args + ["--yes"])
	body = json.loads(result.output)
	assert body["error"]["code"] == code
	assert body["error"]["details"]["sent"] is None
	assert body["error"]["recoverable"] is False
	assert "private" not in result.output
	greeting_platform.start_chat.assert_called_once()


@pytest.mark.parametrize("option,value", [("--allow-mqtt-session", None), ("--read-receipt-timeout", "25")])
def test_removed_receipt_options_are_rejected_before_auth(greeting_args, option, value):
	with patch("boss_agent_cli.commands.recruiter.recommendations.AuthManager") as auth:
		result = CliRunner().invoke(cli, greeting_args + [option] + ([value] if value else []))
	assert result.exit_code != 0
	assert option in result.output
	auth.assert_not_called()


def test_receipt_modules_and_mcp_parameters_are_removed():
	from boss_agent_cli.schema.data import SCHEMA_DATA
	from boss_agent_cli.mcp_tools import TOOLS

	for module in ("boss_agent_cli.api.recruiter_mqtt", "boss_agent_cli.commands.recruiter._read_receipt_worker"):
		assert importlib.util.find_spec(module) is None
	assert not hasattr(BossRecruiterClient, "mark_read")
	assert not hasattr(BossRecruiterPlatform, "mark_read")
	tool = next(tool for tool in TOOLS if tool.name == "boss_hr_greet")
	assert "allow_mqtt_session" not in tool.input_schema["properties"]
	assert "read_receipt_timeout" not in tool.input_schema["properties"]
	options = SCHEMA_DATA["commands"]["hr"]["options"]["greet"]
	assert "--allow-mqtt-session" not in options
	assert "--read-receipt-timeout" not in options
