"""招聘者招呼本地状态失败的确定性收口（Issue #3110）。"""

import json
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from boss_agent_cli.api.request_budget import RequestBudgetExceeded
from boss_agent_cli.cache.store import CacheStore
from boss_agent_cli.main import cli


def _greet_args(tmp_path: Path) -> list[str]:
	return [
		"--data-dir", str(tmp_path), "--json", "hr", "greet",
		"--geek-id", "g", "--job-id", "j", "--expect-id", "e", "--lid", "",
		"--security-id", "s", "--message", "你好", "--yes",
	]


def _invoke(args: list[str]) -> Any:
	result = CliRunner().invoke(cli, args)
	assert result.output, result.exception
	return json.loads(result.output), result.exit_code


@contextmanager
def _platform(platform: MagicMock) -> Iterator[None]:
	with patch("boss_agent_cli.commands.recruiter.recommendations.AuthManager") as auth, \
			patch("boss_agent_cli.commands.recruiter.recommendations.get_recruiter_platform_instance") as factory:
		auth.return_value.get_token.return_value = None
		factory.return_value.__enter__.return_value = platform
		yield


def _successful_platform() -> MagicMock:
	platform = MagicMock()
	platform.is_success.return_value = True
	platform.start_chat.return_value = {"code": 0, "zpData": {}}
	return platform


def _cleaned_count(envelope: dict[str, Any], target: str) -> int:
	return next(item["cleaned"] for item in envelope["data"]["results"] if item["target"] == target)


def test_unsent_request_release_failure_is_local_state_error(tmp_path: Path) -> None:
	platform = _successful_platform()
	platform.start_chat.side_effect = RequestBudgetExceeded("budget")
	with patch.object(CacheStore, "release_recruiter_greet", side_effect=OSError("disk read-only")):
		with _platform(platform):
			envelope, exit_code = _invoke(_greet_args(tmp_path))

	error = envelope["error"]
	assert exit_code == 1
	assert error["code"] == "LOCAL_STATE_WRITE_FAILED"
	assert error["recoverable"] is False
	assert error["details"]["request_sent"] is False
	assert error["details"]["recorded"] is False
	assert error["details"]["outcome_unknown"] is False
	assert error["details"]["replay_safe"] is False
	assert error["details"]["cause_code"] == "REQUEST_BUDGET_EXHAUSTED"
	assert "clean --pending-recruiter-greet" in error["recovery_action"]
	assert "--dry-run" in error["recovery_action"]
	assert "--all" not in error["recovery_action"]


def test_targeted_clean_releases_only_that_pending_and_unblocks_retry(tmp_path: Path) -> None:
	platform = _successful_platform()
	platform.start_chat.side_effect = RequestBudgetExceeded("budget")
	with patch.object(CacheStore, "release_recruiter_greet", side_effect=OSError("disk read-only")):
		with _platform(platform):
			_invoke(_greet_args(tmp_path))

	preview, _ = _invoke([
		"--data-dir", str(tmp_path), "--json", "clean",
		"--pending-recruiter-greet", "g", "j", "--dry-run",
	])
	assert _cleaned_count(preview, "招聘者招呼预约") == 1
	assert "--pending-recruiter-greet" in preview["hints"]["next_actions"][0]

	cleaned, _ = _invoke([
		"--data-dir", str(tmp_path), "--json", "clean",
		"--pending-recruiter-greet", "g", "j",
	])
	assert _cleaned_count(cleaned, "招聘者招呼预约") == 1

	retry = _successful_platform()
	with _platform(retry):
		envelope, exit_code = _invoke(_greet_args(tmp_path))
	assert exit_code == 0
	assert envelope["ok"] is True
	assert retry.start_chat.call_count == 1


def test_record_failure_after_sent_is_not_result_unknown(tmp_path: Path) -> None:
	platform = _successful_platform()
	with patch.object(CacheStore, "record_recruiter_greet", side_effect=OSError("disk read-only")):
		with _platform(platform):
			envelope, exit_code = _invoke(_greet_args(tmp_path))

	error = envelope["error"]
	assert exit_code == 1
	assert error["code"] == "LOCAL_STATE_WRITE_FAILED"
	assert error["details"]["sent"] is True
	assert error["details"]["recorded"] is False
	assert error["details"]["outcome_unknown"] is False
	assert error["details"]["replay_safe"] is False
	assert "核对" not in error["recovery_action"]
	assert "hr chat" in error["recovery_action"]


def test_record_failure_after_quota_rejection_points_to_targeted_clean(tmp_path: Path) -> None:
	platform = MagicMock()
	platform.is_success.return_value = False
	platform.parse_error.return_value = ("GREET_LIMIT", "quota")
	platform.start_chat.return_value = {"code": 42, "message": "quota"}
	with patch.object(CacheStore, "record_recruiter_greet", side_effect=OSError("disk read-only")):
		with _platform(platform):
			envelope, exit_code = _invoke(_greet_args(tmp_path))

	error = envelope["error"]
	assert exit_code == 1
	assert error["code"] == "LOCAL_STATE_WRITE_FAILED"
	assert error["details"]["sent"] is False
	assert error["details"]["outcome_unknown"] is False
	assert error["details"]["replay_safe"] is False
	assert "clean --pending-recruiter-greet" in error["recovery_action"]


def test_targeted_clean_preserves_sent_quota_and_other_candidates(tmp_path: Path) -> None:
	db_path = tmp_path / "cache" / "boss_agent.db"
	with CacheStore(db_path) as cache:
		assert cache.claim_recruiter_greet("g1", "j1") is None
		cache.record_recruiter_greet("g1", "j1")
		assert cache.claim_recruiter_greet("g2", "j2") is None
		assert cache.claim_recruiter_greet("g3", "j3") is None
		cache.record_recruiter_greet("g3", "j3", status="quota_limited")

	envelope, exit_code = _invoke([
		"--data-dir", str(tmp_path), "--json", "clean",
		"--pending-recruiter-greet", "g2", "j2",
	])
	assert exit_code == 0
	assert _cleaned_count(envelope, "招聘者招呼预约") == 1

	with CacheStore(db_path) as cache:
		assert cache.claim_recruiter_greet("g2", "j2") is None
		assert cache.claim_recruiter_greet("g1", "j1") == "sent"
		assert cache.claim_recruiter_greet("g3", "j3") == "quota_limited"


def test_targeted_clean_rejects_blank_ids_and_mixed_modes(tmp_path: Path) -> None:
	blank, exit_code = _invoke([
		"--data-dir", str(tmp_path), "--json", "clean",
		"--pending-recruiter-greet", "", "j",
	])
	assert exit_code == 1
	assert blank["error"]["code"] == "INVALID_PARAM"

	mixed, exit_code = _invoke([
		"--data-dir", str(tmp_path), "--json", "clean",
		"--pending-claims", "0" * 32,
		"--pending-recruiter-greet", "g", "j",
	])
	assert exit_code == 1
	assert mixed["error"]["code"] == "INVALID_PARAM"
