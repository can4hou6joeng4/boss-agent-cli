"""审计批次 119：crawl status 回报待处理的停止请求（Issue #2482）。

`crawl stop` 受理停止请求后只写 ``stop_requested=1``；执行器在消费它之前崩溃 /
被 SIGKILL 时，标记永远留在库里。此时 status 原来既不说还有停止请求，又建议
``crawl resume --background`` —— 而 resume 会先清除该标记，跟随工具指引就等于
静默撤销用户已受理的停止请求。

契约：
1. ``queued`` / ``running`` 的 status data 回报 ``stop_requested``；终态不回显；
2. 陈旧的 running / queued 在 ``stop_requested=true`` 时建议 ``crawl stop``
   （再执行一次会按断点结算 stopped），不再建议 ``crawl resume --background``；
3. 没有待处理停止请求时维持 #2465 / #2479 的 resume 建议；
4. 向导与 MCP 的 ``crawl_status`` 步骤同口径。
"""

import json
import shlex

from click.testing import CliRunner

from boss_agent_cli.cache.store import CacheStore
from boss_agent_cli.crawler.service import CrawlSettings, crawl_execution_lock
from boss_agent_cli.main import cli
from boss_agent_cli.output import Logger
from boss_agent_cli.wizard.actions import ActionContext, DEFAULT_ACTIONS

RUN_ID = "run-1"


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


def _seed(tmp_path, *, status: str, stop_requested: bool = False) -> None:
	with CacheStore(_db(tmp_path)) as cache:
		cache.create_crawl_run(RUN_ID, _params(tmp_path), str(tmp_path / "crawl" / "runs" / RUN_ID), status=status)
		if stop_requested:
			assert cache.request_crawl_stop(RUN_ID)


def _expire_update(tmp_path) -> None:
	with CacheStore(_db(tmp_path)) as cache:
		cache._conn.execute("UPDATE crawl_runs SET updated_at = updated_at - 120 WHERE run_id = ?", (RUN_ID,))
		cache._conn.commit()


def _status_actions(tmp_path) -> list[str]:
	result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "--json", "crawl", "status", RUN_ID])
	assert result.exit_code == 0, result.output
	payload = json.loads(result.stdout)
	assert payload["ok"] is True, payload
	actions = ((payload.get("hints") or {}).get("next_actions")) or []
	return actions


def _status_data(tmp_path) -> dict:
	result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "--json", "crawl", "status", RUN_ID])
	assert result.exit_code == 0, result.output
	return json.loads(result.stdout)["data"]


def _wizard_status(tmp_path):
	return DEFAULT_ACTIONS["crawl_status"](_context(tmp_path), {"run_id": RUN_ID}, {})


def test_stale_running_reports_pending_stop_and_suggests_settling_stop(tmp_path):
	_seed(tmp_path, status="running", stop_requested=True)
	data = _status_data(tmp_path)
	actions = _status_actions(tmp_path)

	assert data["status"] == "running"
	assert data["executor_active"] is False
	assert data["running_stale"] is True
	assert data["stop_requested"] is True
	assert any("crawl stop run-1" in action for action in actions)
	assert not any("crawl resume" in action for action in actions), "resume 会清除已受理的停止请求"


def test_stale_running_without_pending_stop_still_suggests_resume(tmp_path):
	_seed(tmp_path, status="running")
	actions = _status_actions(tmp_path)

	assert any("crawl resume run-1 --background" in action for action in actions)
	assert not any("crawl stop run-1" in action for action in actions)


def test_live_executor_with_pending_stop_keeps_waiting_on_stop(tmp_path):
	_seed(tmp_path, status="running", stop_requested=True)
	# 执行锁被持有：模拟执行器还在跑、尚未消费停止标记。
	with crawl_execution_lock(tmp_path, RUN_ID) as acquired:
		assert acquired
		data = _status_data(tmp_path)
		actions = _status_actions(tmp_path)

	assert data["executor_active"] is True
	assert "running_stale" not in data
	assert data["stop_requested"] is True
	assert any("crawl stop run-1" in action for action in actions)
	assert not any("crawl resume" in action for action in actions)


def test_overdue_queued_with_pending_stop_does_not_suggest_resume(tmp_path):
	_seed(tmp_path, status="queued", stop_requested=True)
	_expire_update(tmp_path)
	data = _status_data(tmp_path)
	actions = _status_actions(tmp_path)

	assert data["queue_overdue"] is True
	assert data["stop_requested"] is True
	assert any("crawl stop run-1" in action for action in actions)
	assert not any("crawl resume" in action for action in actions)


def test_overdue_queued_without_pending_stop_suggests_resume(tmp_path):
	_seed(tmp_path, status="queued")
	_expire_update(tmp_path)
	actions = _status_actions(tmp_path)

	assert any("crawl resume run-1 --background" in action for action in actions)


def test_terminal_run_does_not_replay_historical_stop_flag(tmp_path):
	_seed(tmp_path, status="stopped", stop_requested=True)
	data = _status_data(tmp_path)

	assert data["status"] == "stopped"
	assert "stop_requested" not in data
	assert "executor_active" in data


def test_wizard_status_settles_pending_stop_instead_of_resuming(tmp_path):
	_seed(tmp_path, status="running", stop_requested=True)
	result = _wizard_status(tmp_path)

	assert result.data["stop_requested"] is True
	assert result.next_action is not None
	assert shlex.split(result.next_action) == [
		"boss",
		"--data-dir",
		str(tmp_path.resolve()),
		"crawl",
		"stop",
		RUN_ID,
	]


def test_wizard_status_overdue_queued_pending_stop_targets_stop(tmp_path):
	_seed(tmp_path, status="queued", stop_requested=True)
	_expire_update(tmp_path)
	result = _wizard_status(tmp_path)

	assert result.data["queue_overdue"] is True
	assert result.next_action is not None
	assert shlex.split(result.next_action)[-2:] == ["stop", RUN_ID]


def test_wizard_status_without_pending_stop_keeps_resume_next_action(tmp_path):
	_seed(tmp_path, status="running")
	result = _wizard_status(tmp_path)

	assert result.data["stop_requested"] is False
	assert result.next_action is not None
	assert "--background" in result.next_action
