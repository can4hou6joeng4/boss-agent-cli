"""停止标记写入路径的回归测试（Issue #1684）。

`agent stop` 不能对目录形态的 stop.json 做原子替换；写入前必须报实际路径，
不能落到泛化的 LOCAL_IO_ERROR。悬空符号链接仍可被 os.replace 替换。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

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


def _invoke(tmp_path: Path, *args: str):
	return CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "--json", *args])


@pytest.mark.parametrize("shape", _SHAPES)
def test_request_stop_rejects_non_regular_stop_path(tmp_path: Path, shape: str) -> None:
	stop_path = _make_stop_path(tmp_path, shape)

	with pytest.raises(AutomationStateError) as caught:
		AutomationStore(tmp_path).request_stop("manual-stop")

	assert caught.value.path == stop_path
	assert "不是普通文件" in caught.value.detail
	assert stop_path.is_dir()


def test_request_stop_still_replaces_dangling_symlink(tmp_path: Path) -> None:
	root = tmp_path / "automation"
	root.mkdir()
	stop_path = root / "stop.json"
	stop_path.symlink_to(tmp_path / "missing-target")

	store = AutomationStore(tmp_path)
	store.request_stop("manual-stop")

	assert store.read_stop()["open"] is True
	assert store.read_stop()["reason"] == "manual-stop"
	assert stop_path.is_file()


def test_agent_stop_reports_state_invalid_for_non_regular_stop_path(tmp_path: Path) -> None:
	_make_stop_path(tmp_path, "directory")

	result = _invoke(tmp_path, "agent", "stop")

	assert result.exit_code == 1, result.output
	payload: dict[str, Any] = json.loads(result.output)
	assert payload["ok"] is False
	assert payload["error"]["code"] == "AUTOMATION_STATE_INVALID"
	assert "stop.json" in payload["error"]["message"]
	assert "不是普通文件" in payload["error"]["message"]
