"""显式恢复时的停止标记路径校验（Issue #1678）。

同名目录会使 stop.json 的原子替换失败。目录与指向目录的符号链接都应在
写入 state.json 之前报告状态损坏。
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner
import pytest

from boss_agent_cli.automation.storage import AutomationStateError, AutomationStore
from boss_agent_cli.main import cli

_SHAPES = ("directory", "symlink_to_directory")


def _make_stop_path(tmp_path: Path, shape: str) -> Path:
	root = tmp_path / "automation"
	root.mkdir()
	stop_path = root / "stop.json"
	if shape == "directory":
		stop_path.mkdir()
	else:
		target = tmp_path / "target"
		target.mkdir()
		stop_path.symlink_to(target, target_is_directory=True)
	return stop_path


@pytest.mark.parametrize("shape", _SHAPES)
def test_resume_refuses_non_regular_stop_path(tmp_path: Path, shape: str) -> None:
	stop_path = _make_stop_path(tmp_path, shape)

	with pytest.raises(AutomationStateError) as caught:
		AutomationStore(tmp_path).resume()

	assert caught.value.path == stop_path
	assert "不是普通文件" in caught.value.detail
	# 失败必须发生在写 state.json 之前，否则会留下一次半写入的 closed 快照。
	assert not (tmp_path / "automation" / "state.json").exists()


def test_resume_still_replaces_dangling_stop_symlink(tmp_path: Path) -> None:
	root = tmp_path / "automation"
	root.mkdir()
	(root / "stop.json").symlink_to(tmp_path / "missing-target")

	store = AutomationStore(tmp_path)
	assert store.resume() is True
	assert store.read_stop()["open"] is False
	assert (root / "stop.json").is_file()


def test_agent_run_resume_reports_state_invalid_for_non_regular_stop_path(tmp_path: Path) -> None:
	_make_stop_path(tmp_path, "directory")

	with patch("boss_agent_cli.commands.agent.build_automation_adapter", return_value=object()):
		result = CliRunner().invoke(
			cli,
			["--json", "--data-dir", str(tmp_path), "--role", "recruiter", "agent", "run", "--resume"],
		)

	assert result.exit_code == 1, result.output
	payload = json.loads(result.stdout)
	assert payload["ok"] is False
	assert payload["error"]["code"] == "AUTOMATION_STATE_INVALID"
	assert "stop.json" in payload["error"]["message"]
	assert "不是普通文件" in payload["error"]["message"]
	assert not (tmp_path / "automation" / "state.json").exists()
