"""本地记录数值 / 容器漂移在读取边界的收口（Issue #1212）。

四个位置同族但独立：

1. ``crawl_budget`` / ``request_state`` / ``request_attempts`` 是本地调度元数据，
   坏值不能让采集或全部平台请求失败，也不能让等待时间无限延长。
2. ``crawl_jobs.page_no`` / ``detail_done`` 与 ``crawl_runs`` 一样必须在读取边界
   校验，``CacheRecordInvalid`` 由命令层输出 ``CACHE_RECORD_INVALID``。
3. 向导展示计数读取历史 ``last_result``，坏值只能降级展示，不能 traceback。
4. 风控锁写入不能因为登录态结构漂移抛 ``AttributeError``，掩盖原始风控信封。
"""

import io
import sqlite3
import time

import pytest
from rich.console import Console

from boss_agent_cli import display
from boss_agent_cli.api import cdp_risk_lock
from boss_agent_cli.api.client import AccountRiskError
from boss_agent_cli.api.request_budget import RequestBudget
from boss_agent_cli.auth.manager import AuthManager
from boss_agent_cli.cache.store import CacheRecordInvalid, CacheStore
from boss_agent_cli.crawler.service import CrawlService, CrawlSettings
from boss_agent_cli.wizard.models import count_or_none
from boss_agent_cli.wizard.prompts import has_result_follow_up
from boss_agent_cli.wizard.renderer import render_run


def _params(tmp_path, **overrides):
	params = {
		"query": "python",
		"city_code": "101210100",
		"pages": 1,
		"with_detail": True,
		"browser": {"profile_path": str(tmp_path / "profile"), "cdp_port": 9333},
		"chrome_path": None,
		"hook": {"profile": "none", "source": "user-supplied", "directory": None},
		"limits": {"max_requests": 20, "max_details": 50, "max_seconds": 600, "max_retries": 1},
		"operating_mode": "assisted",
	}
	params.update(overrides)
	return params


def _open_cache(tmp_path):
	return CacheStore(tmp_path / "cache" / "boss_agent.db")


def _seed_run(tmp_path) -> None:
	with _open_cache(tmp_path) as cache:
		cache.create_crawl_run("run-1", _params(tmp_path), str(tmp_path / "crawl" / "run-1"), status="running")


def _seed_crawl_job(tmp_path, *, page_no: object = 1, detail_done: object = 0) -> None:
	with _open_cache(tmp_path) as cache:
		cache._conn.execute(
			"INSERT INTO crawl_jobs (run_id, job_key, selector, page_no, payload, detail_done, updated_at) "
			"VALUES (?, ?, ?, ?, ?, ?, ?)",
			("run-1", "job-1", ".job-card", page_no, "{}", detail_done, 0.0),
		)
		cache._conn.commit()


# ── 1. 预算表 ───────────────────────────────────────────────────────


@pytest.mark.parametrize("drifted", ["yesterday", "nan", "inf", float("inf"), float("-inf")])
def test_reserve_crawl_budget_tolerates_drifted_timestamp(tmp_path, drifted):
	with _open_cache(tmp_path) as cache:
		cache._conn.execute(
			"INSERT OR REPLACE INTO crawl_budget (budget_key, last_request_at) VALUES (?, ?)",
			("zhipin:all", drifted),
		)
		cache._conn.commit()
		assert cache.reserve_crawl_budget("zhipin:all", now=1000.0, interval=8.0) == 0.0
		# 下一轮写入把坏值覆盖成本次预留，调度自愈。
		assert cache.get_crawl_budget("zhipin:all") == 1000.0


def test_get_crawl_budget_returns_none_for_drifted_timestamp(tmp_path):
	with _open_cache(tmp_path) as cache:
		cache._conn.execute(
			"INSERT OR REPLACE INTO crawl_budget (budget_key, last_request_at) VALUES (?, ?)",
			("zhipin:all", "yesterday"),
		)
		cache._conn.commit()
		assert cache.get_crawl_budget("zhipin:all") is None


@pytest.mark.parametrize("column", ["last_at", "next_at", "cooldown_until"])
def test_request_budget_attempt_tolerates_drifted_state(tmp_path, column):
	budget = RequestBudget(tmp_path, interval=0.0, wait_timeout=1.0)
	with budget._database() as db:
		db.execute(f"UPDATE request_state SET {column}=? WHERE id=1", ("yesterday",))
	with budget.attempt("test"):
		pass
	with budget._database() as db:
		count = db.execute("SELECT COUNT(*) FROM request_attempts").fetchone()[0]
		if column == "cooldown_until":
			cooldown = db.execute("SELECT cooldown_until FROM request_state WHERE id=1").fetchone()[0]
	assert count == 1
	if column == "cooldown_until":
		assert cooldown == 0.0


def test_request_budget_cleans_drifted_attempt_rows(tmp_path):
	"""坏行永不满足数值比较，会一直占满每分钟额度并让等待超时。"""
	budget = RequestBudget(tmp_path, interval=0.0, wait_timeout=0.5, max_per_minute=1)
	with budget._database() as db:
		db.execute("INSERT INTO request_attempts (requested_at, kind) VALUES (?, ?)", ("yesterday", "api"))
	with budget.attempt("test"):
		pass
	with budget._database() as db:
		remaining = db.execute("SELECT COUNT(*) FROM request_attempts").fetchone()[0]
	assert remaining == 1  # 只有本次合法登记的一条


@pytest.mark.parametrize("drifted", ["later", float("inf")])
def test_request_budget_cooldown_overwrites_drifted_state(tmp_path, drifted):
	"""SQLite 的 MAX(text, real) 会选中文本，导致服务器冷却被静默吞掉。"""
	budget = RequestBudget(tmp_path, interval=0.0, wait_timeout=0.5, max_per_minute=3)
	with budget._database() as db:
		db.execute("UPDATE request_state SET cooldown_until=? WHERE id=1", (drifted,))
	budget.cooldown(0.0)
	with budget._database() as db:
		value = db.execute("SELECT cooldown_until FROM request_state WHERE id=1").fetchone()[0]
	assert isinstance(value, float)
	assert time.time() - 5 <= value <= time.time() + 5
	# 下一轮 attempt 不会因为坏冷却永久等待。
	with budget.attempt("test"):
		pass


# ── 2. crawl_jobs ───────────────────────────────────────────────────


@pytest.mark.parametrize("column", ["page_no", "detail_done"])
def test_crawl_job_columns_are_validated_at_read_boundary(tmp_path, column):
	_seed_run(tmp_path)
	_seed_crawl_job(tmp_path, **{column: "abc"})
	with _open_cache(tmp_path) as cache:
		with pytest.raises(CacheRecordInvalid) as caught:
			cache.list_crawl_jobs("run-1")
	assert caught.value.column == column
	assert caught.value.record_id == "job-1"


def test_crawl_status_reports_page_no_drift_as_corrupt_record(tmp_path):
	from click.testing import CliRunner

	from boss_agent_cli.main import cli

	_seed_run(tmp_path)
	_seed_crawl_job(tmp_path, page_no="abc")
	result = CliRunner().invoke(cli, ["--json", "--data-dir", str(tmp_path), "crawl", "status", "run-1"])
	assert result.exit_code == 1, result.output
	assert "CACHE_RECORD_INVALID" in result.output
	assert "Traceback" not in (result.stderr or "")


class _FakeTransport:
	def open(self):
		return []

	def close(self):
		pass


def test_run_reraises_cache_record_invalid_instead_of_reporting_stopped(tmp_path, monkeypatch):
	"""``_complete_pending_details`` 读到坏行时，入口必须拿到损坏码而不是成功信封。"""
	_seed_run(tmp_path)
	with _open_cache(tmp_path) as cache:
		service = CrawlService(cache, data_dir=tmp_path, transport_factory=lambda settings: _FakeTransport())
		settings = CrawlSettings.from_dict(_params(tmp_path), run_id="run-1")

		def boom(*args, **kwargs):
			raise CacheRecordInvalid("crawl_job", "job-1", "page_no", "期望整数，实际是 str")

		monkeypatch.setattr(service, "_complete_pending_details", boom)
		with pytest.raises(CacheRecordInvalid):
			service._run("run-1", settings, next_page=1, output_dir=tmp_path / "crawl" / "run-1")
		assert cache.get_crawl_run("run-1")["status"] == "stopped"


# ── 3. 向导展示 ─────────────────────────────────────────────────────


@pytest.mark.parametrize("drifted", ["abc", [1], {"x": 1}, -5])
def test_count_or_none_tolerates_drift(drifted):
	assert count_or_none(drifted) is None


def test_count_or_none_keeps_legitimate_values():
	assert count_or_none(None) is None
	assert count_or_none(True) is None
	assert count_or_none(12) == 12
	assert count_or_none("12") == 12
	assert count_or_none(3.9) == 3


@pytest.mark.parametrize("drifted", ["abc", [1], {"x": 1}, -5])
def test_has_result_follow_up_tolerates_drifted_jobs_seen(drifted):
	run = {
		"status": "waiting_input",
		"goal": "crawl_start",
		"last_result": {"data": {"jobs_seen": drifted}},
	}
	assert has_result_follow_up(run) is False


def test_render_run_tolerates_drifted_jobs_seen(monkeypatch):
	stream = io.StringIO()
	monkeypatch.setattr(display, "console", Console(file=stream, force_terminal=False, width=200))
	render_run(
		{
			"run_id": "wrn_1",
			"status": "waiting_input",
			"goal": "crawl_start",
			"role": "candidate",
			"last_result": {"data": {"jobs_seen": "abc", "status": "completed"}},
		},
		with_preview=True,
	)
	assert "需要你处理后再继续" in stream.getvalue()


# ── 4. 风控锁 ───────────────────────────────────────────────────────


def test_record_cdp_risk_lock_tolerates_drifted_cookies(tmp_path):
	manager = AuthManager(tmp_path)
	manager._token = {"cookies": []}
	error = AccountRiskError("synthetic risk")

	manager._record_cdp_risk_lock(error=error)

	assert error.details["risk_lock"]["locked"] is True
	assert error.details["risk_lock"]["persisted"] is True
	assert cdp_risk_lock.read_lock(tmp_path) is not None
	assert manager._unpersisted_risk_lock is None


def test_has_primary_cookie_tolerates_drifted_cookies(tmp_path):
	manager = AuthManager(tmp_path)
	assert manager._has_primary_cookie({"cookies": []}) is False
	assert manager._has_primary_cookie({"cookies": {"wt2": "synthetic"}}) is True


def test_sqlite_returns_text_for_drifted_real_column(tmp_path):
	"""守卫 SQLite 的动态类型前提：REAL 列确实能读回文本。"""
	db = sqlite3.connect(tmp_path / "probe.sqlite3")
	db.execute("CREATE TABLE t (v REAL NOT NULL)")
	db.execute("INSERT INTO t (v) VALUES ('yesterday')")
	assert db.execute("SELECT v FROM t").fetchone()[0] == "yesterday"
	db.close()
