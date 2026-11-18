"""停止开关路径为非普通文件时的 fail-closed 回归测试（Issue #1675）。

`automation/stop.json` 是目录、指向目录的符号链接或悬空符号链接时，自动化读取器
必须按「状态未知」熔断；`hr location` 不能把它当成「没有停止开关」继续预览或发送。
"""

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

from click.testing import CliRunner
import pytest

from boss_agent_cli.automation.storage import AutomationStore
from boss_agent_cli.commands.recruiter.location import _stop_reason
from boss_agent_cli.main import cli

FRIEND_ID = 10001
_SHAPES = ("directory", "symlink_to_directory", "dangling_symlink")


def _make_stop_path(tmp_path: Path, shape: str) -> Path:
	root = tmp_path / "automation"
	root.mkdir()
	stop_path = root / "stop.json"
	if shape == "directory":
		stop_path.mkdir()
	elif shape == "symlink_to_directory":
		target = tmp_path / "target"
		target.mkdir()
		stop_path.symlink_to(target, target_is_directory=True)
	else:
		stop_path.symlink_to(tmp_path / "missing-target")
	return stop_path


def _invoke(tmp_path: Path, *args: str):
	with patch("boss_agent_cli.commands.recruiter.location.AuthManager") as auth, patch(
		"boss_agent_cli.commands.recruiter.location.get_recruiter_platform_instance"
	) as factory:
		result = CliRunner().invoke(
			cli, ["--data-dir", str(tmp_path), "--json", "hr", "location", str(FRIEND_ID), *args]
		)
	return result, auth, factory


def _payload(result: Any) -> dict[str, Any]:
	return json.loads(result.output)


@pytest.mark.parametrize("shape", _SHAPES)
def test_read_stop_fails_closed_on_non_regular_path(tmp_path: Path, shape: str) -> None:
	_make_stop_path(tmp_path, shape)

	latch = AutomationStore(tmp_path).read_stop()
	assert latch["open"] is True
	assert latch["corrupt"] is True
	assert "stop.json" in latch["reason"]


@pytest.mark.parametrize("shape", _SHAPES)
def test_location_stop_check_matches_read_stop(tmp_path: Path, shape: str) -> None:
	_make_stop_path(tmp_path, shape)

	assert _stop_reason(tmp_path) is not None


def test_location_preview_is_blocked_by_non_regular_stop_path(tmp_path: Path) -> None:
	_make_stop_path(tmp_path, "directory")

	result, auth, factory = _invoke(tmp_path, "--dry-run")
	assert result.exit_code == 1, result.output
	payload = _payload(result)
	assert payload["ok"] is False
	assert payload["error"]["code"] == "CIRCUIT_BREAKER_OPEN"
	assert "stop.json" in payload["error"]["message"]
	auth.assert_not_called()
	factory.assert_not_called()


def test_missing_stop_file_still_previews_without_creating_automation_dir(tmp_path: Path) -> None:
	result, auth, factory = _invoke(tmp_path, "--dry-run")
	assert result.exit_code == 0, result.output
	assert _payload(result)["data"] == {"friend_id": FRIEND_ID, "sent": False, "dry_run": True}
	auth.assert_not_called()
	factory.assert_not_called()
	assert not (tmp_path / "automation").exists()
