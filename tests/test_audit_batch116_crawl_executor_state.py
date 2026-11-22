"""审计批次 116：crawl 执行器退出的状态与预检失败终态（Issue #2465、#2466）。

两个契约点：
1. DB 的 ``running`` 只有在执行锁仍被持有时才算「在跑」。执行器被 SIGKILL 后
   状态不会自己变：``crawl status`` 必须报出 stale，``crawl stop`` 必须按断点
   落 stopped，不能写一条没人消费的 stop 标记再报成功。
2. ``_run`` 的预检 / transport 构造失败也必须走 finally 写回终态：否则
   ``_resume_locked`` 已标记的 running 会永久留在断点里、清空上一轮错误，并让
   紧随其后的 ``crawl stop`` 假成功。
"""

import json
from contextlib import contextmanager
from pathlib import Path

import pytest
from click.testing import CliRunner

from boss_agent_cli.cache.store import CacheStore
from boss_agent_cli.commands import crawl as crawl_command
from boss_agent_cli.crawler.service import CrawlService, CrawlSettings, crawl_executor_active
from boss_agent_cli.main import cli
from boss_agent_cli.services.file_lock import exclusive_file_lock, run_lock_path


def _db(tmp_path: Path) -> Path:
	return tmp_path / "cache" / "boss_agent.db"


def _lock_path(tmp_path: Path, run_id: str = "run-1") -> Path:
	return run_lock_path(tmp_path / "cache" / "crawl-locks", run_id)


def _params(tmp_path: Path) -> dict:
	return {
		"query": "python",
		"city_code": "101210100",
		"pages": 2,
		"with_detail": False,
		"browser": {"profile_path": str(tmp_path / "profile"), "cdp_port": 9333},
		"chrome_path": None,
		"hook": {"profile": "none", "source": "user-supplied", "directory": None},
		"limits": {"max_requests": 20, "max_details": 50, "max_seconds": 600, "max_retries": 1},
		"operating_mode": "assisted",
	}


def _seed_run(tmp_path: Path, *, status: str = "running", error: str = "") -> str:
	run_id = "run-1"
	with CacheStore(_db(tmp_path)) as cache:
		cache.create_crawl_run(run_id, _params(tmp_path), str(tmp_path / "crawl" / run_id), status=status)
		if error:
			cache.update_crawl_run(run_id, status=status, next_page=1, error=error)
	return run_id


def _invoke(tmp_path: Path, *args: str):
	return CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "--json", "crawl", *args])


def _envelope(result) -> dict:
	assert result.exit_code == 0, result.output
	return json.loads(result.stdout)


def _expire_queue_grace(tmp_path: Path) -> None:
	"""把排队交接时间挪到宽限窗口之外，模拟 worker 从未启动。"""
	with CacheStore(_db(tmp_path)) as cache:
		cache._conn.execute("UPDATE crawl_runs SET updated_at = updated_at - 120 WHERE run_id = 'run-1'")
		cache._conn.commit()


# ── Issue #2465：status 识别执行器已退出的 running / queued ──────────


def test_status_marks_running_run_without_executor_as_stale(tmp_path):
	_seed_run(tmp_path, status="running")
	payload = _envelope(_invoke(tmp_path, "status", "run-1"))

	assert payload["data"]["status"] == "running"
	assert payload["data"]["executor_active"] is False
	assert payload["data"]["running_stale"] is True
	next_actions = payload["hints"]["next_actions"]
	assert any("crawl resume run-1 --background" in action for action in next_actions)
	assert not any("crawl stop" in action for action in next_actions), (
		"执行器已不在时提示 stop 只会得到一个空操作"
	)


def test_status_reports_live_executor_under_the_run_lock(tmp_path):
	_seed_run(tmp_path, status="running")
	with exclusive_file_lock(_lock_path(tmp_path)) as acquired:
		assert acquired
		payload = _envelope(_invoke(tmp_path, "status", "run-1"))

	assert payload["data"]["executor_active"] is True
	assert "running_stale" not in payload["data"]
	assert any("crawl stop" in action for action in payload["hints"]["next_actions"])


def test_status_queued_within_handoff_grace_counts_as_active(tmp_path):
	_seed_run(tmp_path, status="queued")
	payload = _envelope(_invoke(tmp_path, "status", "run-1"))

	assert payload["data"]["executor_active"] is True
	assert any("crawl stop" in action for action in payload["hints"]["next_actions"])


def test_status_queued_after_grace_without_worker_offers_resume(tmp_path):
	_seed_run(tmp_path, status="queued")
	_expire_queue_grace(tmp_path)
	payload = _envelope(_invoke(tmp_path, "status", "run-1"))

	assert payload["data"]["executor_active"] is False
	assert any("crawl resume run-1 --background" in action for action in payload["hints"]["next_actions"])


def test_status_terminal_run_never_probes_or_claims_stale(tmp_path):
	_seed_run(tmp_path, status="completed")
	payload = _envelope(_invoke(tmp_path, "status", "run-1"))

	assert payload["data"]["executor_active"] is False
	assert "running_stale" not in payload["data"]
	assert not any("crawl stop" in action for action in payload["hints"]["next_actions"])


# ── Issue #2465：stop 在无执行器时按断点落 stopped ───────────────────


def test_stop_recovers_stale_running_run_and_status_stops_claiming_running(tmp_path):
	_seed_run(tmp_path, status="running")
	payload = _envelope(_invoke(tmp_path, "stop", "run-1"))

	assert payload["data"] == {"run_id": "run-1", "status": "stopped", "stale_recovered": True}
	with CacheStore(_db(tmp_path)) as cache:
		run = cache.get_crawl_run("run-1")
	assert run is not None
	assert run["status"] == "stopped"
	assert run["stop_requested"] is True
	assert run["next_page"] == 1
	assert "resume" in run["error"]
	assert any("crawl resume run-1 --background" in action for action in payload["hints"]["next_actions"])

	# 落盘后 status 不再谎报运行中，也不再提示 stop。
	after = _envelope(_invoke(tmp_path, "status", "run-1"))
	assert after["data"]["status"] == "stopped"
	assert after["data"]["executor_active"] is False
	assert "running_stale" not in after["data"]
	assert not any("crawl stop" in action for action in after["hints"]["next_actions"])


def test_stop_with_live_executor_only_requests_stop(tmp_path):
	_seed_run(tmp_path, status="running")
	with exclusive_file_lock(_lock_path(tmp_path)) as acquired:
		assert acquired
		payload = _envelope(_invoke(tmp_path, "stop", "run-1"))

	assert payload["data"] == {"run_id": "run-1", "status": "stop_requested"}
	with CacheStore(_db(tmp_path)) as cache:
		run = cache.get_crawl_run("run-1")
	assert run["status"] == "running"
	assert run["stop_requested"] is True


def test_stop_recovers_stale_queued_run_after_handoff_grace(tmp_path):
	_seed_run(tmp_path, status="queued")
	_expire_queue_grace(tmp_path)
	payload = _envelope(_invoke(tmp_path, "stop", "run-1"))

	assert payload["data"] == {"run_id": "run-1", "status": "stopped", "stale_recovered": True}
	with CacheStore(_db(tmp_path)) as cache:
		assert cache.get_crawl_run("run-1")["status"] == "stopped"


@pytest.mark.parametrize("concurrent_status", ["running", "queued", "completed"])
def test_stop_checks_latest_checkpoint_under_execution_lock(tmp_path, monkeypatch, concurrent_status):
	_seed_run(tmp_path, status="running")
	write_stop = CacheStore.stop_active_crawl_run

	@contextmanager
	def lock_after_worker_progress(data_dir, run_id):
		path = _lock_path(data_dir, run_id)
		with exclusive_file_lock(path) as acquired:
			assert acquired
			with CacheStore(_db(tmp_path)) as cache:
				cache.update_crawl_run(run_id, status=concurrent_status, next_page=2, list_finished=True)
		with exclusive_file_lock(path) as acquired:
			assert acquired
			yield acquired

	def write_while_locked(cache, run_id, **kwargs):
		with exclusive_file_lock(_lock_path(tmp_path, run_id)) as acquired:
			assert not acquired
		return write_stop(cache, run_id, **kwargs)

	monkeypatch.setattr(crawl_command, "crawl_execution_lock", lock_after_worker_progress)
	monkeypatch.setattr(CacheStore, "stop_active_crawl_run", write_while_locked)
	result = _invoke(tmp_path, "stop", "run-1")
	with CacheStore(_db(tmp_path)) as cache:
		run = cache.get_crawl_run("run-1")
	assert run["next_page"] == 2
	assert run["list_finished"] is True
	if concurrent_status == "completed":
		assert result.exit_code == 1, result.output
		assert json.loads(result.stdout)["error"]["code"] == "CRAWL_RUN_FINISHED"
		assert run["status"] == "completed"
		assert run["stop_requested"] is False
	else:
		status = "stopped" if concurrent_status == "running" else "stop_requested"
		assert _envelope(result)["data"]["status"] == status
		assert run["status"] == ("stopped" if concurrent_status == "running" else "queued")
		assert run["stop_requested"] is True


def test_stop_reports_run_deleted_before_the_write(tmp_path, monkeypatch):
	_seed_run(tmp_path, status="running")
	write_stop = CacheStore.stop_active_crawl_run

	def delete_before_write(cache, run_id, **kwargs):
		with CacheStore(_db(tmp_path)) as other:
			other._conn.execute("DELETE FROM crawl_runs WHERE run_id = ?", (run_id,))
			other._conn.commit()
		return write_stop(cache, run_id, **kwargs)

	monkeypatch.setattr(CacheStore, "stop_active_crawl_run", delete_before_write)
	result = _invoke(tmp_path, "stop", "run-1")

	assert result.exit_code == 1, result.output
	assert json.loads(result.stdout)["error"]["code"] == "CRAWL_RUN_NOT_FOUND"


def test_stop_on_drifted_next_page_reports_corrupt_record_without_changes(tmp_path):
	# 断点数值列漂移与 status / resume 同口径：报 CACHE_RECORD_INVALID，
	# 失败的调用不改动任务（不能借 stale 探测顺手把行覆盖成 stopped）。
	_seed_run(tmp_path, status="running")
	with CacheStore(_db(tmp_path)) as cache:
		cache._conn.execute("UPDATE crawl_runs SET next_page = 'abc' WHERE run_id = 'run-1'")
		cache._conn.commit()
	result = _invoke(tmp_path, "stop", "run-1")

	assert result.exit_code == 1, result.output
	assert json.loads(result.stdout)["error"]["code"] == "CACHE_RECORD_INVALID"
	with CacheStore(_db(tmp_path)) as cache:
		row = cache._conn.execute(
			"SELECT status, stop_requested, next_page FROM crawl_runs WHERE run_id = 'run-1'"
		).fetchone()
	assert row == ("running", 0, "abc")


def test_crawl_executor_active_round_trips_the_execution_lock(tmp_path):
	assert crawl_executor_active(tmp_path, "run-1") is False
	with exclusive_file_lock(_lock_path(tmp_path)) as acquired:
		assert acquired
		assert crawl_executor_active(tmp_path, "run-1") is True
	assert crawl_executor_active(tmp_path, "run-1") is False
	# 探测释放时清掉锁文件，不在数据目录里留下孤儿。
	assert not _lock_path(tmp_path).exists()


# ── Issue #2466：预检 / 构造失败写回终态 ────────────────────────────


def _resume_with_failing_transport_factory(tmp_path: Path) -> str:
	run_id = _seed_run(tmp_path, status="stopped", error="previous reason")

	def factory(settings: CrawlSettings) -> object:
		raise RuntimeError("browser factory unavailable")

	with CacheStore(_db(tmp_path)) as cache:
		service = CrawlService(cache, data_dir=tmp_path, transport_factory=factory)
		outcome = service.resume(run_id)
		run = cache.get_crawl_run(run_id)
	assert outcome.status == "stopped"
	assert outcome.error == "browser factory unavailable"
	assert run is not None
	assert run["status"] == "stopped", "构造失败不能把断点永久留在 running"
	assert run["error"] == "browser factory unavailable"
	assert run["next_page"] == 1
	return run_id


def test_transport_factory_failure_writes_stopped_terminal_state(tmp_path):
	_resume_with_failing_transport_factory(tmp_path)


def test_failed_preflight_run_no_longer_accepts_a_fake_stop(tmp_path):
	_resume_with_failing_transport_factory(tmp_path)
	result = _invoke(tmp_path, "stop", "run-1")

	# 预检失败已落 stopped：stop 必须报终态，而不是写一条没人消费的停止标记。
	assert result.exit_code == 1, result.output
	assert json.loads(result.stdout)["error"]["code"] == "CRAWL_RUN_FINISHED"
	with CacheStore(_db(tmp_path)) as cache:
		run = cache.get_crawl_run("run-1")
	assert run["stop_requested"] is False
	assert run["status"] == "stopped"
