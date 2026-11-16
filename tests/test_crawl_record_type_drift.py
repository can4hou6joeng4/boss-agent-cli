"""crawl / workflow 记录的数值列与 params 类型漂移（Issue #1192）。

两个契约点：
1. 数值列被写成文本时，命令必须返回 ``CACHE_RECORD_INVALID`` 信封，不能
   stdout 为空、stderr 只剩 ``ValueError`` traceback。
2. ``crawl status`` 与 ``crawl resume`` 对同一份 ``params`` 的判定必须一致：
   前者不能把 ``query=5`` / ``limits="oops"`` 原样放进 ``ok=true`` 信封。
"""

import json

import pytest
from click.testing import CliRunner

from boss_agent_cli.cache.store import CacheStore
from boss_agent_cli.commands.crawl import _awaiting_queued_worker
from boss_agent_cli.main import cli
from boss_agent_cli.wizard.live_crawl import sort_runs_for_status_picker


def _params(tmp_path, **overrides):
	params = {
		"query": "python",
		"city_code": "101210100",
		"pages": 1,
		"with_detail": False,
		"browser": {"profile_path": str(tmp_path / "profile"), "cdp_port": 0},
		"chrome_path": None,
		"hook": {"profile": "none", "source": "user-supplied", "directory": None},
		"limits": {"max_requests": 20, "max_details": 50, "max_seconds": 600, "max_retries": 1},
		"operating_mode": "assisted",
	}
	params.update(overrides)
	return params


def _seed_run(tmp_path, params=None) -> None:
	cache = CacheStore(tmp_path / "cache" / "boss_agent.db")
	try:
		cache.create_crawl_run("run-1", params or _params(tmp_path), str(tmp_path / "crawl" / "run-1"))
	finally:
		cache.close()


def _exec(tmp_path, sql: str, values: tuple) -> None:
	cache = CacheStore(tmp_path / "cache" / "boss_agent.db")
	try:
		cache._conn.execute(sql, values)
		cache._conn.commit()
	finally:
		cache.close()


def _invoke(tmp_path, *args: str):
	return CliRunner().invoke(cli, ["--json", "--data-dir", str(tmp_path), "crawl", *args])


def _assert_corrupt_envelope(result, *, column: str) -> None:
	assert result.exit_code == 1, result.output
	assert "Traceback" not in (result.stderr or "")
	payload = json.loads(result.stdout)
	assert payload["ok"] is False
	assert payload["error"]["code"] == "CACHE_RECORD_INVALID"
	assert column in payload["error"]["message"]


@pytest.mark.parametrize(
	("column", "value"),
	[
		("next_page", "abc"),
		("requests_attempted", "x"),
		("detail_requests_attempted", "x"),
		("elapsed_seconds", "x"),
		("stop_requested", "false"),
		("list_finished", "false"),
	],
)
def test_crawl_status_numeric_column_drift_returns_envelope(tmp_path, column, value):
	_seed_run(tmp_path)
	_exec(tmp_path, f"UPDATE crawl_runs SET {column} = ? WHERE run_id = 'run-1'", (value,))

	_assert_corrupt_envelope(_invoke(tmp_path, "status", "run-1"), column=column)


def test_crawl_status_rejects_params_type_drift_like_resume(tmp_path):
	_seed_run(tmp_path, _params(tmp_path, query=5))

	_assert_corrupt_envelope(_invoke(tmp_path, "status", "run-1"), column="params.query")


def test_crawl_status_rejects_missing_params_keys_like_resume(tmp_path):
	# 真实写入总是带全顶层字段（Issue #1117）：缺键记录 resume 报损坏，
	# status 不能把它当成健康 run 展示（Issue #1192）。
	params = _params(tmp_path)
	del params["pages"]
	_seed_run(tmp_path, params)

	_assert_corrupt_envelope(_invoke(tmp_path, "status", "run-1"), column="params")


def test_crawl_status_outputs_effective_limits(tmp_path):
	# limits 写成字符串时 CrawlSettings.from_dict 按默认值执行；status 不能把
	# 原始 "oops" 原样回显成 budget.limits（那会与 resume 的行为矛盾）。
	_seed_run(tmp_path, _params(tmp_path, limits="oops"))

	result = _invoke(tmp_path, "status", "run-1")

	assert result.exit_code == 0, result.output
	payload = json.loads(result.stdout)
	assert payload["data"]["budget"]["limits"] == {
		"max_requests": 20,
		"max_details": 50,
		"max_seconds": 600,
		"max_retries": 1,
	}


def test_crawl_status_healthy_run_is_unchanged(tmp_path):
	_seed_run(tmp_path)

	result = _invoke(tmp_path, "status", "run-1")

	assert result.exit_code == 0, result.output
	data = json.loads(result.stdout)["data"]
	assert data["query"] == "python"
	assert data["city_code"] == "101210100"
	assert data["next_page"] == 1
	assert data["pages_completed"] == 0
	assert data["budget"]["limits"]["max_requests"] == 20


def test_status_picker_tolerates_corrupt_updated_at():
	# 列表排序是展示路径：时间戳坏掉只影响排序权重，不能让整个任务列表 traceback。
	runs = [
		{"run_id": "bad", "status": "completed", "updated_at": "abc", "live_crawl_status": "completed", "live_jobs_seen": 2},
		{"run_id": "good", "status": "completed", "updated_at": 100.0, "live_crawl_status": "completed", "live_jobs_seen": 2},
	]
	assert [run["run_id"] for run in sort_runs_for_status_picker(runs)] == ["good", "bad"]


def test_queued_handoff_tolerates_corrupt_updated_at():
	# 时间戳坏掉时按「排队已久」处理：宁可去探测锁，也不让父进程 traceback。
	run = {"status": "queued", "updated_at": "abc"}
	assert _awaiting_queued_worker(run) is False
