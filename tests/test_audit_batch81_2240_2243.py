"""第十二轮审计批次：Issue #2240–#2243 的回归测试。

- #2240：恢复取得执行权后、慢预检之前先把状态落成 running，预检期间的
  `crawl stop` 不再被终态拒绝；预检失败还原原状态 / 错误 / 时间戳。
- #2241：completed 快速路径导出前后重读断点，clean --privacy 并发生效时不
  重建隐私产物；clean 先删数据库行再删文件。
- #2242：后台恢复父进程与执行器同口径校验 Hook 目录与设置，确定性失败不再
  返回 ok=true。
- #2243：交接窗口内显式 --pages / --with-detail / 停止标记不再被静默忽略。

全部测试离线运行；不调用平台 API、浏览器或真实网络。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from click.testing import CliRunner

import boss_agent_cli.commands.clean as clean_mod
import boss_agent_cli.crawler.service as service_mod
from boss_agent_cli.cache.store import CacheStore
from boss_agent_cli.commands.crawl import CrawlHandoffPending, _prepare_background_resume
from boss_agent_cli.crawler.controls import CrawlHookDirInvalid
from boss_agent_cli.crawler.service import CrawlService
from boss_agent_cli.main import cli

_RUN_ID = "run-1"


def _params(*, query: str = "AI", hook_profile: str = "none", hook_dir: str | None = None) -> dict[str, Any]:
	return {
		"query": query,
		"city_code": "101210100",
		"pages": 1,
		"with_detail": False,
		"browser": {"profile_path": "/tmp/profile", "cdp_port": 9333},
		"chrome_path": None,
		"hook": {"profile": hook_profile, "source": "user-supplied", "directory": hook_dir},
		"limits": {"max_requests": 20, "max_details": 50, "max_seconds": 600, "max_retries": 1},
		"operating_mode": "assisted",
	}


def _seed(
	tmp_path: Path,
	*,
	status: str = "stopped",
	stop_requested: int = 0,
	query: str = "AI",
	hook_profile: str = "none",
	hook_dir: str | None = None,
) -> Path:
	db_path = tmp_path / "cache" / "boss_agent.db"
	db_path.parent.mkdir(parents=True, exist_ok=True)
	with CacheStore(db_path) as cache:
		cache.create_crawl_run(
			_RUN_ID,
			_params(query=query, hook_profile=hook_profile, hook_dir=hook_dir),
			str(tmp_path / "crawl" / _RUN_ID),
			status=status,
		)
	if stop_requested:
		with sqlite3.connect(db_path) as connection:
			connection.execute("UPDATE crawl_runs SET stop_requested = 1 WHERE run_id = ?", (_RUN_ID,))
	return db_path


def _service(cache: CacheStore, tmp_path: Path, *, preflight=None, transport_factory=None) -> CrawlService:
	def explode(_settings):  # pragma: no cover - 不应构造浏览器
		raise AssertionError("预检失败不应构造传输层")

	return CrawlService(
		cache,
		data_dir=tmp_path,
		transport_factory=transport_factory or explode,
		budget_factory=lambda _cache: SimpleNamespace(),
		preflight=preflight,
	)


# ── #2240：预检窗口内 stop 可见，失败还原状态 ──────────────────────


def test_foreground_resume_marks_running_before_preflight(tmp_path) -> None:
	db_path = _seed(tmp_path, status="stopped")
	observed: dict[str, Any] = {}

	class Boom(RuntimeError):
		pass

	def preflight() -> None:
		with CacheStore(db_path) as probe:
			observed["status"] = probe.get_crawl_run(_RUN_ID)["status"]
		raise Boom("preflight failed")

	with CacheStore(db_path) as cache:
		before = cache.get_crawl_run(_RUN_ID)
		service = _service(cache, tmp_path, preflight=preflight)
		with pytest.raises(Boom):
			service.resume(_RUN_ID, clear_stop=True)
		# 预检失败还原为恢复前的完整记录：终态、错误与时间戳都不变。
		assert cache.get_crawl_run(_RUN_ID) == before

	assert observed["status"] == "running"


def test_foreground_resume_restores_state_when_mark_active_fails(tmp_path, monkeypatch) -> None:
	"""标记本身失败（本地 IO）时没有写入时间：还原仍要完成，原异常原样上抛。"""
	db_path = _seed(tmp_path, status="stopped", stop_requested=1)

	def explode(self, run_id, status):
		raise OSError("模拟本地 IO 失败")

	with CacheStore(db_path) as cache:
		before = cache.get_crawl_run(_RUN_ID)
		monkeypatch.setattr(CacheStore, "mark_crawl_run_active", explode)
		service = _service(cache, tmp_path, preflight=lambda: None)
		with pytest.raises(OSError, match="模拟本地 IO 失败"):
			service.resume(_RUN_ID, clear_stop=True)
		assert cache.get_crawl_run(_RUN_ID) == before


def test_foreground_resume_honors_stop_arriving_during_preflight(tmp_path) -> None:
	db_path = _seed(tmp_path, status="stopped")

	def preflight() -> None:
		with CacheStore(db_path) as other:
			other.request_crawl_stop(_RUN_ID)

	class NoOpenTransport:
		def open(self):
			raise AssertionError("停止请求必须在打开浏览器之前生效")

	with CacheStore(db_path) as cache:
		service = _service(cache, tmp_path, preflight=preflight, transport_factory=lambda _settings: NoOpenTransport())
		outcome = service.resume(_RUN_ID)

		# 停止请求在预检之后立即生效：不打开浏览器、任务按 stopped 结算。
		assert outcome.status == "stopped"
		assert "stop requested" in outcome.error
		assert cache.get_crawl_run(_RUN_ID)["status"] == "stopped"


# ── #2241：快速路径不复活已清理的产物 ─────────────────────────────


def test_completed_fast_path_drops_artifacts_when_run_disappears(tmp_path, monkeypatch) -> None:
	db_path = _seed(tmp_path, status="completed")
	output_dir = tmp_path / "crawl" / _RUN_ID
	real_write = service_mod.write_run_outputs

	def write_then_clean(path: Path, rows: list[dict[str, Any]]) -> dict[str, str]:
		written = real_write(path, rows)
		# 模拟并发 clean --privacy：行在导出期间被删除（先删行、后删文件）。
		with sqlite3.connect(db_path) as connection:
			connection.execute("DELETE FROM crawl_jobs")
			connection.execute("DELETE FROM crawl_runs")
		return written

	monkeypatch.setattr(service_mod, "write_run_outputs", write_then_clean)

	with CacheStore(db_path) as cache:
		service = _service(cache, tmp_path)
		with pytest.raises(KeyError):
			service.resume(_RUN_ID)

	for extension in ("json", "csv", "xlsx"):
		assert not (output_dir / f"jobs.{extension}").exists()


def test_clean_deletes_crawl_rows_before_files(tmp_path, monkeypatch) -> None:
	db_path = _seed(tmp_path, status="completed")
	output_dir = tmp_path / "crawl" / _RUN_ID
	output_dir.mkdir(parents=True, exist_ok=True)
	(output_dir / "jobs.json").write_text("[]", encoding="utf-8")

	real_clean_path = clean_mod._clean_path
	observed: dict[str, int] = {}

	def probe_clean_path(path, *, dry_run, root):
		with sqlite3.connect(db_path) as connection:
			observed["rows"] = connection.execute("SELECT COUNT(*) FROM crawl_runs").fetchone()[0]
		return real_clean_path(path, dry_run=dry_run, root=root)

	monkeypatch.setattr(clean_mod, "_clean_path", probe_clean_path)

	clean_mod._clean_crawl_data(tmp_path, dry_run=False)

	assert observed["rows"] == 0
	assert not (tmp_path / "crawl").exists()


# ── #2242：后台父进程校验 hook 目录与设置 ──────────────────────────


def test_background_resume_rejects_broken_hook_dir_before_queued(tmp_path) -> None:
	db_path = _seed(
		tmp_path,
		status="stopped",
		hook_profile="screenshot-full",
		hook_dir=str(tmp_path / "missing-hooks"),
	)

	with pytest.raises(CrawlHookDirInvalid):
		_prepare_background_resume(
			tmp_path, _RUN_ID, pages=None, with_detail=False, clear_stop=True, preflight=lambda: None
		)

	with CacheStore(db_path) as cache:
		run = cache.get_crawl_run(_RUN_ID)
	assert run["status"] == "stopped"


def test_background_resume_rejects_blank_query_before_queued(tmp_path) -> None:
	db_path = _seed(tmp_path, status="stopped", query="   ")

	with pytest.raises(ValueError, match="搜索关键词不能为空"):
		_prepare_background_resume(
			tmp_path, _RUN_ID, pages=None, with_detail=False, clear_stop=True, preflight=lambda: None
		)

	with CacheStore(db_path) as cache:
		assert cache.get_crawl_run(_RUN_ID)["status"] == "stopped"


def test_background_resume_broken_hook_dir_envelope_is_local_io(tmp_path) -> None:
	_seed(
		tmp_path,
		status="stopped",
		hook_profile="screenshot-full",
		hook_dir=str(tmp_path / "missing-hooks"),
	)

	result = CliRunner().invoke(
		cli, ["--data-dir", str(tmp_path), "--json", "crawl", "resume", _RUN_ID, "--background"]
	)

	assert result.exit_code == 1, result.output
	envelope = json.loads(result.stdout)
	assert envelope["ok"] is False
	assert envelope["error"]["code"] == "LOCAL_IO_ERROR"
	assert envelope["error"]["recoverable"] is False


# ── #2243：交接窗口内参数不被静默忽略 ─────────────────────────────


def test_background_resume_defers_explicit_parameters_during_handoff(tmp_path) -> None:
	_seed(tmp_path, status="queued")

	with pytest.raises(CrawlHandoffPending):
		_prepare_background_resume(
			tmp_path, _RUN_ID, pages=2, with_detail=False, clear_stop=True, preflight=lambda: None
		)

	status, should_launch = _prepare_background_resume(
		tmp_path, _RUN_ID, pages=None, with_detail=False, clear_stop=True, preflight=lambda: None
	)
	assert (status, should_launch) == ("queued", False)


def test_background_resume_defers_stop_clearing_during_handoff(tmp_path) -> None:
	db_path = _seed(tmp_path, status="queued", stop_requested=1)

	with pytest.raises(CrawlHandoffPending):
		_prepare_background_resume(
			tmp_path, _RUN_ID, pages=None, with_detail=False, clear_stop=True, preflight=lambda: None
		)

	with CacheStore(db_path) as cache:
		# 停止标记保持，不被交接窗口外来的 resume 清掉。
		assert cache.get_crawl_run(_RUN_ID)["stop_requested"] == 1
