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
import os
import sqlite3
import stat
import time

import pytest
from rich.console import Console

from boss_agent_cli import display
from boss_agent_cli.api import cdp_risk_lock
from boss_agent_cli.api.client import AccountRiskError
from boss_agent_cli.api.request_budget import RequestBudget, RequestBudgetExceeded
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


@pytest.mark.parametrize("drifted", [1000.0 + 86400.0, 1000.0 + 1e9])
def test_reserve_crawl_budget_resets_far_future_reservation(tmp_path, drifted):
	"""有限远未来值同样是漂移：不能返回巨大等待并继续推向未来（Issue #1422）。"""
	with _open_cache(tmp_path) as cache:
		cache._conn.execute(
			"INSERT OR REPLACE INTO crawl_budget (budget_key, last_request_at) VALUES (?, ?)",
			("zhipin:all", drifted),
		)
		cache._conn.commit()
		assert cache.reserve_crawl_budget("zhipin:all", now=1000.0, interval=8.0) == 0.0
		# 坏值被本次预留覆盖，调度自愈。
		assert cache.get_crawl_budget("zhipin:all") == 1000.0


def test_reserve_crawl_budget_keeps_slightly_ahead_reservation(tmp_path):
	"""略超前的正数是上一次预留的合法槽位，不能被当成漂移清零。"""
	with _open_cache(tmp_path) as cache:
		cache._conn.execute(
			"INSERT OR REPLACE INTO crawl_budget (budget_key, last_request_at) VALUES (?, ?)",
			("zhipin:all", 1002.0),
		)
		cache._conn.commit()
		assert cache.reserve_crawl_budget("zhipin:all", now=1000.0, interval=8.0) == 10.0
		assert cache.get_crawl_budget("zhipin:all") == 1010.0


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


@pytest.mark.parametrize("column", ["last_at", "next_at"])
def test_request_budget_attempt_resets_far_future_state(tmp_path, column):
	"""有限远未来值不能把每条命令都拒成预算耗尽且永不写回（Issue #1422）。"""
	budget = RequestBudget(tmp_path, interval=1.5, wait_timeout=1.0)
	with budget._database() as db:
		db.execute(f"UPDATE request_state SET {column}=? WHERE id=1", (time.time() + 86400.0,))
	with budget.attempt("test"):
		pass
	with budget._database() as db:
		last_at, next_at, cooldown = db.execute(
			"SELECT last_at,next_at,cooldown_until FROM request_state WHERE id=1"
		).fetchone()
	assert time.time() - last_at < 5
	assert 0 <= next_at - time.time() < 5
	assert cooldown == 0.0


def test_request_budget_attempt_keeps_short_future_reservation(tmp_path, monkeypatch):
	"""正数但略超前的 next_at 是合法预留：等到该时间点后执行，不能清零。"""
	clock = [1000.0]
	monkeypatch.setattr("boss_agent_cli.api.request_budget.time.time", lambda: clock[0])
	slept: list[float] = []

	def fake_sleep(seconds, **_kwargs):
		slept.append(seconds)
		clock[0] += seconds

	monkeypatch.setattr("boss_agent_cli.api.request_budget.cancellable_sleep", fake_sleep)
	budget = RequestBudget(tmp_path, interval=1.5, wait_timeout=5.0)
	with budget._database() as db:
		db.execute("UPDATE request_state SET next_at=? WHERE id=1", (1001.0,))
	with budget.attempt("test"):
		pass
	assert slept == [pytest.approx(1.0)]
	with budget._database() as db:
		next_at = db.execute("SELECT next_at FROM request_state WHERE id=1").fetchone()[0]
	assert next_at == pytest.approx(1002.5)


def test_request_budget_attempt_keeps_legitimate_cooldown(tmp_path, monkeypatch):
	"""服务器冷却可以长于请求间隔：合法的短冷却不能被当成漂移（Issue #1422）。"""
	clock = [1000.0]
	monkeypatch.setattr("boss_agent_cli.api.request_budget.time.time", lambda: clock[0])
	budget = RequestBudget(tmp_path, interval=1.5, wait_timeout=1.0)
	with budget._database() as db:
		db.execute("UPDATE request_state SET cooldown_until=? WHERE id=1", (1005.0,))
	with pytest.raises(RequestBudgetExceeded) as exc_info:
		with budget.attempt("test"):
			pass
	assert exc_info.value.details["retry_after_seconds"] == pytest.approx(5.0)
	with budget._database() as db:
		cooldown = db.execute("SELECT cooldown_until FROM request_state WHERE id=1").fetchone()[0]
	assert cooldown == pytest.approx(1005.0)


def test_request_budget_attempt_bounds_far_future_cooldown(tmp_path, monkeypatch):
	"""超过本地冷却上限的值按漂移截断并写回，而不是永久阻塞（Issue #1422）。"""
	clock = [1000.0]
	monkeypatch.setattr("boss_agent_cli.api.request_budget.time.time", lambda: clock[0])
	budget = RequestBudget(tmp_path, interval=1.5, wait_timeout=1.0)
	with budget._database() as db:
		db.execute("UPDATE request_state SET cooldown_until=? WHERE id=1", (1000.0 + 86400.0,))
	with pytest.raises(RequestBudgetExceeded) as exc_info:
		with budget.attempt("test"):
			pass
	assert exc_info.value.details["retry_after_seconds"] == pytest.approx(3600.0)
	with budget._database() as db:
		cooldown = db.execute("SELECT cooldown_until FROM request_state WHERE id=1").fetchone()[0]
	assert cooldown == pytest.approx(1000.0 + 3600.0)


def test_request_budget_cooldown_overwrites_far_future_drift(tmp_path, monkeypatch):
	"""有限远未来值不能让 MAX() 永远选中坏值、吞掉服务器冷却（Issue #1422）。"""
	clock = [1000.0]
	monkeypatch.setattr("boss_agent_cli.api.request_budget.time.time", lambda: clock[0])
	budget = RequestBudget(tmp_path, interval=0.0, wait_timeout=0.5)
	with budget._database() as db:
		db.execute("UPDATE request_state SET cooldown_until=? WHERE id=1", (1000.0 + 86400.0,))
	budget.cooldown(10.0)
	with budget._database() as db:
		cooldown = db.execute("SELECT cooldown_until FROM request_state WHERE id=1").fetchone()[0]
	assert cooldown == pytest.approx(1010.0)


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


# ── 请求租约路径类型（Issue #1753） ────────────────────────────────


def _lease_path(tmp_path):
	root = tmp_path / "budget"
	root.mkdir(exist_ok=True)
	return root / "request-budget.lease"


def test_request_budget_lease_refuses_symlink_without_touching_target(tmp_path):
	lease = _lease_path(tmp_path)
	target = tmp_path / "outside.lease"
	target.write_bytes(b"keep")
	try:
		lease.symlink_to(target)
	except (OSError, NotImplementedError):
		pytest.skip("当前文件系统不支持符号链接")

	budget = RequestBudget(tmp_path, interval=0.0, wait_timeout=1.0, root=lease.parent)
	with pytest.raises(sqlite3.DatabaseError) as exc_info:
		with budget.attempt("test"):
			pass

	assert "符号链接" in str(exc_info.value)
	assert lease.is_symlink()
	assert target.read_bytes() == b"keep"


def test_request_budget_lease_refuses_dangling_symlink_without_creating_target(tmp_path):
	lease = _lease_path(tmp_path)
	target = tmp_path / "missing.lease"
	try:
		lease.symlink_to(target)
	except (OSError, NotImplementedError):
		pytest.skip("当前文件系统不支持符号链接")

	budget = RequestBudget(tmp_path, interval=0.0, wait_timeout=1.0, root=lease.parent)
	with pytest.raises(sqlite3.DatabaseError):
		with budget.attempt("test"):
			pass

	assert not target.exists()


@pytest.mark.skipif(os.name == "nt", reason="Windows 没有 POSIX FIFO")
def test_request_budget_lease_refuses_fifo_without_blocking(tmp_path):
	lease = _lease_path(tmp_path)
	os.mkfifo(lease)
	budget = RequestBudget(tmp_path, interval=0.0, wait_timeout=1.0, root=lease.parent)

	with pytest.raises(sqlite3.DatabaseError) as exc_info:
		with budget.attempt("test"):
			pass

	assert "不是普通文件" in str(exc_info.value)
	assert stat.S_ISFIFO(lease.lstat().st_mode)
