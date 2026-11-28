"""招聘者工作地点本地状态失败的确定性收口（Issue #3111）。"""

import json
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from boss_agent_cli.api.client import AccountRiskError
from boss_agent_cli.api.request_budget import RequestBudgetExceeded
from boss_agent_cli.cache.store import CacheStore
from boss_agent_cli.main import cli


DETAIL = {"code": 0, "zpData": {"friendList": [{
	"friendId": 5, "uid": 5, "securityId": "s", "jobId": 77, "name": "n", "jobName": "j",
}]}}


def _location_args(friend_id: int, tmp_path: Path) -> list[str]:
	return [
		"--data-dir", str(tmp_path), "--json", "hr", "location", str(friend_id), "--yes",
	]


def _invoke(args: list[str]) -> Any:
	result = CliRunner().invoke(cli, args)
	assert result.output, result.exception
	return json.loads(result.output), result.exit_code


@contextmanager
def _platform(platform: MagicMock) -> Iterator[None]:
	with patch("boss_agent_cli.commands.recruiter.location.AuthManager") as auth, \
			patch("boss_agent_cli.commands.recruiter.location.get_recruiter_platform_instance") as factory:
		auth.return_value.get_token.return_value = None
		factory.return_value.__enter__.return_value = platform
		yield


def _successful_platform() -> MagicMock:
	platform = MagicMock()
	platform.is_success.return_value = True
	platform.friend_detail.return_value = DETAIL
	platform.send_job_location.return_value = {"code": 0, "zpData": {}}
	return platform


def _cleaned_count(envelope: dict[str, Any], target: str) -> int:
	return next(item["cleaned"] for item in envelope["data"]["results"] if item["target"] == target)


def test_unsent_request_release_failure_is_local_state_error(tmp_path: Path) -> None:
	platform = _successful_platform()
	platform.friend_detail.side_effect = RequestBudgetExceeded("budget")
	with patch.object(CacheStore, "release_location_send", side_effect=OSError("disk read-only")):
		with _platform(platform):
			envelope, exit_code = _invoke(_location_args(5, tmp_path))

	error = envelope["error"]
	assert exit_code == 1
	assert error["code"] == "LOCAL_STATE_WRITE_FAILED"
	assert error["recoverable"] is False
	assert error["details"]["friend_id"] == 5
	assert error["details"]["request_sent"] is False
	assert error["details"]["recorded"] is False
	assert error["details"]["outcome_unknown"] is False
	assert error["details"]["replay_safe"] is False
	assert "clean --pending-recruiter-location" in error["recovery_action"]
	assert "--dry-run" in error["recovery_action"]
	assert "--all" not in error["recovery_action"]


def test_risk_release_failure_keeps_risk_code(tmp_path: Path) -> None:
	platform = _successful_platform()
	platform.friend_detail.side_effect = AccountRiskError("安全页")
	with patch.object(CacheStore, "release_location_send", side_effect=OSError("disk read-only")):
		with _platform(platform):
			envelope, exit_code = _invoke(_location_args(5, tmp_path))

	error = envelope["error"]
	assert exit_code == 1
	assert error["code"] == "ACCOUNT_RISK"
	assert error["details"]["local_claim_release_failed"] is True
	assert "clean --pending-recruiter-location" in error["recovery_action"]


def test_targeted_clean_unblocks_retry(tmp_path: Path) -> None:
	platform = _successful_platform()
	platform.friend_detail.side_effect = RequestBudgetExceeded("budget")
	with patch.object(CacheStore, "release_location_send", side_effect=OSError("disk read-only")):
		with _platform(platform):
			_invoke(_location_args(5, tmp_path))

	preview, _ = _invoke([
		"--data-dir", str(tmp_path), "--json", "clean",
		"--pending-recruiter-location", "5", "--dry-run",
	])
	assert _cleaned_count(preview, "招聘者工作地点预约") == 1
	assert "--pending-recruiter-location" in preview["hints"]["next_actions"][0]

	cleaned, _ = _invoke([
		"--data-dir", str(tmp_path), "--json", "clean",
		"--pending-recruiter-location", "5",
	])
	assert _cleaned_count(cleaned, "招聘者工作地点预约") == 1

	retry = _successful_platform()
	with _platform(retry):
		envelope, exit_code = _invoke(_location_args(5, tmp_path))
	assert exit_code == 0
	assert envelope["ok"] is True
	assert retry.send_job_location.call_count == 1


def test_targeted_clean_preserves_sent_and_other_sessions(tmp_path: Path) -> None:
	db_path = tmp_path / "cache" / "boss_agent.db"
	with CacheStore(db_path) as cache:
		assert cache.claim_location_send(5) is None
		cache.record_location_send(5, job_id="j")
		assert cache.claim_location_send(6) is None

	envelope, exit_code = _invoke([
		"--data-dir", str(tmp_path), "--json", "clean",
		"--pending-recruiter-location", "6",
	])
	assert exit_code == 0
	assert _cleaned_count(envelope, "招聘者工作地点预约") == 1

	with CacheStore(db_path) as cache:
		assert cache.claim_location_send(6) is None
		assert cache.location_send_status(5) == "sent"


def test_record_failure_after_send_is_not_result_unknown(tmp_path: Path) -> None:
	platform = _successful_platform()
	with patch.object(CacheStore, "record_location_send", side_effect=OSError("disk read-only")):
		with _platform(platform):
			envelope, exit_code = _invoke(_location_args(5, tmp_path))

	error = envelope["error"]
	assert exit_code == 1
	assert error["code"] == "LOCAL_STATE_WRITE_FAILED"
	assert error["details"]["sent"] is True
	assert error["details"]["recorded"] is False
	assert error["details"]["outcome_unknown"] is False
	assert error["details"]["replay_safe"] is False
	assert "核对" not in error["recovery_action"]
	assert "hr chatmsg" in error["recovery_action"]
