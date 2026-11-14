"""同一采集任务只能有一个执行器，查询与停止请求不受执行互斥影响。"""

from __future__ import annotations

import errno
import json
import subprocess
import sys
import textwrap
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from click.testing import CliRunner

from boss_agent_cli.cache.store import CacheStore
from boss_agent_cli.crawler.service import CrawlBudget, CrawlBusyError, CrawlService, CrawlSettings
from boss_agent_cli.display import shell_argument
from boss_agent_cli.main import cli
from boss_agent_cli.output import Logger
from boss_agent_cli.wizard.actions import ActionContext, DEFAULT_ACTIONS
from boss_agent_cli.wizard.catalog import build_plan
from boss_agent_cli.wizard.models import WizardInput
from boss_agent_cli.wizard.runner import WorkflowRunner
from boss_agent_cli.wizard.store import WorkflowStore

ENVELOPE_KEYS = {"ok", "schema_version", "command", "data", "pagination", "error", "hints"}


class _Pages:
	def __init__(self, on_fetch=None) -> None:
		self.on_fetch = on_fetch
		self.fetches: list[int] = []

	def open(self):
		return []

	def fetch_page(self, query, city_code, page_no):
		self.fetches.append(page_no)
		if self.on_fetch is not None:
			self.on_fetch(page_no)
		return {"code": 0, "zpData": {"jobList": [{
			"encryptJobId": f"job-{page_no}", "securityId": f"fixture-sec-{page_no}",
			"jobName": "测试职位", "brandName": "测试公司",
		}], "hasMore": page_no < 3}}

	def close(self, *, quit_browser=True):
		pass


class _NoDelayBudget(CrawlBudget):
	def wait(self, kind: str) -> None:
		pass


def _settings(tmp_path, *, pages=3):
	return CrawlSettings(
		query="AI", city_code="101210100", pages=pages, with_detail=False,
		profile_path=tmp_path / "profile", chrome_path=None, cdp_port=9333,
		hook_profile="none", operating_mode="research",
	)


def _service(tmp_path, transport_factory, **kwargs):
	# 每个线程各开一条 SQLite 连接，不能跨线程使用连接。
	cache = CacheStore(tmp_path / "cache" / "boss_agent.db")
	return cache, CrawlService(
		cache, data_dir=tmp_path, transport_factory=transport_factory,
		budget_factory=_NoDelayBudget, **kwargs,
	)


def _persisted_run(tmp_path, *, pages=3):
	cache, service = _service(tmp_path, lambda settings: _Pages())
	with cache:
		return service.create(_settings(tmp_path, pages=pages))


def _status_command(data_dir, run_id):
	return f"boss --data-dir {shell_argument(str(data_dir.resolve()))} crawl status {shell_argument(run_id)}"


def _lock_files(data_dir):
	return list((data_dir / "cache" / "crawl-locks").glob("*.lock"))


@pytest.fixture
def offline_cli(monkeypatch):
	# 登录态与浏览器都是替身；后到者连 transport 都不应构造。
	monkeypatch.setattr("boss_agent_cli.commands.crawl._session_seed", lambda ctx: ({}, ""))

	def forbidden(*args, **kwargs):
		raise AssertionError("被拒绝的调用不应构造浏览器")

	monkeypatch.setattr("boss_agent_cli.commands.crawl._transport_factory", forbidden)
	return CliRunner()


@pytest.mark.parametrize("entry", ["resume", "create_and_run"])
def test_overlapping_execution_never_fetches_the_same_page_twice(tmp_path, entry):
	entered = threading.Event()
	release = threading.Event()
	created = []

	def on_fetch(page):
		if page == 2:
			entered.set()
			assert release.wait(10)

	transport = _Pages(on_fetch)
	if entry == "resume":
		created.append(_persisted_run(tmp_path))

	def first_worker():
		cache, service = _service(tmp_path, lambda settings: transport, on_run_created=created.append)
		with cache:
			return service.resume(created[0]) if entry == "resume" else service.create_and_run(_settings(tmp_path))

	with ThreadPoolExecutor(max_workers=1) as pool:
		first = pool.submit(first_worker)
		try:
			assert entered.wait(10)
			assert len(created) == 1
			constructed = []
			cache, second = _service(tmp_path, lambda settings: constructed.append(settings) or _Pages())
			with cache:
				before = cache.get_crawl_run(created[0])
				assert before["next_page"] == 2
				with pytest.raises(CrawlBusyError) as rejected:
					second.resume(created[0], pages=10, with_detail=True, clear_stop=True)
				assert rejected.value.run_id == created[0]
				assert cache.get_crawl_run(created[0]) == before
				assert constructed == []
				assert transport.fetches == [1, 2]
		finally:
			release.set()
		outcome = first.result(timeout=10)

	assert outcome.status == "completed"
	assert outcome.requests_attempted == 3
	assert transport.fetches == [1, 2, 3]
	cache, again = _service(tmp_path, lambda settings: constructed.append(settings) or _Pages())
	with cache:
		assert again.resume(created[0]).requests_attempted == 3
		assert constructed == []
	assert _lock_files(tmp_path) == []


def test_new_run_uses_the_checkpoint_advanced_by_a_creation_callback(tmp_path):
	"""发布新任务编号的回调允许别人先接手，创建者不能再从第一页抓起。"""
	fetched = []

	def run_first(run_id):
		cache, worker = _service(tmp_path, lambda settings: _Pages(fetched.append))
		with cache:
			assert worker.resume(run_id, pages=1).next_page == 2

	cache, creator = _service(tmp_path, lambda settings: _Pages(fetched.append), on_run_created=run_first)
	with cache:
		outcome = creator.create_and_run(_settings(tmp_path))
	assert outcome.status == "completed"
	assert fetched == [1]
	assert outcome.next_page == 2
	assert outcome.requests_attempted == 1


def test_busy_resume_does_not_clear_a_pending_stop_or_read_the_checkpoint(tmp_path, monkeypatch):
	run_id = _persisted_run(tmp_path)
	cache, service = _service(tmp_path, lambda settings: _Pages())
	with cache:
		assert cache.request_crawl_stop(run_id)
		before = cache.get_crawl_run(run_id)
		with service.execution_lock(run_id) as acquired:
			assert acquired
			with monkeypatch.context() as patch:
				def forbidden(*args):
					raise AssertionError("必须先取执行权再读断点")
				patch.setattr(cache, "get_crawl_run", forbidden)
				with pytest.raises(CrawlBusyError):
					service.resume(run_id, clear_stop=True)
		assert cache.get_crawl_run(run_id) == before


@pytest.mark.parametrize("run_id", ["../../outside", "nested/run", "任务 with spaces", "a" * 400])
def test_lock_path_is_independent_of_the_run_id(tmp_path, run_id):
	cache, service = _service(tmp_path, lambda settings: _Pages())
	with cache, service.execution_lock(run_id) as acquired:
		assert acquired
		assert [path.parent for path in tmp_path.rglob("*.lock")] == [tmp_path / "cache" / "crawl-locks"]
		with service.execution_lock(run_id) as again:
			assert not again
	assert _lock_files(tmp_path) == []


@pytest.mark.parametrize("mode,exit_code", [("envelope", 1), ("sysexits", 75)])
def test_cli_resume_reports_busy_and_offers_status_only(tmp_path, offline_cli, mode, exit_code):
	run_id = _persisted_run(tmp_path)
	cache, service = _service(tmp_path, lambda settings: _Pages())
	with cache, service.execution_lock(run_id) as acquired:
		assert acquired
		before = cache.get_crawl_run(run_id)
		result = offline_cli.invoke(cli, [
			"--data-dir", str(tmp_path), "--json", "--exit-codes", mode, "crawl", "resume", run_id,
		])
		assert cache.get_crawl_run(run_id) == before
	assert result.exit_code == exit_code, result.output
	payload = json.loads(result.stdout)
	assert set(payload) == ENVELOPE_KEYS
	assert payload["ok"] is False
	assert payload["error"]["code"] == "CRAWL_BUSY"
	assert payload["error"]["recoverable"] is True
	assert payload["error"]["details"] == {"run_id": run_id}
	status_command = _status_command(tmp_path, run_id)
	assert status_command in payload["error"]["recovery_action"]
	assert payload["hints"]["next_actions"] == [status_command]


def test_status_results_and_stop_stay_available_during_execution(tmp_path, offline_cli):
	run_id = _persisted_run(tmp_path)
	cache, service = _service(tmp_path, lambda settings: _Pages())
	with cache, service.execution_lock(run_id) as acquired:
		assert acquired
		for command in ("status", "results", "stop"):
			result = offline_cli.invoke(cli, ["--data-dir", str(tmp_path), "--json", "crawl", command, run_id])
			assert result.exit_code == 0, result.output
			assert json.loads(result.stdout)["data"]["run_id"] == run_id
		assert cache.get_crawl_run(run_id)["stop_requested"] is True


@pytest.mark.parametrize("raised", [RuntimeError("offline failure"), KeyboardInterrupt(), SystemExit(3)])
def test_execution_right_is_released_on_failure_or_interruption(tmp_path, raised):
	def fail(page):
		raise raised

	run_id = _persisted_run(tmp_path)
	cache, service = _service(tmp_path, lambda settings: _Pages(fail))
	with cache:
		if isinstance(raised, (KeyboardInterrupt, SystemExit)):
			with pytest.raises(type(raised)):
				service.resume(run_id)
		else:
			assert service.resume(run_id).status == "stopped"
	transport = _Pages()
	cache, worker = _service(tmp_path, lambda settings: transport)
	with cache:
		# 对所有异常直接验证同一任务的锁已交还，不能用另建任务证明。
		with worker.execution_lock(run_id) as acquired:
			assert acquired
		assert worker.resume(run_id).status == "completed"
		assert transport.fetches == [1, 2, 3]
	assert _lock_files(tmp_path) == []


def test_different_runs_execute_independently(tmp_path):
	run_id = _persisted_run(tmp_path)
	cache, service = _service(tmp_path, lambda settings: _Pages())
	with cache, service.execution_lock(run_id) as acquired:
		assert acquired
		assert service.create_and_run(_settings(tmp_path)).status == "completed"
	assert _lock_files(tmp_path) == []


@pytest.mark.parametrize("failure", ["unsupported", "blocked-directory"])
def test_unavailable_lock_fails_closed_as_a_local_file_error(tmp_path, offline_cli, monkeypatch, failure):
	run_id = _persisted_run(tmp_path)
	if failure == "unsupported":
		def unsupported(handle):
			raise OSError(errno.ENOLCK, "No locks available")
		monkeypatch.setattr("boss_agent_cli.services.file_lock.try_lock_exclusive", unsupported)
	else:
		(tmp_path / "cache" / "crawl-locks").write_text("not a directory", encoding="utf-8")
	result = offline_cli.invoke(cli, ["--data-dir", str(tmp_path), "--json", "crawl", "resume", run_id])
	assert result.exit_code == 1, result.output
	payload = json.loads(result.stdout)
	assert set(payload) == ENVELOPE_KEYS
	assert payload["error"]["code"] == "LOCAL_IO_ERROR"
	assert payload["error"]["recoverable"] is False
	with CacheStore(tmp_path / "cache" / "boss_agent.db") as cache:
		run = cache.get_crawl_run(run_id)
		assert run["status"] == "queued"
		assert run["next_page"] == 1
	status = offline_cli.invoke(cli, ["--data-dir", str(tmp_path), "--json", "crawl", "status", run_id])
	assert status.exit_code == 0, status.output


_OFFLINE_CLI = textwrap.dedent("""\
import boss_agent_cli.commands.crawl as crawl
from boss_agent_cli.main import cli
crawl._session_seed = lambda ctx: ({}, "")
def forbidden(*args, **kwargs):
    raise AssertionError("must not construct transport")
crawl._transport_factory = forbidden
cli()
""")


def test_another_process_gets_a_busy_envelope_without_touching_the_run(tmp_path):
	run_id = _persisted_run(tmp_path)
	cache, service = _service(tmp_path, lambda settings: _Pages())
	with cache, service.execution_lock(run_id) as acquired:
		assert acquired
		before = cache.get_crawl_run(run_id)
		result = subprocess.run([
			sys.executable, "-c", _OFFLINE_CLI, "--data-dir", str(tmp_path), "--json",
			"--exit-codes", "sysexits", "crawl", "resume", run_id,
		], capture_output=True, text=True, timeout=30)
		assert result.returncode == 75, result.stderr
		payload = json.loads(result.stdout)
		assert set(payload) == ENVELOPE_KEYS
		assert payload["error"]["code"] == "CRAWL_BUSY"
		assert payload["hints"]["next_actions"] == [_status_command(tmp_path, run_id)]
		assert cache.get_crawl_run(run_id) == before


def test_execution_right_is_released_when_a_process_exits_abruptly(tmp_path):
	run_id = _persisted_run(tmp_path)
	child = subprocess.run([
		sys.executable, "-c", textwrap.dedent("""\
import os
import sys
from pathlib import Path
from boss_agent_cli.cache.store import CacheStore
from boss_agent_cli.crawler.service import CrawlService
class ExitTransport:
    def open(self):
        os._exit(17)
data_dir = Path(sys.argv[1])
with CacheStore(data_dir / "cache" / "boss_agent.db") as cache:
    CrawlService(cache, data_dir=data_dir, transport_factory=lambda settings: ExitTransport()).resume(sys.argv[2])
"""), str(tmp_path), run_id,
	], capture_output=True, text=True, timeout=30)
	assert child.returncode == 17, child.stderr
	assert len(_lock_files(tmp_path)) == 1
	cache, service = _service(tmp_path, lambda settings: _Pages())
	with cache:
		assert service.resume(run_id).status == "completed"
	assert _lock_files(tmp_path) == []


@pytest.mark.parametrize("step_name", ["crawl_resume", "crawl_start"])
def test_wizard_reports_busy_then_resumes_the_original_run(tmp_path, monkeypatch, step_name):
	# crawl_start 的旧断点与显式 crawl_resume 都要识别另一个执行器。
	run_id = _persisted_run(tmp_path)
	transport = _Pages()
	monkeypatch.setattr("boss_agent_cli.wizard.actions._crawl_seed_session", lambda ctx: ({}, ""))
	monkeypatch.setattr("boss_agent_cli.wizard.actions.DrissionCrawlerSession", lambda **kwargs: transport)
	monkeypatch.setattr("boss_agent_cli.wizard.actions.CrawlService", lambda cache, **kwargs: CrawlService(
		cache, budget_factory=_NoDelayBudget, **kwargs,
	))
	plan = build_plan(WizardInput.from_mapping({
		"role": "candidate", "platform": "zhipin", "goal": step_name,
		"inputs": {"run_id": run_id} if step_name == "crawl_resume" else {"query": "AI", "city": "杭州"},
		"requested_steps": [step_name],
	}))
	context = ActionContext(tmp_path, "zhipin", "candidate", Logger("error"))
	cache, service = _service(tmp_path, lambda settings: _Pages())
	with cache, WorkflowStore(tmp_path) as store:
		store.create("wrn-busy", plan)
		if step_name == "crawl_start":
			store.update_step("wrn-busy", step_name, status="waiting_input", result={"inner_run_id": run_id})
		with service.execution_lock(run_id) as acquired:
			assert acquired
			busy = WorkflowRunner(store, DEFAULT_ACTIONS).run(plan, context, run_id="wrn-busy")
			result = CliRunner().invoke(cli, [
				"--data-dir", str(tmp_path), "--json", "wizard", "--resume", "wrn-busy",
			])
			assert result.exit_code == 1, result.output
			payload = json.loads(result.stdout)
			assert set(payload) == ENVELOPE_KEYS
			assert payload["error"]["code"] == "CRAWL_BUSY"
			assert payload["hints"]["next_actions"] == [_status_command(tmp_path, run_id)]
			status = CliRunner().invoke(cli, [
				"--data-dir", str(tmp_path), "--json", "wizard", "--status", "wrn-busy",
			])
			assert status.exit_code == 0, status.output
			assert json.loads(status.stdout)["hints"]["next_actions"] == [_status_command(tmp_path, run_id)]
		assert busy["status"] == "failed"
		error = busy["steps"][0]["error"]
		assert error["code"] == "CRAWL_BUSY"
		assert error["recoverable"] is True
		assert error.get("replay_safe", True) is True
		assert _status_command(tmp_path, run_id) in error["recovery_action"]
		assert transport.fetches == []
		resumed = WorkflowRunner(store, DEFAULT_ACTIONS).run(plan, context, run_id="wrn-busy")
	assert resumed["status"] == "completed"
	assert resumed["steps"][0]["result"]["data"]["run_id"] == run_id
	assert transport.fetches == [1, 2, 3]
