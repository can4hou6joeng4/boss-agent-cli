"""招聘者发送工作地点：离线契约。平台与登录态全部使用替身。"""

import json
import sqlite3
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from click.testing import CliRunner
import pytest

from boss_agent_cli.api import recruiter_endpoints as ep
from boss_agent_cli.api.recruiter_client import BossRecruiterClient
from boss_agent_cli.auth.manager import AuthRequired
from boss_agent_cli.automation.storage import AutomationStore
from boss_agent_cli.cache.store import CacheStore
from boss_agent_cli.display import error_contract_for_code
from boss_agent_cli.main import cli
from boss_agent_cli.platforms.zhipin_recruiter import BossRecruiterPlatform


FRIEND_ID = 10001


def _friend(**overrides: Any) -> dict[str, Any]:
	friend: dict[str, Any] = {
		"uid": FRIEND_ID,
		"securityId": "conversation-security",
		"jobId": 5566,
		"jobName": "后端工程师",
		"name": "测试候选人",
	}
	friend.update(overrides)
	return friend


def _detail(*friends: dict[str, Any]) -> dict[str, Any]:
	return {"code": 0, "zpData": {"friendList": list(friends)}}


def _platform() -> MagicMock:
	platform = MagicMock()
	real = BossRecruiterPlatform(MagicMock())
	platform.is_success.side_effect = real.is_success
	platform.parse_error.side_effect = real.parse_error
	platform.unwrap_data.side_effect = real.unwrap_data
	platform.friend_detail.return_value = _detail(_friend())
	platform.send_job_location.return_value = {"code": 0, "zpData": {}}
	return platform


def _invoke(tmp_path: Path, *args: str, platform: MagicMock | None = None):
	with patch("boss_agent_cli.commands.recruiter.location.AuthManager") as auth, patch(
		"boss_agent_cli.commands.recruiter.location.get_recruiter_platform_instance"
	) as factory:
		if platform is not None:
			factory.return_value.__enter__.return_value = platform
		result = CliRunner().invoke(
			cli, ["--data-dir", str(tmp_path), "--json", "hr", "location", str(FRIEND_ID), *args]
		)
	return result, auth, factory


def _payload(result) -> dict[str, Any]:
	return json.loads(result.output)


def test_dry_run_previews_without_auth_network_or_ledger(tmp_path: Path) -> None:
	result, auth, factory = _invoke(tmp_path, "--dry-run")
	assert result.exit_code == 0, result.output
	payload = _payload(result)
	assert payload["ok"] is True
	assert payload["command"] == "recruiter-location"
	assert payload["data"] == {"friend_id": FRIEND_ID, "sent": False, "dry_run": True}
	assert "friend_detail" in payload["hints"]["operator_actions"][0]
	auth.assert_not_called()
	factory.assert_not_called()
	assert not (tmp_path / "cache" / "boss_agent.db").exists()


def test_send_requires_explicit_confirmation(tmp_path: Path) -> None:
	result, auth, factory = _invoke(tmp_path)
	assert result.exit_code == 1, result.output
	payload = _payload(result)
	assert payload["ok"] is False
	assert payload["command"] == "recruiter-location"
	assert payload["error"]["code"] == "CONFIRMATION_REQUIRED"
	assert (payload["error"]["recoverable"], payload["error"]["recovery_action"]) == error_contract_for_code(
		"CONFIRMATION_REQUIRED"
	)
	auth.assert_not_called()
	factory.assert_not_called()


def test_send_uses_conversation_security_id_and_records_ledger(tmp_path: Path) -> None:
	platform = _platform()
	result, _auth, _factory = _invoke(tmp_path, "--yes", platform=platform)
	assert result.exit_code == 0, result.output
	payload = _payload(result)
	assert payload["data"] == {
		"friend_id": FRIEND_ID,
		"sent": True,
		"job_id": 5566,
		"job_name": "后端工程师",
		"name": "测试候选人",
	}
	assert payload["hints"]["next_actions"] == [f"boss hr chatmsg {FRIEND_ID}"]
	assert "conversation-security" not in result.output
	platform.friend_detail.assert_called_once_with([FRIEND_ID], retry=False)
	platform.send_job_location.assert_called_once_with(
		job_id=5566, to_user_id=FRIEND_ID, security_id="conversation-security"
	)

	again, _auth, factory = _invoke(tmp_path, "--yes", platform=_platform())
	assert again.exit_code == 1
	duplicate = _payload(again)
	assert duplicate["error"]["code"] == "LOCATION_ALREADY_SENT"
	assert duplicate["error"]["details"]["sent"] is True
	assert (duplicate["error"]["recoverable"], duplicate["error"]["recovery_action"]) == error_contract_for_code(
		"LOCATION_ALREADY_SENT"
	)
	factory.assert_not_called()


def test_not_logged_in_stops_before_any_platform_call(tmp_path: Path) -> None:
	platform = _platform()
	with patch("boss_agent_cli.commands.recruiter.location.AuthManager") as auth, patch(
		"boss_agent_cli.commands.recruiter.location.get_recruiter_platform_instance"
	) as factory:
		auth.return_value.get_token.side_effect = AuthRequired()
		factory.return_value.__enter__.return_value = platform
		result = CliRunner().invoke(
			cli, ["--data-dir", str(tmp_path), "--json", "hr", "location", str(FRIEND_ID), "--yes"]
		)
	assert result.exit_code == 1, result.output
	payload = _payload(result)
	assert payload["error"]["code"] == "AUTH_REQUIRED"
	assert payload["error"]["recoverable"] is True
	assert payload["error"]["recovery_action"]
	platform.friend_detail.assert_not_called()
	platform.send_job_location.assert_not_called()


def test_rate_limited_detail_does_not_send_or_lock_the_friend(tmp_path: Path) -> None:
	platform = _platform()
	platform.friend_detail.return_value = {"code": 9, "message": "请求频率过高"}
	result, _auth, _factory = _invoke(tmp_path, "--yes", platform=platform)
	assert result.exit_code == 1, result.output
	payload = _payload(result)
	assert payload["error"]["code"] == "RATE_LIMITED"
	assert (payload["error"]["recoverable"], payload["error"]["recovery_action"]) == error_contract_for_code(
		"RATE_LIMITED"
	)
	platform.send_job_location.assert_not_called()

	follow, _auth, _factory = _invoke(tmp_path, "--dry-run")
	assert follow.exit_code == 0, follow.output
	assert _payload(follow)["data"]["dry_run"] is True


def test_friend_not_found_does_not_send(tmp_path: Path) -> None:
	platform = _platform()
	platform.friend_detail.return_value = _detail()
	result, _auth, _factory = _invoke(tmp_path, "--yes", platform=platform)
	assert result.exit_code == 1, result.output
	payload = _payload(result)
	assert payload["error"]["code"] == "CONTACT_NOT_FOUND"
	assert payload["error"]["recoverable"] is False
	assert "boss hr chat" in payload["error"]["recovery_action"]
	assert payload["hints"]["next_actions"] == ["boss hr chat"]
	platform.send_job_location.assert_not_called()


@pytest.mark.parametrize(
	"overrides",
	[{"securityId": ""}, {"securityId": None}, {"jobId": 0}, {"jobId": ""}, {"jobId": None}],
)
def test_missing_session_fields_stop_before_send(tmp_path: Path, overrides: dict[str, Any]) -> None:
	platform = _platform()
	platform.friend_detail.return_value = _detail(_friend(**overrides))
	result, _auth, _factory = _invoke(tmp_path, "--yes", platform=platform)
	assert result.exit_code == 1, result.output
	payload = _payload(result)
	assert payload["error"]["code"] == "INVALID_PARAM"
	assert payload["error"]["recoverable"] is False
	assert "securityId" in payload["error"]["message"] or "jobId" in payload["error"]["message"]
	platform.send_job_location.assert_not_called()


def test_duplicate_ledger_blocks_before_auth(tmp_path: Path) -> None:
	with CacheStore(tmp_path / "cache" / "boss_agent.db") as cache:
		assert cache.claim_location_send(FRIEND_ID) is None
		cache.record_location_send(FRIEND_ID, job_id="5566")
	result, auth, factory = _invoke(tmp_path, "--yes")
	assert result.exit_code == 1, result.output
	assert _payload(result)["error"]["code"] == "LOCATION_ALREADY_SENT"
	auth.assert_not_called()
	factory.assert_not_called()


def test_stop_switch_blocks_preview_and_send(tmp_path: Path) -> None:
	AutomationStore(tmp_path).request_stop("manual-stop")
	for extra in (["--yes"], ["--dry-run"]):
		result, auth, factory = _invoke(tmp_path, *extra)
		assert result.exit_code == 1, result.output
		payload = _payload(result)
		assert payload["error"]["code"] == "CIRCUIT_BREAKER_OPEN"
		assert payload["error"]["recoverable"] is True
		assert "agent run --dry-run --resume" in payload["error"]["recovery_action"]
		assert "manual-stop" in payload["error"]["message"]
		auth.assert_not_called()
		factory.assert_not_called()


def test_nonzero_api_code_is_not_recorded_as_sent(tmp_path: Path) -> None:
	platform = _platform()
	platform.send_job_location.return_value = {"code": 1, "message": "发送失败"}
	result, _auth, _factory = _invoke(tmp_path, "--yes", platform=platform)
	assert result.exit_code == 1, result.output
	payload = _payload(result)
	assert payload["error"]["code"] == "UNKNOWN"
	assert (payload["error"]["recoverable"], payload["error"]["recovery_action"]) == error_contract_for_code("UNKNOWN")
	platform.send_job_location.assert_called_once()

	follow, _auth, _factory = _invoke(tmp_path, "--dry-run")
	assert follow.exit_code == 0, follow.output


def test_unconfirmed_send_stays_reserved(tmp_path: Path) -> None:
	platform = _platform()
	platform.send_job_location.return_value = {
		"code": -1,
		"message": "请求结果未确认",
		"__cli_error_code__": "ACTION_UNCONFIRMED",
		"__cli_error_details__": {"outcome": "unknown", "replay_safe": False, "reason": "transport_error"},
	}
	result, _auth, _factory = _invoke(tmp_path, "--yes", platform=platform)
	assert result.exit_code == 1, result.output
	assert _payload(result)["error"]["code"] == "ACTION_UNCONFIRMED"

	again, auth, factory = _invoke(tmp_path, "--yes")
	assert again.exit_code == 1
	assert _payload(again)["error"]["code"] == "LOCATION_RESULT_UNKNOWN"
	auth.assert_not_called()
	factory.assert_not_called()


def test_send_job_location_posts_the_captured_form() -> None:
	client = object.__new__(BossRecruiterClient)
	client._request = MagicMock(return_value={"code": 0})
	assert client.send_job_location(job_id=5566, to_user_id=FRIEND_ID, security_id="conversation-security") == {"code": 0}
	client._request.assert_called_once_with(
		"POST",
		ep.BOSS_SEND_JOB_LOCATION_URL,
		data={"jobId": 5566, "toUserId": FRIEND_ID, "securityId": "conversation-security"},
		retry=False,
	)


def test_platform_delegates_send_job_location() -> None:
	client = MagicMock()
	client.send_job_location.return_value = {"code": 0}
	result = BossRecruiterPlatform(client).send_job_location(job_id=1, to_user_id=2, security_id="sec")
	assert result == {"code": 0}
	client.send_job_location.assert_called_once_with(job_id=1, to_user_id=2, security_id="sec")


def test_claim_record_and_release_location_ledger(tmp_path: Path) -> None:
	with CacheStore(tmp_path / "cache" / "boss_agent.db") as cache:
		assert cache.location_send_status(FRIEND_ID) is None
		assert cache.claim_location_send(FRIEND_ID) is None
		assert cache.claim_location_send(FRIEND_ID) == "pending"
		assert cache.release_location_send(FRIEND_ID) is True
		assert cache.location_send_status(FRIEND_ID) is None
		assert cache.claim_location_send(FRIEND_ID) is None
		cache.record_location_send(FRIEND_ID, job_id="5566")
		assert cache.location_send_status(FRIEND_ID) == "sent"
		assert cache.release_location_send(FRIEND_ID) is False
		assert cache.claim_location_send(FRIEND_ID) == "sent"

	conn = sqlite3.connect(tmp_path / "cache" / "boss_agent.db")
	row = conn.execute(
		"SELECT job_id, name FROM recruiter_location_records WHERE friend_id = ?",
		(str(FRIEND_ID),),
	).fetchone()
	conn.close()
	assert row == ("5566", "")
