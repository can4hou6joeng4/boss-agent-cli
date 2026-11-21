"""第十七轮审计批次：后台恢复的锁内交接口径与 completed 快速路径校验（Issue #2401–#2403）。"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from boss_agent_cli.cache.store import CacheStore
from boss_agent_cli.commands.crawl import CrawlHandoffPending, _prepare_background_resume
from boss_agent_cli.main import cli

_RUN_ID = "run-1"


def _params(*, query: str = "AI", pages: int = 1) -> dict[str, Any]:
	return {
		"query": query,
		"city_code": "101210100",
		"pages": pages,
		"with_detail": False,
		"browser": {"profile_path": "/tmp/profile", "cdp_port": 9333},
		"chrome_path": None,
		"hook": {"profile": "none", "source": "user-supplied", "directory": None},
		"limits": {"max_requests": 20, "max_details": 50, "max_seconds": 600, "max_retries": 1},
		"operating_mode": "assisted",
	}


def _seed(
	tmp_path: Path,
	*,
	status: str = "stopped",
	query: str = "AI",
	pages: int = 1,
	next_page: int = 1,
) -> Path:
	db_path = tmp_path / "cache" / "boss_agent.db"
	db_path.parent.mkdir(parents=True, exist_ok=True)
	with CacheStore(db_path) as cache:
		cache.create_crawl_run(
			_RUN_ID,
			_params(query=query, pages=pages),
			str(tmp_path / "crawl" / _RUN_ID),
			status=status,
		)
	with sqlite3.connect(db_path) as connection:
		connection.execute("UPDATE crawl_runs SET next_page = ? WHERE run_id = ?", (next_page, _RUN_ID))
	return db_path


def _no_preflight() -> object:  # pragma: no cover - 错误路径不应触发预检
	raise AssertionError("预检不应在错误路径执行")


# ── Issue #2401：锁内重读进入交接窗口时同样拒绝显式参数 ─────────────


def test_inner_handoff_rejects_explicit_pages(tmp_path: Path, monkeypatch) -> None:
	_seed(tmp_path, status="stopped")
	real_get = CacheStore.get_crawl_run
	calls = {"n": 0}

	def racing_get(self: CacheStore, run_id: str) -> dict[str, Any] | None:
		calls["n"] += 1
		run = real_get(self, run_id)
		if calls["n"] == 2 and run is not None:
			# 锁外读取之后、取锁之前另一个进程已把 run 标成 queued。
			run = dict(run)
			run["status"] = "queued"
			run["updated_at"] = time.time()
		return run

	monkeypatch.setattr(CacheStore, "get_crawl_run", racing_get)

	with pytest.raises(CrawlHandoffPending):
		_prepare_background_resume(
			tmp_path,
			_RUN_ID,
			pages=7,
			with_detail=False,
			clear_stop=False,
			preflight=_no_preflight,
		)


# ── Issue #2402：completed 快速路径拒绝语义损坏的设置 ────────────────


def test_completed_fast_path_rejects_blank_query(tmp_path: Path) -> None:
	_seed(tmp_path, status="completed", query="   ")

	with pytest.raises(ValueError, match="搜索关键词不能为空"):
		_prepare_background_resume(
			tmp_path,
			_RUN_ID,
			pages=None,
			with_detail=False,
			clear_stop=False,
			preflight=_no_preflight,
		)


def test_inner_completed_fast_path_rejects_blank_query(tmp_path: Path, monkeypatch) -> None:
	"""锁外还是 stopped、锁内已 completed：内层快速路径同样要校验设置。"""
	_seed(tmp_path, status="stopped", query="   ")
	real_get = CacheStore.get_crawl_run
	calls = {"n": 0}

	def completing_get(self: CacheStore, run_id: str) -> dict[str, Any] | None:
		calls["n"] += 1
		run = real_get(self, run_id)
		if calls["n"] == 2 and run is not None:
			run = dict(run)
			run["status"] = "completed"
		return run

	monkeypatch.setattr(CacheStore, "get_crawl_run", completing_get)

	with pytest.raises(ValueError, match="搜索关键词不能为空"):
		_prepare_background_resume(
			tmp_path,
			_RUN_ID,
			pages=None,
			with_detail=False,
			clear_stop=False,
			preflight=_no_preflight,
		)


# ── Issue #2403：设置损坏与 --pages 冲突使用不同恢复指引 ────────────


def test_blank_query_recovery_points_to_new_task(tmp_path: Path) -> None:
	_seed(tmp_path, status="stopped", query="   ")

	result = CliRunner().invoke(
		cli, ["--data-dir", str(tmp_path), "--json", "crawl", "resume", _RUN_ID, "--background"]
	)

	assert result.exit_code == 1, result.output
	envelope = json.loads(result.stdout)
	assert envelope["error"]["code"] == "INVALID_PARAM"
	assert "新建任务" in envelope["error"]["recovery_action"]
	assert "调整 --pages" not in envelope["error"]["recovery_action"]
	next_actions = envelope["hints"]["next_actions"]
	assert any("crawl start" in action for action in next_actions)


def test_pages_conflict_recovery_points_to_pages(tmp_path: Path) -> None:
	_seed(tmp_path, status="stopped", pages=10, next_page=5)

	result = CliRunner().invoke(
		cli,
		["--data-dir", str(tmp_path), "--json", "crawl", "resume", _RUN_ID, "--background", "--pages", "1"],
	)

	assert result.exit_code == 1, result.output
	envelope = json.loads(result.stdout)
	assert envelope["error"]["code"] == "INVALID_PARAM"
	assert "把 --pages 调整到断点进度" in envelope["error"]["recovery_action"]
	assert "新建任务" not in envelope["error"]["recovery_action"]
