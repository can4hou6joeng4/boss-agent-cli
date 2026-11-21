"""第十九轮审计批次：前台 resume 指南、shortlist 空 run_id 与执行锁占用时的显式参数（Issue #2410–#2412）。"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from boss_agent_cli.cache.store import CacheStore
from boss_agent_cli.commands.crawl import CrawlHandoffPending, _prepare_background_resume
from boss_agent_cli.crawler.service import CrawlService
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
) -> None:
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


def _no_preflight() -> object:  # pragma: no cover - 错误路径不应触发预检
	raise AssertionError("预检不应在错误路径执行")


def _invoke(tmp_path: Path, *args: str) -> Any:
	return CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "--json", *args])


# ── Issue #2412：前台 resume 的参数冲突与断点损坏各给精确指引 ────────


def test_foreground_pages_conflict_recovery_points_to_pages(tmp_path: Path) -> None:
	_seed(tmp_path, status="stopped", pages=10, next_page=5)

	result = _invoke(tmp_path, "crawl", "resume", _RUN_ID, "--pages", "1")

	assert result.exit_code == 1, result.output
	envelope = json.loads(result.stdout)
	assert envelope["error"]["code"] == "INVALID_PARAM"
	assert "把 --pages 调整到断点进度" in envelope["error"]["recovery_action"]
	assert "crawl configure" not in envelope["error"]["recovery_action"]
	assert any("crawl status" in action for action in envelope["hints"]["next_actions"])


def test_foreground_blank_query_recovery_points_to_new_task(tmp_path: Path) -> None:
	_seed(tmp_path, status="stopped", query="   ")

	result = _invoke(tmp_path, "crawl", "resume", _RUN_ID)

	assert result.exit_code == 1, result.output
	envelope = json.loads(result.stdout)
	assert envelope["error"]["code"] == "INVALID_PARAM"
	assert "断点里的采集设置无效" in envelope["error"]["recovery_action"]
	assert "crawl configure" not in envelope["error"]["recovery_action"]
	next_actions = envelope["hints"]["next_actions"]
	assert any("crawl status" in action for action in next_actions)
	assert any("crawl start" in action for action in next_actions)


# ── Issue #2411：shortlist 的空 run_id 按参数错误上报 ────────────────


def test_shortlist_rejects_blank_run_id(tmp_path: Path) -> None:
	_seed(tmp_path, status="completed")

	result = _invoke(tmp_path, "crawl", "shortlist", "", "--all")

	assert result.exit_code == 1, result.output
	envelope = json.loads(result.stdout)
	assert envelope["error"]["code"] == "INVALID_PARAM"
	assert "run_id 不能为空" in envelope["error"]["message"]


# ── Issue #2410：执行锁被占用时显式恢复参数不能静默丢弃 ──────────────


def _prepare_with_lock_held(
	tmp_path: Path,
	*,
	pages: int | None,
	with_detail: bool = False,
	clear_stop: bool = False,
	stop_requested: bool = False,
) -> tuple[str, bool]:
	_seed(tmp_path, status="running")
	with CacheStore(tmp_path / "cache" / "boss_agent.db") as cache:
		if stop_requested:
			assert cache.request_crawl_stop(_RUN_ID) is True
		service = CrawlService(cache, data_dir=tmp_path, transport_factory=lambda settings: None)
		with service.execution_lock(_RUN_ID) as acquired:
			assert acquired
			return _prepare_background_resume(
				tmp_path,
				_RUN_ID,
				pages=pages,
				with_detail=with_detail,
				clear_stop=clear_stop,
				preflight=_no_preflight,
			)


def test_lock_held_rejects_explicit_pages(tmp_path: Path) -> None:
	with pytest.raises(CrawlHandoffPending, match="恢复参数尚未生效"):
		_prepare_with_lock_held(tmp_path, pages=7)


def test_lock_held_rejects_with_detail(tmp_path: Path) -> None:
	with pytest.raises(CrawlHandoffPending, match="恢复参数尚未生效"):
		_prepare_with_lock_held(tmp_path, pages=None, with_detail=True)


def test_lock_held_rejects_pending_stop_clear(tmp_path: Path) -> None:
	with pytest.raises(CrawlHandoffPending, match="恢复参数尚未生效"):
		_prepare_with_lock_held(tmp_path, pages=None, clear_stop=True, stop_requested=True)


def test_lock_held_status_only_still_reports_running(tmp_path: Path) -> None:
	assert _prepare_with_lock_held(tmp_path, pages=None) == ("running", False)
