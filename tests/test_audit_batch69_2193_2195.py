"""crawl 控制路径的三处缺陷（Issue #2193 / #2194 / #2195）。

1. 有限远未来的 ``updated_at`` 不能让 queued 任务永远停在「刚排队」窗口：
   后台恢复必须探测执行锁并拉起 worker，status 必须给出 queue_overdue 提示。
2. ``output_dir`` 含控制字符时断点读取边界 fail-closed（与 profile_path 同
   口径），status 与 resume 对同一断点给同一个 ``CACHE_RECORD_INVALID``。
3. 后台恢复把状态落成 queued 失败时同样还原已清除的旧 stop，保持「失败的
   调用不改动任务」。
"""

import json
import sqlite3
import time
from pathlib import Path

import pytest
from click.testing import CliRunner

from boss_agent_cli.cache.store import CacheRecordInvalid, CacheStore
from boss_agent_cli.commands.crawl import _awaiting_queued_worker, _prepare_background_resume
from boss_agent_cli.crawler.service import _artifacts_present
from boss_agent_cli.main import cli


def _seed_run(
	tmp_path: Path,
	*,
	status: str = "queued",
	output_dir: str | None = None,
	updated_at: str = "0",
	stop_requested: int = 0,
) -> None:
	params = {
		"query": "AI",
		"city_code": "101210100",
		"pages": 1,
		"with_detail": False,
		"browser": {"profile_path": str(tmp_path / "profile"), "cdp_port": 9333},
		"chrome_path": None,
		"hook": {"profile": "none", "source": "user-supplied", "directory": None},
		"limits": {"max_requests": 20, "max_details": 50, "max_seconds": 600, "max_retries": 1},
		"operating_mode": "assisted",
	}
	db_path = tmp_path / "cache" / "boss_agent.db"
	db_path.parent.mkdir(parents=True, exist_ok=True)
	with CacheStore(db_path) as cache:
		cache.create_crawl_run("run-1", params, output_dir or str(tmp_path / "crawl" / "run-1"), status=status)
	with sqlite3.connect(db_path) as connection:
		connection.execute(
			"UPDATE crawl_runs SET updated_at = ?, stop_requested = ? WHERE run_id = 'run-1'",
			(updated_at, stop_requested),
		)


def _invoke(tmp_path: Path, *args: str):
	return CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "--json", "crawl", *args])


def _stop_value(tmp_path: Path) -> int:
	db_path = tmp_path / "cache" / "boss_agent.db"
	with sqlite3.connect(db_path) as connection:
		return connection.execute("SELECT stop_requested FROM crawl_runs WHERE run_id = 'run-1'").fetchone()[0]


# ── Issue #2193：有限远未来时间戳按排队已久处理 ──────────────────────────


@pytest.mark.parametrize("offset", [86400.0, 1e9, 9e307])
def test_awaiting_queued_worker_treats_far_future_timestamp_as_stale(offset):
	now = time.time()
	assert _awaiting_queued_worker({"status": "queued", "updated_at": now + offset}) is False


def test_awaiting_queued_worker_tolerates_small_clock_skew():
	assert _awaiting_queued_worker({"status": "queued", "updated_at": time.time() + 5}) is True


def test_prepare_background_resume_launches_worker_for_far_future_timestamp(tmp_path):
	_seed_run(tmp_path, status="queued", updated_at=repr(time.time() + 86400))

	status, should_launch = _prepare_background_resume(
		tmp_path, "run-1", pages=None, with_detail=False, clear_stop=True, preflight=lambda: None
	)

	assert (status, should_launch) == ("queued", True)


def test_status_marks_far_future_queue_as_overdue(tmp_path):
	_seed_run(tmp_path, status="queued", updated_at=repr(time.time() + 86400))

	result = _invoke(tmp_path, "status", "run-1")

	assert result.exit_code == 0, result.output
	payload = json.loads(result.stdout)
	next_actions = payload["hints"]["next_actions"]
	assert any("crawl resume run-1 --background" in action for action in next_actions)


# ── Issue #2194：output_dir 控制字符在读取边界 fail-closed ────────────────


def test_get_crawl_run_rejects_control_characters_in_output_dir(tmp_path):
	_seed_run(tmp_path, status="completed", output_dir="/tmp/crawl\x00bad")

	with CacheStore(tmp_path / "cache" / "boss_agent.db") as cache:
		with pytest.raises(CacheRecordInvalid, match="控制字符"):
			cache.get_crawl_run("run-1")


def test_status_and_resume_agree_on_control_character_output_dir(tmp_path):
	_seed_run(tmp_path, status="completed", output_dir="/tmp/crawl\x00bad")

	for args in (("status", "run-1"), ("resume", "run-1")):
		result = _invoke(tmp_path, *args)
		assert result.exit_code == 1, (args, result.output)
		payload = json.loads(result.stdout)
		assert payload["ok"] is False, args
		assert payload["error"]["code"] == "CACHE_RECORD_INVALID", args


def test_artifacts_present_treats_value_error_as_missing():
	assert _artifacts_present(Path("/tmp/crawl\x00bad")) is False


# ── Issue #2195：状态落盘失败时还原已清除的 stop ─────────────────────────


def test_background_resume_restores_stop_when_mark_active_fails(tmp_path, monkeypatch):
	_seed_run(tmp_path, status="stopped", stop_requested=1)

	def explode(self, run_id, status):
		raise OSError("模拟本地 IO 失败")

	monkeypatch.setattr(CacheStore, "mark_crawl_run_active", explode)

	with pytest.raises(OSError, match="模拟本地 IO 失败"):
		_prepare_background_resume(
			tmp_path, "run-1", pages=None, with_detail=False, clear_stop=True, preflight=lambda: None
		)

	assert _stop_value(tmp_path) == 1
