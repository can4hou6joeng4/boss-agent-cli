"""wizard --resume 的控制读取不被展示用的实时采集叠加挡住（Issue #1176）。

`WorkflowStore.get()` 会叠加内部 crawl 的实时结果；`--status` 把损坏报出来
是展示契约，`--resume` 只需断点里的 plan 与步骤状态。此前两者都走 `get()`，
一条坏 crawl 记录会把已完成、可恢复的任务判成 `CACHE_RECORD_INVALID`，并
提示「原记录无法恢复，重新创建任务」。
"""

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from boss_agent_cli.cache.store import CacheRecordInvalid
from boss_agent_cli.main import cli
from boss_agent_cli.wizard.models import WorkflowPlan
from boss_agent_cli.wizard.store import WorkflowStore


def _seed_completed_crawl_run(tmp_path: Path, *, corrupt_inner: bool = True) -> None:
	"""(goal=crawl_resume, 已完成的 run) + 绑定的内部 crawl 记录。"""
	plan = WorkflowPlan(
		role="candidate",
		platform="zhipin",
		goal="crawl_resume",
		inputs={"run_id": "inner-corrupt"},
		requested_steps=("crawl_resume",),
		mode="headless",
	)
	with WorkflowStore(tmp_path) as store:
		store.create("run-corrupt-crawl", plan)
		store.update_step(
			"run-corrupt-crawl",
			"crawl_resume",
			status="completed",
			result={"inner_run_id": "inner-corrupt"},
		)
		store.update_run("run-corrupt-crawl", status="completed", current_step=None)
		store._cache.create_crawl_run(
			"inner-corrupt",
			{"run_id": "inner-corrupt"},
			str(tmp_path / "crawl" / "runs" / "inner-corrupt"),
		)
		if corrupt_inner:
			store._cache._conn.execute("UPDATE crawl_runs SET params = '[]' WHERE run_id = 'inner-corrupt'")
			store._cache._conn.commit()


def _invoke(tmp_path: Path, *args: str):
	return CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "--json", *args])


def test_resume_completed_run_when_inner_crawl_is_corrupt(tmp_path):
	"""断点与步骤完好时，坏展示数据不能把 --resume 判成不可恢复。"""
	_seed_completed_crawl_run(tmp_path)
	result = _invoke(tmp_path, "wizard", "--resume", "run-corrupt-crawl")
	assert result.exit_code == 0, result.output
	payload = json.loads(result.output)
	assert payload["ok"] is True
	assert payload["data"]["run_id"] == "run-corrupt-crawl"
	assert payload["data"]["status"] == "completed"


def test_status_still_reports_corrupt_inner_crawl(tmp_path):
	"""状态查询是展示路径：坏 crawl 记录仍按 CACHE_RECORD_INVALID 报出来。"""
	_seed_completed_crawl_run(tmp_path)
	result = _invoke(tmp_path, "wizard", "--status", "run-corrupt-crawl")
	assert result.exit_code == 1, result.output
	assert json.loads(result.output)["error"]["code"] == "CACHE_RECORD_INVALID"


def test_resume_still_rejects_corrupt_checkpoint(tmp_path):
	"""断点自身写坏时 --resume 仍拒绝，不能被结果读取的退化路径掩盖。"""
	_seed_completed_crawl_run(tmp_path)
	with WorkflowStore(tmp_path) as store:
		store._cache._conn.execute("UPDATE workflow_runs SET params = '[]' WHERE run_id = 'run-corrupt-crawl'")
		store._cache._conn.commit()
	result = _invoke(tmp_path, "wizard", "--resume", "run-corrupt-crawl")
	assert result.exit_code == 1, result.output
	assert json.loads(result.output)["error"]["code"] == "CACHE_RECORD_INVALID"


def test_get_for_result_falls_back_to_checkpoint(tmp_path):
	"""结果读取优先叠加，叠加失败退回断点；get() 本身仍抛损坏。"""
	_seed_completed_crawl_run(tmp_path)
	with WorkflowStore(tmp_path) as store:
		with pytest.raises(CacheRecordInvalid):
			store.get("run-corrupt-crawl")
		run = store.get_for_result("run-corrupt-crawl")
	assert run is not None
	assert run["status"] == "completed"
	assert "live_crawl_status" not in run


def test_get_for_result_keeps_live_overlay_when_healthy(tmp_path):
	"""叠加数据完好时输出与 get() 一致，不因退化路径丢掉实时采集字段。"""
	_seed_completed_crawl_run(tmp_path, corrupt_inner=False)
	with WorkflowStore(tmp_path) as store:
		store._cache._conn.execute("UPDATE crawl_runs SET status = 'completed' WHERE run_id = 'inner-corrupt'")
		store._cache._conn.commit()
		run = store.get_for_result("run-corrupt-crawl")
	assert run is not None
	assert run["live_crawl_status"] == "completed"
	assert run["live_jobs_seen"] == 0
