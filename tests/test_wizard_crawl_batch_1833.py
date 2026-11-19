"""wizard / crawl 的 run_id 类型、部分失败断点与导出失败恢复（Issue #1833–#1836）。"""

import json
import shlex
from pathlib import Path

import pytest
from click.testing import CliRunner

from boss_agent_cli.cache.store import CacheStore
from boss_agent_cli.commands import crawl as crawl_module
from boss_agent_cli.crawler.controls import CrawlExportError
from boss_agent_cli.crawler.service import CrawlOutcome
from boss_agent_cli.main import cli
from boss_agent_cli.output import Logger
from boss_agent_cli.wizard.actions import ActionContext, _crawl_outcome_result, _crawl_resume, _crawl_start, _crawl_status
from boss_agent_cli.wizard.runner import WorkflowActionError


def _action_context(tmp_path: Path) -> ActionContext:
	return ActionContext(data_dir=tmp_path, platform="zhipin", role="candidate", logger=Logger())


def _outcome(status: str = "stopped", error: str = "") -> CrawlOutcome:
	return CrawlOutcome(
		run_id="run-1",
		status=status,
		next_page=2,
		pages_completed=3,
		jobs_seen=42,
		detail_checks=1,
		output_paths={"json": "/tmp/out/jobs.json"},
		hooks=(),
		error=error,
	)


class _RecordingService:
	def __init__(self) -> None:
		self.created = 0
		self.resumed: list[str] = []

	def create_and_run(self, settings):
		self.created += 1
		return _outcome("completed")

	def resume(self, run_id, **kwargs):
		self.resumed.append(run_id)
		return _outcome("completed")


# ── Issue #1833：显式 falsy run_id 不能静默新建采集 ──────────────────


@pytest.mark.parametrize("value", [0, False, [], "", "   "])
def test_crawl_start_rejects_explicit_invalid_run_id(tmp_path, monkeypatch, value) -> None:
	service = _RecordingService()
	monkeypatch.setattr("boss_agent_cli.wizard.actions._crawl_service", lambda *args, **kwargs: service)

	with pytest.raises(WorkflowActionError) as exc_info:
		_crawl_start(_action_context(tmp_path), {"query": "AI", "city": "杭州", "run_id": value}, {})

	assert exc_info.value.code == "INVALID_PARAM"
	assert service.created == 0
	assert service.resumed == []


def test_crawl_start_null_run_id_still_creates_new_crawl(tmp_path, monkeypatch) -> None:
	service = _RecordingService()
	monkeypatch.setattr("boss_agent_cli.wizard.actions._crawl_service", lambda *args, **kwargs: service)

	result = _crawl_start(_action_context(tmp_path), {"query": "AI", "city": "杭州", "run_id": None}, {})

	assert service.created == 1
	assert result.data["run_id"] == "run-1"


# ── Issue #1836：run_id 只接受非空字符串 ─────────────────────────────


@pytest.mark.parametrize("value", [123, ["r1"], {"run_id": "r1"}, True, "   "])
def test_crawl_status_rejects_non_string_run_id(tmp_path, value) -> None:
	with pytest.raises(WorkflowActionError) as exc_info:
		_crawl_status(_action_context(tmp_path), {"run_id": value}, {})

	assert exc_info.value.code == "INVALID_PARAM"
	assert "未找到" not in str(exc_info.value)


def test_crawl_resume_rejects_non_string_run_id(tmp_path) -> None:
	with pytest.raises(WorkflowActionError) as exc_info:
		_crawl_resume(_action_context(tmp_path), {"run_id": 123}, {})

	assert exc_info.value.code == "INVALID_PARAM"


def test_crawl_status_accepts_string_run_id_shape(tmp_path) -> None:
	"""合法字符串 run_id 仍按原语义报任务不存在，而不是参数错误。"""
	with pytest.raises(WorkflowActionError) as exc_info:
		_crawl_status(_action_context(tmp_path), {"run_id": "missing-run"}, {})

	assert exc_info.value.code == "CRAWL_RUN_NOT_FOUND"


# ── Issue #1834：限流 / 超时 / 停止要带回断点信息 ────────────────────


@pytest.mark.parametrize(
	("status", "code"),
	[
		("throttle_stopped", "REQUEST_BUDGET_EXHAUSTED"),
		("timeout_stopped", "WORKFLOW_TIMEOUT"),
		("workflow_stopped", "WORKFLOW_STOPPED"),
		("stopped", "NETWORK_ERROR"),
	],
)
def test_crawl_outcome_stopped_keeps_partial_details(status, code) -> None:
	with pytest.raises(WorkflowActionError) as exc_info:
		_crawl_outcome_result(_outcome(status, "中断"))

	error = exc_info.value
	assert error.code == code
	assert error.details is not None
	assert error.details["run_id"] == "run-1"
	assert error.details["pages_completed"] == 3
	assert error.details["jobs_seen"] == 42
	assert error.details["output_paths"] == {"json": "/tmp/out/jobs.json"}


# ── Issue #1835：导出失败要交代已知 run_id ──────────────────────────


def _enable_research(runner: CliRunner, tmp_path: Path) -> None:
	result = runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", "config", "set", "operating_mode", "research"])
	assert result.exit_code == 0, result.output


def _seed_login(tmp_path: Path) -> None:
	from boss_agent_cli.auth.token_store import TokenStore

	TokenStore(tmp_path / "auth").save(
		{"cookies": {"wt2": "test-wt2", "wbg": "1", "zp_at": "1"}, "user_agent": "test-agent", "stoken": ""}
	)


def test_crawl_resume_export_failure_reports_run_id(tmp_path, monkeypatch) -> None:
	def _raise(self, run_id, **kwargs):
		raise CrawlExportError("无法写入输出目录: 目标路径是同名文件")

	monkeypatch.setattr(crawl_module.CrawlService, "resume", _raise)
	runner = CliRunner()
	_enable_research(runner, tmp_path)
	with CacheStore(tmp_path / "cache" / "boss_agent.db") as cache:
		cache.create_crawl_run("run-42", {}, str(tmp_path / "crawl" / "runs" / "run-42"))

	result = runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", "crawl", "resume", "run-42"])

	assert result.exit_code == 1, result.output
	payload = json.loads(result.output)
	error = payload["error"]
	assert error["code"] == "LOCAL_IO_ERROR"
	assert error["details"]["run_id"] == "run-42"
	command_text = error["recovery_action"].split("然后执行 ", 1)[1].split(" 从断点继续", 1)[0]
	assert shlex.split(command_text) == [
		"boss", "--data-dir", str(tmp_path.resolve()), "crawl", "resume", "run-42",
	]


def test_crawl_run_export_failure_reports_created_run_id(tmp_path, monkeypatch) -> None:
	def _raise(self, settings):
		self._on_run_created("run-77")
		raise CrawlExportError("无法写入输出目录: 目标路径是同名文件")

	monkeypatch.setattr(crawl_module.CrawlService, "create_and_run", _raise)
	runner = CliRunner()
	_enable_research(runner, tmp_path)
	_seed_login(tmp_path)

	result = runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", "crawl", "run", "AI", "--city", "杭州"])

	assert result.exit_code == 1, result.output
	error = json.loads(result.output)["error"]
	assert error["code"] == "LOCAL_IO_ERROR"
	assert error["details"]["run_id"] == "run-77"
	assert "run-77" in error["recovery_action"]
