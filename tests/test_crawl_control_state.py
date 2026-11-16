"""`boss crawl` 停止状态机与导出顺序的回归测试（Issue #1238）。

三个接缝各有一组用例：

- transport 内透传的 wizard ambient 控制信号（``WorkflowActionError`` 的
  ``WORKFLOW_STOPPED`` / ``WORKFLOW_TIMEOUT``）必须落成 crawl 自己的
  ``workflow_stopped`` / ``timeout_stopped``；普通异常仍是 ``stopped``。
- 最后一页导出必须发生在 ``completed`` 落库之前：导出失败时任务保持可恢复，
  再次 ``resume`` 仍能补齐产物，而不是带着 ``completed`` 报成功却无文件。
- 命令层把导出失败映射为不可重试的 ``LOCAL_IO_ERROR``；stop / 预算用尽只给
  只读的 ``crawl status``，不给会清除 stop 标记或原地打转的 ``crawl resume``。
"""

import json
import shlex
import socket
from pathlib import Path

import pytest
from click.testing import CliRunner

from boss_agent_cli.cache.store import CacheStore
from boss_agent_cli.commands import crawl as crawl_module
from boss_agent_cli.crawler import service as service_module
from boss_agent_cli.crawler.controls import CrawlExportError
from boss_agent_cli.crawler.service import CrawlBudget, CrawlOutcome, CrawlService, CrawlSettings
from boss_agent_cli.main import cli
from boss_agent_cli.wizard.actions import _crawl_outcome_result
from boss_agent_cli.wizard.runner import WorkflowActionError


def _free_port() -> int:
	with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as connection:
		connection.bind(("127.0.0.1", 0))
		return int(connection.getsockname()[1])


def _job(job_id: str, security_id: str) -> dict:
	return {
		"encryptJobId": job_id,
		"securityId": security_id,
		"jobName": "AI 工程师",
		"salaryDesc": "20-30K",
		"cityName": "杭州",
		"brandName": "测试公司",
		"jobLabels": ["Python", "LLM"],
		"welfareList": ["双休"],
	}


def _page(*jobs: dict, has_more: bool = False) -> dict:
	return {"code": 0, "zpData": {"jobList": list(jobs), "hasMore": has_more}}


class _NoDelayBudget(CrawlBudget):
	def wait(self, kind: str) -> None:
		self._cache.put_crawl_budget(f"test:{kind}", 1.0)


class _SignalTransport:
	"""open 后第一次取页就抛给定异常，用来模拟 ambient 控制信号的透传。"""

	def __init__(self, error: Exception) -> None:
		self.error = error
		self.page_calls: list[int] = []
		self.closed = False

	def open(self) -> list:
		return []

	def fetch_page(self, query: str, city_code: str, page_no: int) -> dict:
		self.page_calls.append(page_no)
		raise self.error

	def fetch_detail(self, security_id: str) -> dict:
		raise self.error

	def close(self, quit_browser: bool = True) -> None:
		self.closed = True


class _FakeTransport:
	def __init__(self, pages: dict[int, dict] | None = None) -> None:
		self.pages = pages or {}
		self.page_calls: list[int] = []
		self.closed = False

	def open(self) -> list:
		return []

	def fetch_page(self, query: str, city_code: str, page_no: int) -> dict:
		self.page_calls.append(page_no)
		return self.pages[page_no]

	def fetch_detail(self, security_id: str) -> dict:
		return {"code": 0, "zpData": {"jobCard": {"postDescription": "职位描述", "address": "西湖区"}}}

	def close(self) -> None:
		self.closed = True


def _settings(tmp_path: Path, *, pages: int = 1, max_retries: int = 0) -> CrawlSettings:
	return CrawlSettings(
		query="AI",
		city_code="101210100",
		pages=pages,
		with_detail=False,
		profile_path=tmp_path / "profile",
		chrome_path=None,
		cdp_port=_free_port(),
		hook_profile="none",
		max_requests=20,
		max_details=50,
		max_seconds=600,
		max_retries=max_retries,
		operating_mode="research",
	)


def _service(tmp_path: Path, transport_factory):
	cache = CacheStore(tmp_path / "cache.db")
	return cache, CrawlService(
		cache,
		data_dir=tmp_path,
		transport_factory=transport_factory,
		budget_factory=lambda store: _NoDelayBudget(store),
	)


# ── service：ambient 控制信号分类 ─────────────────────────────────────


@pytest.mark.parametrize(
	("code", "expected"),
	[
		("WORKFLOW_STOPPED", "workflow_stopped"),
		("WORKFLOW_TIMEOUT", "timeout_stopped"),
	],
)
def test_ambient_control_signal_from_transport_becomes_crawl_stop_state(tmp_path, code, expected):
	"""control 检查抛出的 WorkflowActionError 不能落成可重试的普通 stopped。"""
	signal = WorkflowActionError(code, "已按 stop 请求停止 workflow")
	transport = _SignalTransport(signal)
	cache, service = _service(tmp_path, lambda settings: transport)

	outcome = service.create_and_run(_settings(tmp_path))

	assert outcome.status == expected
	assert outcome.error == str(signal)
	assert cache.get_crawl_run(outcome.run_id)["status"] == expected
	assert transport.closed is True


def test_transport_error_without_control_code_stays_plain_stopped(tmp_path):
	"""没有控制码的普通异常仍按 stopped 处理，不误升成 workflow_stopped。"""
	transport = _SignalTransport(RuntimeError("listener timeout"))
	cache, service = _service(tmp_path, lambda settings: transport)

	outcome = service.create_and_run(_settings(tmp_path))

	assert outcome.status == "stopped"
	assert cache.get_crawl_run(outcome.run_id)["status"] == "stopped"


# ── service：导出必须早于 completed 落库 ─────────────────────────────


def test_export_failure_keeps_run_resumable_until_artifacts_exist(tmp_path, monkeypatch):
	"""首次导出失败不能把 completed 写进库，否则后续 resume 报成功却无产物。"""
	first = _FakeTransport({1: _page(_job("job-1", "sec-1"))})
	second = _FakeTransport({})
	transports = iter((first, second))
	cache, service = _service(tmp_path, lambda settings: next(transports))
	real_export = service_module.write_run_outputs
	calls: list[int] = []

	def flaky_export(output_dir, rows):
		calls.append(1)
		if len(calls) == 2:
			raise CrawlExportError("输出目录不可写")
		return real_export(output_dir, rows)

	monkeypatch.setattr(service_module, "write_run_outputs", flaky_export)
	run_id = service.create(_settings(tmp_path))

	with pytest.raises(CrawlExportError):
		service.resume(run_id, operating_mode="research")

	run = cache.get_crawl_run(run_id)
	assert run is not None
	assert run["status"] == "stopped"
	assert run["list_finished"]
	assert calls == [1, 1]

	monkeypatch.setattr(service_module, "write_run_outputs", real_export)
	outcome = service.resume(run_id, operating_mode="research")

	assert outcome.status == "completed"
	assert cache.get_crawl_run(run_id)["status"] == "completed"
	assert second.page_calls == []
	json_path = Path(outcome.output_paths["json"])
	assert json_path.exists()
	assert len(json.loads(json_path.read_text(encoding="utf-8"))) == 1


# ── 命令层：LOCAL_IO_ERROR 与恢复指引 ────────────────────────────────


def _enable_research(runner: CliRunner, tmp_path: Path) -> None:
	result = runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", "config", "set", "operating_mode", "research"])
	assert result.exit_code == 0, result.output


def _seed_login(tmp_path: Path) -> None:
	from boss_agent_cli.auth.token_store import TokenStore

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


def _envelope(result) -> dict:
	return json.loads(result.output)


def test_run_maps_export_failure_to_local_io_error(tmp_path, monkeypatch):
	"""CrawlExportError 是 RuntimeError 子类，不能兜成让人去装依赖的 CRAWL_UNAVAILABLE。"""

	def _raise(self, settings):
		raise CrawlExportError("无法写入输出目录: 目标路径是同名文件")

	monkeypatch.setattr(crawl_module.CrawlService, "create_and_run", _raise)
	runner = CliRunner()
	_enable_research(runner, tmp_path)

	result = _invoke(runner, tmp_path, "crawl", "run", "AI", "--city", "杭州")

	assert result.exit_code == 1
	error = _envelope(result)["error"]
	assert error["code"] == "LOCAL_IO_ERROR"
	assert error["recoverable"] is False
	assert "crawl resume" in error["recovery_action"]


class _Outcome:
	def __init__(self, status: str) -> None:
		self.run_id = "run-x"
		self.status = status
		self.output_paths: dict[str, str] = {}

	def as_dict(self) -> dict:
		return {"run_id": self.run_id, "status": self.status, "output_paths": {}}


@pytest.mark.parametrize(
	("status", "operator_fragment"),
	[
		("budget_stopped", "预算已用完"),
		("workflow_stopped", "停止请求"),
	],
)
def test_run_stop_hints_point_at_read_only_status(tmp_path, monkeypatch, status, operator_fragment):
	"""stop 与预算用尽的 next_actions 只能是只读的 crawl status。"""
	monkeypatch.setattr(crawl_module.CrawlService, "create_and_run", lambda self, settings: _Outcome(status))
	runner = CliRunner()
	_enable_research(runner, tmp_path)

	result = _invoke(runner, tmp_path, "crawl", "run", "AI", "--city", "杭州")

	assert result.exit_code == 0, result.output
	hints = _envelope(result)["hints"]
	assert [shlex.split(action) for action in hints["next_actions"]] == [
		["boss", "--data-dir", str(tmp_path.resolve()), "crawl", "status", "run-x"],
	]
	assert any(operator_fragment in action for action in hints["operator_actions"])


# ── wizard：stop 识别不再依赖单一英文文案 ────────────────────────────


def _outcome(status: str = "stopped", error: str = "") -> CrawlOutcome:
	return CrawlOutcome(
		run_id="run-1",
		status=status,
		next_page=2,
		pages_completed=1,
		jobs_seen=3,
		detail_checks=0,
		output_paths={},
		hooks=(),
		error=error,
	)


def test_crawl_outcome_workflow_stopped_ignores_error_wording():
	"""service 已按错误码落了 workflow_stopped，向导不再要求 error 里出现英文文案。"""
	with pytest.raises(WorkflowActionError) as exc_info:
		_crawl_outcome_result(_outcome("workflow_stopped", "fixture stop"))

	error = exc_info.value
	assert error.code == "WORKFLOW_STOPPED"
	assert error.recoverable is False
	assert shlex.split(error.recovery_action) == ["boss", "crawl", "status", "run-1"]


@pytest.mark.parametrize("message", ["workflow stop requested", "已按 stop 请求停止 workflow"])
def test_plain_stopped_with_stop_wording_never_offers_resume(message):
	"""旧记录里只有文案可依时，stop 的恢复命令也不能是会清标记的 resume。"""
	with pytest.raises(WorkflowActionError) as exc_info:
		_crawl_outcome_result(_outcome("stopped", message))

	assert exc_info.value.code == "WORKFLOW_STOPPED"
	assert "resume" not in exc_info.value.recovery_action


def test_plain_stopped_with_unrelated_error_keeps_resume():
	"""非 stop 的普通中断仍是可重试的 NETWORK_ERROR，恢复命令保持 resume。"""
	with pytest.raises(WorkflowActionError) as exc_info:
		_crawl_outcome_result(_outcome("stopped", "listener timeout"))

	error = exc_info.value
	assert error.code == "NETWORK_ERROR"
	assert error.recoverable is True
	assert shlex.split(error.recovery_action) == ["boss", "crawl", "resume", "run-1"]
