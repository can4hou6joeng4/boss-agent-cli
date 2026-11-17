"""批次十一：采集浏览器启动、刷新锁、出口矛盾与共享预算的错误分类。

覆盖 Issue #1553–#1556 的用户可见契约：这些失败都不能再落成 ok:true 的
``stopped``、删除他人持有的锁、误报成安装依赖，或把本地节流超时当成任务预算
耗尽。
"""

import json
import sys
import types
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from boss_agent_cli.api.egress import EgressContradictionError, EgressVerdict
from boss_agent_cli.api.request_budget import RequestBudgetExceeded
from boss_agent_cli.auth import token_store as token_store_module
from boss_agent_cli.auth.manager import AuthManager
from boss_agent_cli.auth.token_store import RefreshLockBusy, TokenStore
from boss_agent_cli.cache.store import CacheStore
from boss_agent_cli.commands import crawl as crawl_module
from boss_agent_cli.crawler.controls import CrawlBrowserStartError
from boss_agent_cli.crawler.service import CrawlOutcome, CrawlService, CrawlSettings
from boss_agent_cli.crawler.transport import DrissionCrawlerSession
from boss_agent_cli.main import cli
from boss_agent_cli.output import Logger
from boss_agent_cli.wizard.actions import ActionContext, _crawl_outcome_result, _crawl_resume, _crawl_start
from boss_agent_cli.wizard.runner import WorkflowActionError


class _BrowserBoom(Exception):
	pass


def _egress_error() -> EgressContradictionError:
	detail = "Accept-Language 地区为 CN，出口在 US；时区落在 CN，出口在 US"
	return EgressContradictionError(
		EgressVerdict(
			status="mismatch",
			exit_country="US",
			contradictions=("Accept-Language 地区为 CN，出口在 US", "时区落在 CN，出口在 US"),
			detail=detail,
		)
	)


def _outcome(run_id: str, status: str, *, error: str = "", data_dir: Path | None = None) -> CrawlOutcome:
	return CrawlOutcome(
		run_id=run_id,
		status=status,
		next_page=2,
		pages_completed=1,
		jobs_seen=0,
		detail_checks=0,
		output_paths={},
		hooks=(),
		error=error,
		data_dir=data_dir,
	)


def _enable_research(runner: CliRunner, tmp_path: Path) -> None:
	result = runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", "config", "set", "operating_mode", "research"])
	assert result.exit_code == 0, result.output


def _seed_login(tmp_path: Path) -> None:
	TokenStore(tmp_path / "auth").save(
		{
			"cookies": {"wt2": "test-wt2", "wbg": "1", "zp_at": "1"},
			"user_agent": "test-agent",
			"stoken": "",
		}
	)


def _invoke(runner: CliRunner, tmp_path: Path, *args: str):
	_seed_login(tmp_path)
	return runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", *args])


def _envelope(result) -> dict[str, Any]:
	return json.loads(result.output)


# ── Issue #1554：刷新锁不得删除他人的锁 ─────────────────────────


def test_refresh_lock_does_not_delete_another_holders_lock(tmp_path, monkeypatch):
	store = TokenStore(tmp_path / "auth")
	lock_path = tmp_path / "auth" / "refresh.lock"
	monkeypatch.setattr(token_store_module, "_LOCK_TIMEOUT", 0.05)

	holder = store.refresh_lock()
	holder.__enter__()
	try:
		assert lock_path.exists()
		with pytest.raises(RefreshLockBusy):
			with TokenStore(tmp_path / "auth").refresh_lock():
				pass
		# 超时的等待方没有删除对方仍在使用的锁。
		assert lock_path.exists()
	finally:
		holder.__exit__(None, None, None)

	assert not lock_path.exists()
	with store.refresh_lock():
		pass


def test_refresh_lock_stale_file_is_not_treated_as_held(tmp_path):
	"""进程崩溃留下的锁文件没有内核锁，不应阻塞下一个刷新者。"""
	store = TokenStore(tmp_path / "auth")
	lock_path = tmp_path / "auth" / "refresh.lock"
	lock_path.parent.mkdir(parents=True, exist_ok=True)
	lock_path.touch()

	with store.refresh_lock():
		pass

	assert not lock_path.exists()


def test_force_refresh_adopts_concurrent_refresh_without_relaunching(tmp_path, monkeypatch):
	from boss_agent_cli.auth import manager as manager_module

	class _Store:
		def __init__(self) -> None:
			self.loads = 0
			self.saved: list[dict[str, Any]] = []

		def load(self) -> dict[str, Any]:
			self.loads += 1
			stoken = "old" if self.loads == 1 else "new"
			return {"cookies": {"wt2": "fixture"}, "stoken": stoken}

		@contextmanager
		def refresh_lock(self):
			yield

		def save(self, data: dict[str, Any]) -> None:
			self.saved.append(data)

	def _unexpected(*args, **kwargs):
		raise AssertionError("另一个进程已刷新时应直接采纳新 stoken，不再启动浏览器")

	monkeypatch.setattr(manager_module, "probe_cdp", _unexpected)
	monkeypatch.setattr(manager_module, "refresh_stoken", _unexpected)
	manager = AuthManager(tmp_path)
	store = _Store()
	manager._store = store  # type: ignore[assignment]

	manager.force_refresh()

	assert manager._token is not None and manager._token["stoken"] == "new"
	assert store.saved == []


def test_force_refresh_reports_refresh_lock_busy_as_retryable(tmp_path, monkeypatch):
	class _BusyStore:
		def load(self) -> dict[str, Any]:
			return {"cookies": {"wt2": "fixture"}, "stoken": "old"}

		def refresh_lock(self):
			raise RefreshLockBusy("另一个进程正在刷新登录态，等待 30 秒仍未释放锁")

	manager = AuthManager(tmp_path)
	manager._store = _BusyStore()  # type: ignore[assignment]

	with pytest.raises(RequestBudgetExceeded) as exc_info:
		manager.force_refresh()

	assert exc_info.value.code == "REQUEST_BUDGET_EXHAUSTED"
	assert exc_info.value.details["request_sent"] is False
	assert "另一个进程正在刷新登录态" in str(exc_info.value)
	assert "请稍后重试" in str(exc_info.value)


# ── Issue #1553：浏览器启动失败 ────────────────────────────────


def _fake_drissionpage(monkeypatch, page_factory):
	class _Options:
		def set_local_port(self, *args: Any) -> None:
			pass

		def existing_only(self, *args: Any) -> None:
			pass

		def set_user_data_path(self, *args: Any) -> None:
			pass

		def set_browser_path(self, *args: Any) -> None:
			pass

		def set_user_agent(self, *args: Any) -> None:
			pass

	fake = types.ModuleType("DrissionPage")
	fake.ChromiumOptions = _Options
	fake.ChromiumPage = page_factory
	monkeypatch.setitem(sys.modules, "DrissionPage", fake)


@pytest.mark.parametrize("attached", [False, True])
def test_transport_open_wraps_browser_constructor_failure(tmp_path, monkeypatch, attached):
	session = DrissionCrawlerSession(
		profile_path=tmp_path / "profile",
		chrome_path=None,
		cdp_port=9231,
		hook_profile="none",
		hook_dir=None,
	)
	monkeypatch.setattr(session, "_port_is_in_use", lambda port: attached)
	monkeypatch.setattr(session, "_profile_owner", lambda path: None)
	monkeypatch.setattr(session, "_clear_stale_profile_locks", lambda path: None)

	def _raise(options):
		raise _BrowserBoom("chrome missing")

	_fake_drissionpage(monkeypatch, _raise)

	with pytest.raises(CrawlBrowserStartError) as exc_info:
		session.open()

	error = exc_info.value
	assert error.cdp_port == 9231
	assert error.attached is attached
	assert "_BrowserBoom" in str(error)
	assert "9231" in str(error)
	assert ("附着到已有的采集浏览器" if attached else "启动采集浏览器") in str(error)


def test_crawl_service_re_raises_browser_start_and_saves_checkpoint(tmp_path, monkeypatch):
	monkeypatch.delenv("BOSS_EGRESS_PROBE_URL", raising=False)
	start_error = CrawlBrowserStartError(
		"无法启动采集浏览器（调试端口 9229）：_BrowserBoom: chrome missing",
		cdp_port=9229,
		attached=False,
	)

	class _Transport:
		def open(self):
			raise start_error

		def close(self, **kwargs):
			pass

	settings = CrawlSettings(
		query="AI",
		city_code="101210100",
		pages=1,
		with_detail=False,
		profile_path=tmp_path / "profile",
		chrome_path=None,
		cdp_port=9229,
		hook_profile="none",
	)
	created: list[str] = []
	cache = CacheStore(tmp_path / "cache" / "boss_agent.db")
	try:
		service = CrawlService(
			cache,
			data_dir=tmp_path,
			transport_factory=lambda _settings: _Transport(),
			on_run_created=created.append,
		)
		with pytest.raises(CrawlBrowserStartError):
			service.create_and_run(settings)
		assert len(created) == 1
		run = cache.get_crawl_run(created[0])
		assert run is not None
		assert run["status"] == "stopped"
		assert "无法启动采集浏览器" in run["error"]
	finally:
		cache.close()


def test_crawl_run_reports_browser_start_failure_with_chrome_guidance(tmp_path, monkeypatch):
	def _raise(self, settings):
		raise CrawlBrowserStartError(
			"无法启动采集浏览器（调试端口 9229）：_BrowserBoom: chrome missing",
			cdp_port=9229,
			attached=False,
		)

	monkeypatch.setattr(crawl_module.CrawlService, "create_and_run", _raise)
	runner = CliRunner()
	_enable_research(runner, tmp_path)
	result = _invoke(runner, tmp_path, "crawl", "run", "AI", "--city", "杭州")

	assert result.exit_code == 1, result.output
	error = _envelope(result)["error"]
	assert error["code"] == "CRAWL_UNAVAILABLE"
	assert error["recoverable"] is True
	assert "chrome_path" in error["recovery_action"]
	assert "调试端口 9229" in error["recovery_action"]
	assert "9229" in error["message"]


# ── Issue #1556：共享预算等待超时 ──────────────────────────────


def test_crawl_run_throttle_stop_points_at_resume_not_new_task(tmp_path, monkeypatch):
	monkeypatch.setattr(
		crawl_module.CrawlService,
		"create_and_run",
		lambda self, settings: _outcome("run-throttle", "throttle_stopped", error="请求节流等待超时，请求未发出"),
	)
	runner = CliRunner()
	_enable_research(runner, tmp_path)
	result = _invoke(runner, tmp_path, "crawl", "run", "AI", "--city", "杭州")

	assert result.exit_code == 0, result.output
	payload = _envelope(result)
	assert payload["data"]["status"] == "throttle_stopped"
	next_actions = payload["hints"]["next_actions"]
	assert len(next_actions) == 1
	assert "crawl resume run-throttle" in next_actions[0]
	operator_actions = payload["hints"]["operator_actions"]
	assert any("节流" in action for action in operator_actions)
	assert all("继续采集需要新建任务" not in action for action in operator_actions)


def test_wizard_throttle_stop_is_retryable_resume(tmp_path):
	outcome = _outcome(
		"run-throttle",
		"throttle_stopped",
		error="请求节流等待超时，请求未发出",
		data_dir=tmp_path,
	)

	with pytest.raises(WorkflowActionError) as exc_info:
		_crawl_outcome_result(outcome)

	error = exc_info.value
	assert error.code == "REQUEST_BUDGET_EXHAUSTED"
	assert error.recoverable is True
	assert error.auto_retry is False
	assert "crawl resume run-throttle" in str(error.recovery_action)


# ── Issue #1555：出口与语言/时区矛盾 ───────────────────────────


def test_crawl_run_maps_egress_contradiction(tmp_path, monkeypatch):
	def _raise(self, settings):
		raise _egress_error()

	monkeypatch.setattr(crawl_module.CrawlService, "create_and_run", _raise)
	runner = CliRunner()
	_enable_research(runner, tmp_path)
	result = _invoke(runner, tmp_path, "crawl", "run", "AI", "--city", "杭州")

	assert result.exit_code == 1, result.output
	error = _envelope(result)["error"]
	assert error["code"] == "EGRESS_CONTRADICTION"
	assert error["recoverable"] is False
	assert error["details"]["egress"]["exit_country"] == "US"
	assert "BOSS_EGRESS_PROBE_URL" in error["recovery_action"]
	assert "安装" not in error["recovery_action"]


def test_crawl_start_maps_egress_contradiction(tmp_path, monkeypatch):
	def _raise(self, settings):
		raise _egress_error()

	monkeypatch.setattr(crawl_module.CrawlService, "create", _raise)
	runner = CliRunner()
	_enable_research(runner, tmp_path)
	result = _invoke(runner, tmp_path, "crawl", "start", "AI", "--city", "杭州")

	assert result.exit_code == 1, result.output
	error = _envelope(result)["error"]
	assert error["code"] == "EGRESS_CONTRADICTION"
	assert error["recoverable"] is False


def test_agent_crawl_maps_egress_contradiction(tmp_path, monkeypatch):
	runner = CliRunner()
	_init = runner.invoke(
		cli,
		["--data-dir", str(tmp_path), "--json", "resume", "init", "--name", "test-resume", "--template", "default"],
	)
	assert _init.exit_code == 0, _init.output
	_seed_login(tmp_path)

	def _raise(self, settings):
		raise _egress_error()

	monkeypatch.setattr("boss_agent_cli.commands.agent.CrawlService.create_and_run", _raise)
	result = runner.invoke(
		cli,
		[
			"--data-dir",
			str(tmp_path),
			"--json",
			"agent",
			"crawl",
			"--query",
			"AI",
			"--city",
			"杭州",
			"--resume",
			"test-resume",
		],
	)

	assert result.exit_code == 1, result.output
	payload = _envelope(result)
	assert payload["command"] == "agent.crawl"
	assert payload["error"]["code"] == "EGRESS_CONTRADICTION"
	assert payload["error"]["recoverable"] is False


def _action_context(tmp_path: Path) -> ActionContext:
	return ActionContext(data_dir=tmp_path, platform="zhipin", role="candidate", logger=Logger())


class _RaisingService:
	def __init__(self, error: Exception, *, on: str = "create_and_run") -> None:
		self.error = error
		self.on = on

	def create_and_run(self, settings):
		raise self.error

	def resume(self, run_id, **kwargs):
		raise self.error


def test_wizard_start_maps_egress_contradiction(tmp_path, monkeypatch):
	service = _RaisingService(_egress_error())
	monkeypatch.setattr("boss_agent_cli.wizard.actions._crawl_service", lambda context, cache, **_: service)
	context = _action_context(tmp_path)

	with pytest.raises(WorkflowActionError) as exc_info:
		_crawl_start(context, {"query": "AI", "city": "杭州"}, {})

	error = exc_info.value
	assert error.code == "EGRESS_CONTRADICTION"
	assert error.recoverable is False
	assert error.auto_retry is False
	assert error.details is not None
	assert error.details["egress"]["exit_country"] == "US"


def test_wizard_resume_maps_browser_start_failure(tmp_path, monkeypatch):
	service = _RaisingService(
		CrawlBrowserStartError(
			"无法附着到已有的采集浏览器（调试端口 9231）：_BrowserBoom: closed",
			cdp_port=9231,
			attached=True,
		)
	)
	monkeypatch.setattr("boss_agent_cli.wizard.actions._crawl_service", lambda context, cache, **_: service)
	context = _action_context(tmp_path)

	with pytest.raises(WorkflowActionError) as exc_info:
		_crawl_resume(context, {"run_id": "run-1"}, {})

	error = exc_info.value
	assert error.code == "CRAWL_UNAVAILABLE"
	assert error.recoverable is True
	assert error.auto_retry is True
	assert "chrome_path" in str(error.recovery_action)


# ── 登记表 ────────────────────────────────────────────────────


def test_schema_registers_egress_contradiction():
	from boss_agent_cli.schema.data import SCHEMA_DATA

	spec = SCHEMA_DATA["error_codes"]["EGRESS_CONTRADICTION"]
	assert spec["recoverable"] is False
	assert "BOSS_EGRESS_PROBE_URL" in spec["recovery_action"]
