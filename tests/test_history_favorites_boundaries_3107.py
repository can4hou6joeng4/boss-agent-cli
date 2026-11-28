"""历史分页与收藏同步的只读错误契约（Issue #3107）。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from boss_agent_cli.main import cli
from boss_agent_cli.services.pagination import MAX_TRUSTED_PAGE


def _platform(payload: dict[str, Any], *, success: bool = True) -> MagicMock:
	platform = MagicMock()
	platform.name = "zhipin"
	platform.__enter__.return_value = platform
	platform.__exit__.return_value = None
	platform.is_success.side_effect = lambda raw: raw.get("code", 0) == 0 if success else False
	platform.unwrap_data.side_effect = lambda raw: raw.get("zpData")
	platform.parse_error.return_value = ("UNKNOWN", "平台未识别的失败")
	platform.job_history.return_value = payload
	platform.job_favorites.return_value = payload
	return platform


def _invoke(tmp_path: Path, *args: str):
	return CliRunner().invoke(cli, ["--json", "--data-dir", str(tmp_path), *args])


def test_history_uses_platform_corrected_page_for_next_hint(tmp_path: Path) -> None:
	platform = _platform({
		"code": 0,
		"zpData": {
			"page": 2,
			"hasMore": True,
			"jobList": [{"encryptJobId": "j1", "securityId": "s1", "jobName": "职位"}],
		},
	})
	with (
		patch("boss_agent_cli.commands.history.AuthManager"),
		patch("boss_agent_cli.commands.history.get_platform_instance", return_value=platform),
	):
		result = _invoke(tmp_path, "history", "--page", "1")

	assert result.exit_code == 0, result.output
	envelope = json.loads(result.output)
	assert envelope["pagination"]["page"] == 2
	assert envelope["pagination"]["total"] == 1
	assert any("history --page 3" in action for action in envelope["hints"]["next_actions"])
	platform.job_history.assert_called_once_with(1)


def test_history_still_stops_at_trusted_page_limit(tmp_path: Path) -> None:
	platform = _platform({
		"code": 0,
		"zpData": {
			"page": MAX_TRUSTED_PAGE,
			"hasMore": True,
			"jobList": [{"encryptJobId": "j1", "securityId": "s1", "jobName": "职位"}],
		},
	})
	with (
		patch("boss_agent_cli.commands.history.AuthManager"),
		patch("boss_agent_cli.commands.history.get_platform_instance", return_value=platform),
	):
		result = _invoke(tmp_path, "history", "--page", "1")

	assert result.exit_code == 0, result.output
	envelope = json.loads(result.output)
	assert envelope["pagination"]["page"] == MAX_TRUSTED_PAGE
	assert not any("history --page" in action for action in envelope["hints"]["next_actions"])
	assert envelope["hints"]["operator_actions"]


def test_favorites_sync_read_failure_is_retryable_before_any_local_write(tmp_path: Path) -> None:
	platform = _platform({"code": -1, "message": "未识别失败"}, success=False)
	with (
		patch("boss_agent_cli.commands.favorites.AuthManager"),
		patch("boss_agent_cli.commands.favorites.get_platform_instance", return_value=platform),
		patch("boss_agent_cli.commands.favorites.CacheStore") as cache_cls,
	):
		result = _invoke(tmp_path, "favorites", "sync")

	assert result.exit_code == 1, result.output
	error = json.loads(result.output)["error"]
	assert error["code"] == "NETWORK_ERROR"
	assert error["recoverable"] is True
	assert "核对本次操作是否生效" not in error["recovery_action"]
	assert cache_cls.call_count == 0
