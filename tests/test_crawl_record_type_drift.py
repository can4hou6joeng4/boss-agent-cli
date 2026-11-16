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
from boss_agent_cli.commands.crawl import _awaiting_queued_worker, _prepare_background_resume
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


def _seed_run(tmp_path, params=None, *, status: str = "running") -> None:
	cache = CacheStore(tmp_path / "cache" / "boss_agent.db")
	try:
		cache.create_crawl_run("run-1", params or _params(tmp_path), str(tmp_path / "crawl" / "run-1"), status=status)
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


@pytest.mark.parametrize("drifted", ["yesterday", "12abc"])
def test_prepare_background_resume_tolerates_corrupt_updated_at(tmp_path, drifted):
	# updated_at 不参与契约校验（Issue #1192）：后台恢复只拿它做预检失败时的
	# 还原，漂移必须按 0 降级，不能被兜成可重试的 NETWORK_ERROR（Issue #1206）。
	_seed_run(tmp_path, status="stopped")
	_exec(tmp_path, "UPDATE crawl_runs SET updated_at = ? WHERE run_id = 'run-1'", (drifted,))

	status, should_launch = _prepare_background_resume(
		tmp_path, "run-1", pages=None, with_detail=False, clear_stop=True, preflight=lambda: None
	)

	assert (status, should_launch) == ("queued", True)


def test_prepare_background_resume_restores_stop_with_normalized_timestamp(tmp_path):
	# 预检失败仍要还原旧 stop；还原时间戳用归一后的 0.0，不能把漂移值写回去。
	_seed_run(tmp_path, status="stopped")
	cache = CacheStore(tmp_path / "cache" / "boss_agent.db")
	try:
		assert cache.request_crawl_stop("run-1")
	finally:
		cache.close()
	_exec(tmp_path, "UPDATE crawl_runs SET updated_at = 'yesterday' WHERE run_id = 'run-1'", ())

	def failing_preflight() -> None:
		raise RuntimeError("crawl 依赖缺失")

	with pytest.raises(RuntimeError, match="依赖缺失"):
		_prepare_background_resume(
			tmp_path, "run-1", pages=None, with_detail=False, clear_stop=True, preflight=failing_preflight
		)

	cache = CacheStore(tmp_path / "cache" / "boss_agent.db")
	try:
		run = cache.get_crawl_run("run-1")
	finally:
		cache.close()
	assert run["stop_requested"] is True
	assert run["updated_at"] == 0.0


def test_resume_background_does_not_report_corrupt_timestamp_as_network_error(tmp_path, monkeypatch):
	# CLI 层回归：此前 ValueError 被 handle_auth_errors 兜成可重试的 NETWORK_ERROR，
	# Agent 会拿同一条命令无限重试（Issue #1206）。
	launched: list[str] = []
	monkeypatch.setattr(
		"boss_agent_cli.commands.crawl._launch_background_resume",
		lambda data_dir, run_id, **kwargs: launched.append(run_id),
	)
	_seed_run(tmp_path, status="stopped")
	_exec(tmp_path, "UPDATE crawl_runs SET updated_at = 'yesterday' WHERE run_id = 'run-1'", ())

	from boss_agent_cli.auth.token_store import TokenStore

	TokenStore(tmp_path / "auth").save(
		{
			"cookies": {"wt2": "test-wt2", "wbg": "1", "zp_at": "1"},
			"user_agent": "test-agent",
		}
	)
	result = _invoke(tmp_path, "resume", "run-1", "--background")

	assert result.exit_code == 0, result.output
	payload = json.loads(result.stdout)
	assert payload["ok"] is True
	assert payload["data"] == {"run_id": "run-1", "status": "queued", "background": True}
	assert launched == ["run-1"]
