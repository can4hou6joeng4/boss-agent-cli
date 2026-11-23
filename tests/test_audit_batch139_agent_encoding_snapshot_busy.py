"""审计批次 139：agent 输入编码与聊天快照锁超时的错误分类（Issue #2537 / #2538）。

- `agent stop` / `agent review reject` 的 `--reason`、`agent crawl --run-id`
  含孤立代理项时，此前分别在 JSONL 写入层 / sqlite 绑定层炸开，被误报成
  `AUTOMATION_STATE_INVALID`（「状态文件损坏」）或指向无关的 crawl 配置；
- 聊天快照锁等待超时抛普通 `OSError`（带 filename），被 `is_local_io_error`
  判成不可重试的 `LOCAL_IO_ERROR` + 磁盘指引。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import click
from click.testing import CliRunner
import pytest

from boss_agent_cli.display import handle_auth_errors
from boss_agent_cli.main import BossCliGroup, cli
from boss_agent_cli.output import Logger
from boss_agent_cli.services.chat_snapshot import (
	ChatSnapshotBusyError,
	_load_previous_snapshot,
	_snapshot_lock,
)
from boss_agent_cli.services.file_lock import exclusive_file_lock, run_lock_path

_BAD = "\ud800"
_DATE = "2026-11-23"


def _invoke(tmp_path: Path, *args: str):
	return CliRunner().invoke(cli, ["--json", "--data-dir", str(tmp_path), *args])


def _payload(result) -> dict:
	assert result.exit_code == 1, result.output
	payload = json.loads(result.stdout)
	assert payload["ok"] is False
	return payload


def _error(result) -> dict:
	return _payload(result)["error"]


def test_agent_stop_rejects_unencodable_reason(tmp_path: Path) -> None:
	error = _error(_invoke(tmp_path, "agent", "stop", "--reason", _BAD))

	assert error["code"] == "INVALID_PARAM"
	assert "--reason" in error["message"]
	assert "UTF-8" in error["message"]
	# 不能把输入问题归因到状态文件，更不能引导删除 / 备份。
	assert "状态文件" not in error["message"]
	assert "备份" not in error["recovery_action"]
	assert not (tmp_path / "automation" / "stop.json").exists()


def test_agent_review_reject_rejects_unencodable_reason(tmp_path: Path) -> None:
	error = _error(_invoke(tmp_path, "agent", "review", "reject", "review-1", "--reason", _BAD))

	assert error["code"] == "INVALID_PARAM"
	assert "--reason" in error["message"]
	assert "队列" not in error["recovery_action"]
	assert "备份" not in error["recovery_action"]


def test_agent_crawl_rejects_unencodable_run_id_without_crawl_config_hint(tmp_path: Path) -> None:
	payload = _payload(_invoke(tmp_path, "agent", "crawl", "--run-id", _BAD, "--resume", "base"))
	error = payload["error"]

	assert error["code"] == "INVALID_PARAM"
	assert "UTF-8" in error["message"]
	assert "run-id" in error["message"]
	assert "configure" not in error["recovery_action"]
	assert all("configure" not in action for action in payload["hints"]["next_actions"])


def test_agent_stop_accepts_encodable_reason(tmp_path: Path) -> None:
	result = _invoke(tmp_path, "agent", "stop", "--reason", "manual-check")

	assert result.exit_code == 0, result.output
	payload = json.loads(result.stdout)
	assert payload["ok"] is True
	assert payload["data"]["reason"] == "manual-check"


def _hold_snapshot_lock(tmp_path: Path) -> tuple[Path, Any]:
	snapshot = tmp_path / f"{_DATE}.json"
	lock = run_lock_path(tmp_path, f"chat-snapshot-{snapshot.name}")
	return snapshot, exclusive_file_lock(lock)


def test_snapshot_lock_contention_raises_typed_busy_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	monkeypatch.setattr("boss_agent_cli.services.chat_snapshot._LOCK_ATTEMPTS", 2)
	monkeypatch.setattr("boss_agent_cli.services.chat_snapshot._LOCK_RETRY_INTERVAL_SECONDS", 0)
	snapshot, holder = _hold_snapshot_lock(tmp_path)

	with holder as acquired:
		assert acquired
		with pytest.raises(ChatSnapshotBusyError) as exc_info:
			with _snapshot_lock(str(snapshot)):
				pass

	assert isinstance(exc_info.value, OSError)
	assert "其他进程" in str(exc_info.value)


def test_previous_snapshot_still_degrades_when_lock_is_busy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	monkeypatch.setattr("boss_agent_cli.services.chat_snapshot._LOCK_ATTEMPTS", 2)
	monkeypatch.setattr("boss_agent_cli.services.chat_snapshot._LOCK_RETRY_INTERVAL_SECONDS", 0)
	snapshot, holder = _hold_snapshot_lock(tmp_path)
	snapshot.write_text("[]", encoding="utf-8")

	with holder as acquired:
		assert acquired
		# 读不到上一份快照时保持原有降级语义：返回 None 而不是抛错。
		assert _load_previous_snapshot(str(snapshot), Logger()) is None


def test_handle_auth_errors_maps_snapshot_busy_to_retryable_cache_busy() -> None:
	ctx = MagicMock()
	ctx.obj = {"json_output": True, "data_dir": Path("/tmp/boss-2538"), "platform": "zhipin"}

	@handle_auth_errors("chat")
	def impl(ctx):
		raise ChatSnapshotBusyError(11, "聊天快照正被其他进程写入，等待互斥锁超时", "/tmp/2026-11-23.json")

	with patch("boss_agent_cli.display.handle_error_output") as mock_err:
		impl(ctx)

	kwargs = mock_err.call_args.kwargs
	assert kwargs["code"] == "CACHE_BUSY"
	assert kwargs["recoverable"] is True
	assert "稍后重试" in kwargs["recovery_action"]
	assert "磁盘" not in kwargs["recovery_action"]
	assert "权限" not in kwargs["recovery_action"]


def test_cli_group_maps_snapshot_busy_to_cache_busy() -> None:
	@click.group(cls=BossCliGroup)
	def group() -> None:
		pass

	@group.command("boom")
	def boom() -> None:
		raise ChatSnapshotBusyError(11, "聊天快照正被其他进程写入", "/tmp/2026-11-23.json")

	result = CliRunner().invoke(group, ["boom"])

	assert result.exit_code == 1, result.output
	payload = json.loads(result.stdout)
	assert payload["error"]["code"] == "CACHE_BUSY"
	assert payload["error"]["recoverable"] is True
