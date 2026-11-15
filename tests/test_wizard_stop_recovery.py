"""workflow 停止标记与内部采集停止标记之间的中断必须可补偿。"""

import json
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from boss_agent_cli.cache.store import CacheBusyError, CacheStore
from boss_agent_cli.main import cli
from boss_agent_cli.wizard.models import WorkflowPlan
from boss_agent_cli.wizard.store import WorkflowStore


def _create_bound_run(data_dir, binding):
	plan = WorkflowPlan(
		role="candidate", platform="zhipin", goal="crawl_start",
		inputs={"query": "Python", "city": "北京"},
		requested_steps=("crawl_start",), mode="headless",
	)
	with WorkflowStore(data_dir) as store:
		store.create("outer-stop", plan)
		store.update_run("outer-stop", status="running", current_step="crawl_start")
		store.update_step("outer-stop", "crawl_start", status="running", result=binding)
		store.cache.create_crawl_run("inner-stop", {"query": "Python"}, str(data_dir / "crawl" / "inner-stop"))


def _stop(data_dir):
	return CliRunner().invoke(cli, ["--json", "--data-dir", str(data_dir), "wizard", "--stop", "outer-stop"])


@pytest.mark.parametrize("binding", [{"inner_run_id": "inner-stop"}, {"data": {"run_id": "inner-stop"}}])
def test_stop_repairs_crash_after_outer_marker(tmp_path, binding):
	_create_bound_run(tmp_path, binding)
	with WorkflowStore(tmp_path) as store:
		# 模拟外层 stop 已提交、尚未传播到内部 crawl 时执行器消失。
		assert store.cache.request_workflow_stop("outer-stop") is True
		assert store.cache.get_crawl_run("inner-stop")["stop_requested"] is False

	result = _stop(tmp_path)

	assert result.exit_code == 0, result.output
	assert json.loads(result.stdout)["data"]["status"] == "stopped"
	with WorkflowStore(tmp_path) as store:
		assert store.cache.get_crawl_run("inner-stop")["stop_requested"] is True


def test_stop_retry_repairs_transient_inner_write_failure(tmp_path):
	_create_bound_run(tmp_path, {"inner_run_id": "inner-stop"})
	with WorkflowStore(tmp_path) as store:
		with patch.object(CacheStore, "request_crawl_stop", side_effect=CacheBusyError(tmp_path / "cache" / "boss_agent.db", "fixture busy")):
			with pytest.raises(CacheBusyError):
				store.request_stop("outer-stop")
		assert store.get_checkpoint("outer-stop")["status"] == "stopped"
		assert store.cache.get_crawl_run("inner-stop")["stop_requested"] is False

	result = _stop(tmp_path)

	assert result.exit_code == 0, result.output
	assert json.loads(result.stdout)["ok"] is True
	with WorkflowStore(tmp_path) as store:
		assert store.cache.get_crawl_run("inner-stop")["stop_requested"] is True


def test_stop_remains_repeatable_after_success(tmp_path):
	_create_bound_run(tmp_path, {"inner_run_id": "inner-stop"})
	first = _stop(tmp_path)
	second = _stop(tmp_path)

	assert first.exit_code == 0, first.output
	assert second.exit_code == 0, second.output
	assert json.loads(second.stdout)["data"]["status"] == "stopped"


@pytest.mark.parametrize("exists", [False, True], ids=["missing", "completed"])
def test_stop_still_rejects_missing_or_completed_runs(tmp_path, exists):
	if exists:
		_create_bound_run(tmp_path, {"inner_run_id": "inner-stop"})
		with WorkflowStore(tmp_path) as store:
			store.update_run("outer-stop", status="completed", current_step=None)

	result = _stop(tmp_path)

	assert result.exit_code == 1, result.output
	assert json.loads(result.stdout)["error"]["code"] == "WORKFLOW_NOT_FOUND"
	if exists:
		with WorkflowStore(tmp_path) as store:
			assert store.cache.get_crawl_run("inner-stop")["stop_requested"] is False
