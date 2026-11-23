"""clean 的信封与预览口径（Issue #2639）。"""

from __future__ import annotations

import os
import time
from pathlib import Path

from click.testing import CliRunner

from boss_agent_cli.api.cdp_risk_lock import read_lock, write_lock
from boss_agent_cli.commands import clean as clean_module
from boss_agent_cli.main import cli


def _invoke(*args: str, data_dir: Path) -> tuple[int, dict | None]:
	runner = CliRunner()
	result = runner.invoke(cli, ["--json", "--data-dir", str(data_dir), *args])
	return result.exit_code, __import__("json").loads(result.stdout) if result.stdout.strip() else None


def _age(path: Path, days: int) -> None:
	stamp = time.time() - days * 24 * 3600
	os.utime(path, (stamp, stamp))


def test_clean_risk_lock_then_privacy_failure_reports_lock_state(tmp_path, monkeypatch):
	"""清锁成功、隐私清理失败时，信封必须带上锁已被清除的事实（#1830 / #2639）。"""
	write_lock(tmp_path, None)

	# 隐私块的最后一项目标失败：前面的登录态 / 简历等已经处理完。
	def _fail(data_dir, *, dry_run):
		raise OSError(13, "Permission denied", str(data_dir / "crawl"))

	monkeypatch.setattr(clean_module, "_clean_crawl_data", _fail)
	code, payload = _invoke("clean", "--risk-lock", "--privacy", data_dir=tmp_path)

	assert code == 1
	assert payload["ok"] is False
	assert payload["error"]["code"] == "LOCAL_IO_ERROR"
	assert payload["data"]["risk_lock"] == {"local_lock_removed": True, "platform_risk_status": "unverified"}
	assert "不会回滚" in payload["error"]["message"]
	assert any("不需要因为本次失败重新执行 clean --risk-lock" in item for item in payload["hints"]["operator_actions"])
	assert read_lock(tmp_path) is None


def test_clean_privacy_failure_without_lock_omits_risk_lock(tmp_path, monkeypatch):
	"""没有清锁时不得凭空塞入 risk_lock。"""

	def _fail(data_dir, *, dry_run):
		raise OSError(13, "Permission denied", str(data_dir / "crawl"))

	monkeypatch.setattr(clean_module, "_clean_crawl_data", _fail)
	code, payload = _invoke("clean", "--risk-lock", "--privacy", data_dir=tmp_path)

	assert code == 1
	assert "risk_lock" not in payload["data"]
	assert "不会回滚" not in payload["error"]["message"]


def test_clean_privacy_dry_run_does_not_double_count_aged_files(tmp_path):
	"""--privacy --dry-run 与实际执行报告相同的释放字节数（#2639）。

	预览不删除文件，旧快照会在第 3 步与第 5 步被各统计一次；现在由第 5 步
	按整目录统计，实际执行也删掉整目录，两者一致。
	"""
	snapshots = tmp_path / "chat-history"
	snapshots.mkdir()
	old = snapshots / "old-snapshot.json"
	old.write_bytes(b"x" * 1234)
	_age(old, 40)
	fresh = snapshots / "fresh.json"
	fresh.write_bytes(b"y" * 10)

	code, preview = _invoke("clean", "--privacy", "--dry-run", "--days", "30", data_dir=tmp_path)
	assert code == 0
	preview_total = preview["data"]["total_bytes_freed"]

	code, real = _invoke("clean", "--privacy", "--days", "30", data_dir=tmp_path)
	assert code == 0
	assert real["data"]["total_bytes_freed"] == preview_total == 1244
	assert not old.exists()
	# --privacy 整目录清理：同一目录里未到期的文件也会一并删除，预览口径与之一致。
	assert not fresh.exists()
