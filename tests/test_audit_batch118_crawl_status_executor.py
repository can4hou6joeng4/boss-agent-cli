"""审计批次 118：向导与 MCP 的 crawl_status 回报执行器状态（Issue #2479）。

`boss crawl status` 在 #2465 后回报 ``executor_active`` / ``running_stale``，但
向导与 ``boss_wizard`` 的 ``crawl_status`` 步骤走 ``crawler/operations``，数据里
没有这些字段：执行器被 SIGKILL 后，状态步骤仍显示 ``running``，也不建议后台恢复。

契约：
1. 用同一次任务行读取计算 ``executor_active``（不引入第二次读取）；
2. ``running`` 且执行锁空闲时标记 ``running_stale``，``queued`` 超出交接窗口时
   标记 ``queue_overdue``；
3. 两种「没有执行器」的状态都在步骤 ``next_action`` 里给出带数据目录的
   ``crawl resume <run_id> --background``。
"""

import json
import shlex

from click.testing import CliRunner

from boss_agent_cli.cache.store import CacheStore
from boss_agent_cli.crawler.service import CrawlSettings, crawl_execution_lock
from boss_agent_cli.main import cli
from boss_agent_cli.output import Logger
from boss_agent_cli.wizard.actions import ActionContext, DEFAULT_ACTIONS


def _db(tmp_path):
	return tmp_path / "cache" / "boss_agent.db"


def _params(tmp_path) -> dict:
	return CrawlSettings(
		query="Python",
		city_code="101010100",
		pages=2,
		with_detail=False,
		profile_path=tmp_path / "crawl" / "chrome-profile",
		chrome_path=None,
		cdp_port=9333,
		hook_profile="none",
	).as_dict()


def _context(tmp_path) -> ActionContext:
	return ActionContext(data_dir=tmp_path, platform="zhipin", role="candidate", logger=Logger())


def _seed(tmp_path, *, status: str) -> None:
	with CacheStore(_db(tmp_path)) as cache:
		cache.create_crawl_run("run-1", _params(tmp_path), str(tmp_path / "crawl" / "runs" / "run-1"), status=status)


def _expire_update(tmp_path) -> None:
	with CacheStore(_db(tmp_path)) as cache:
		cache._conn.execute("UPDATE crawl_runs SET updated_at = updated_at - 120 WHERE run_id = 'run-1'")
		cache._conn.commit()


def _status(tmp_path):
	return DEFAULT_ACTIONS["crawl_status"](_context(tmp_path), {"run_id": "run-1"}, {})


def test_wizard_status_reports_live_executor(tmp_path):
	_seed(tmp_path, status="running")
	with crawl_execution_lock(tmp_path, "run-1") as acquired:
		assert acquired
		result = _status(tmp_path)

	assert result.data["executor_active"] is True
	assert "running_stale" not in result.data
	assert result.next_action is None


def test_wizard_status_marks_stale_running_and_hints_background_resume(tmp_path):
	_seed(tmp_path, status="running")
	result = _status(tmp_path)

	assert result.data["executor_active"] is False
	assert result.data["running_stale"] is True
	assert result.next_action is not None
	assert shlex.split(result.next_action) == [
		"boss",
		"--data-dir",
		str(tmp_path.resolve()),
		"crawl",
		"resume",
		"run-1",
		"--background",
	]


def test_wizard_status_fresh_queued_counts_as_active(tmp_path):
	_seed(tmp_path, status="queued")
	result = _status(tmp_path)

	assert result.data["executor_active"] is True
	assert "queue_overdue" not in result.data
	assert result.next_action is None


def test_wizard_status_overdue_queued_hints_background_resume(tmp_path):
	_seed(tmp_path, status="queued")
	_expire_update(tmp_path)
	result = _status(tmp_path)

	assert result.data["executor_active"] is False
	assert result.data["queue_overdue"] is True
	assert result.next_action is not None
	assert "--background" in result.next_action


def test_wizard_status_terminal_run_reports_no_executor(tmp_path):
	_seed(tmp_path, status="completed")
	result = _status(tmp_path)

	assert result.data["executor_active"] is False
	assert "running_stale" not in result.data
	assert "queue_overdue" not in result.data
	assert result.next_action is None


def test_wizard_status_uses_the_same_run_snapshot_for_executor_state(tmp_path, monkeypatch):
	"""执行器判定必须基于组装状态用的那一行，不能在两次读取之间漂移（#2476 口径）。"""
	_seed(tmp_path, status="running")
	get_crawl_run = CacheStore.get_crawl_run
	reads = 0

	def read_during_status(cache, requested_id):
		nonlocal reads
		record = get_crawl_run(cache, requested_id)
		reads += 1
		if reads == 1:
			with CacheStore(_db(tmp_path)) as other:
				other.update_crawl_run(requested_id, status="completed", next_page=2)
		return record

	monkeypatch.setattr(CacheStore, "get_crawl_run", read_during_status)
	result = _status(tmp_path)

	assert result.data["status"] == "running"
	assert result.data["executor_active"] is False
	assert result.data["running_stale"] is True
	assert reads == 1


def test_cli_status_exposes_queue_overdue_for_agents(tmp_path):
	_seed(tmp_path, status="queued")
	_expire_update(tmp_path)
	result = CliRunner().invoke(
		cli, ["--data-dir", str(tmp_path), "--json", "crawl", "status", "run-1"]
	)

	assert result.exit_code == 0, result.output
	payload = json.loads(result.stdout)
	assert payload["data"]["executor_active"] is False
	assert payload["data"]["queue_overdue"] is True
	assert any(
		"crawl resume run-1 --background" in action for action in payload["hints"]["next_actions"]
	)
