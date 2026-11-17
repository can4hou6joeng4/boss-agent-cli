"""第四轮审计批次：Issue #1581–#1586 的回归测试。"""

import json
import socket
import sqlite3
from pathlib import Path

import pytest
from click.testing import CliRunner

from boss_agent_cli.automation.boss_adapter import BossRecruiterAutomationPlatform
from boss_agent_cli.cache.store import CacheStore
from boss_agent_cli.crawler.controls import CrawlHookDirInvalid
from boss_agent_cli.crawler.service import CrawlBudget, CrawlService, CrawlSettings
from boss_agent_cli.crawler.transport import DrissionCrawlerSession
from boss_agent_cli.main import cli
from boss_agent_cli.output import Logger
from boss_agent_cli.wizard.actions import ActionContext, DEFAULT_ACTIONS
from boss_agent_cli.wizard.catalog import build_plan
from boss_agent_cli.wizard.models import WizardInput
from boss_agent_cli.wizard.runner import WorkflowActionError
from boss_agent_cli.wizard.store import WorkflowStore


class _RecordingRecruiter:
	def __init__(self, *, greet: list[dict], friend: list[dict]) -> None:
		self.calls: list[str] = []
		self.greet = greet
		self.friend = friend

	def greet_list(self, page: int = 1, job_id: str | None = None) -> dict:
		self.calls.append("greet")
		return {"code": 0, "zpData": {"result": self.greet}}

	def friend_list(self, page: int = 1, label_id: int = 0, job_id: str | None = None) -> dict:
		self.calls.append("friend")
		return {"code": 0, "zpData": {"result": self.friend}}


def _item(index: int, name: str) -> dict:
	return {"friendId": index, "name": name}


def _free_port() -> int:
	with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as connection:
		connection.bind(("127.0.0.1", 0))
		return int(connection.getsockname()[1])


def _settings(tmp_path: Path, *, with_detail: bool = False, query: str = "AI") -> CrawlSettings:
	return CrawlSettings(
		query=query,
		city_code="101210100",
		pages=1,
		with_detail=with_detail,
		profile_path=tmp_path / "profile",
		chrome_path=None,
		cdp_port=_free_port(),
		hook_profile="none",
		operating_mode="research",
	)


class _NoDelayBudget(CrawlBudget):
	def wait(self, kind: str) -> None:
		pass


class _DetailTransport:
	def __init__(self, page: dict) -> None:
		self.page = page
		self.detail_calls: list[str] = []

	def open(self) -> list:
		return []

	def fetch_page(self, query: str, city_code: str, page_no: int) -> dict:
		return self.page

	def fetch_detail(self, security_id: str) -> dict:
		self.detail_calls.append(security_id)
		return {"code": 0, "zpData": {"jobCard": {"postDescription": "职位描述"}}}

	def close(self, *, quit_browser: bool = True) -> None:
		pass


def _invoke(data_dir: Path, *args: str):
	return CliRunner().invoke(cli, ["--data-dir", str(data_dir), "--json", *args])


# ── #1581：tabs 严格限定扫描范围 ──────────────────────────────────


def test_scan_respects_only_selected_tab_and_empty_config():
	greet = [_item(1, "招呼一"), _item(2, "招呼二")]
	friend = [_item(3, "未读一")]
	adapter = BossRecruiterAutomationPlatform(_RecordingRecruiter(greet=greet, friend=friend))

	assert [ref.id for ref in adapter.scan_conversations(["新招呼"], 1)] == ["1"]
	recruiter = adapter._recruiter
	assert recruiter.calls == ["greet"]

	adapter = BossRecruiterAutomationPlatform(_RecordingRecruiter(greet=[], friend=friend))
	assert adapter.scan_conversations([], 5) == []
	assert adapter._recruiter.calls == []
	assert adapter.scan_conversations(["未读"], 5)[0].id == "3"
	assert adapter._recruiter.calls == ["friend"]


def test_scan_applies_max_per_tab_independently():
	greet = [_item(1, "招呼一"), _item(2, "招呼二")]
	friend = [_item(3, "未读一"), _item(4, "未读二")]
	adapter = BossRecruiterAutomationPlatform(_RecordingRecruiter(greet=greet, friend=friend))
	refs = adapter.scan_conversations(["新招呼", "未读"], 1)

	assert [ref.id for ref in refs] == ["1", "3"]


# ── #1582：空白 query 三层拒绝 ────────────────────────────────────


def test_blank_crawl_query_is_rejected_by_build_plan():
	with pytest.raises(ValueError, match="缺少 inputs"):
		build_plan(
			WizardInput.from_mapping(
				{
					"role": "candidate",
					"platform": "zhipin",
					"goal": "crawl_start",
					"inputs": {"query": "   ", "city": "北京"},
				}
			)
		)


def test_blank_crawl_query_is_rejected_before_writing_a_run(tmp_path):
	context = ActionContext(tmp_path, "zhipin", "candidate", Logger())
	with pytest.raises(WorkflowActionError) as caught:
		DEFAULT_ACTIONS["crawl_start"](context, {"query": "  ", "city": "北京"}, {})

	assert caught.value.code == "INVALID_PARAM"
	assert "关键词" in str(caught.value)
	db_path = tmp_path / "cache" / "boss_agent.db"
	if not db_path.exists():
		return
	with sqlite3.connect(db_path) as connection:
		assert connection.execute("SELECT COUNT(*) FROM crawl_runs").fetchone()[0] == 0


def test_service_rejects_blank_query_even_if_callers_skip_validation(tmp_path):
	cache = CacheStore(tmp_path / "cache" / "boss_agent.db")
	with cache, pytest.raises(ValueError, match="关键词"):
		CrawlService(cache, data_dir=tmp_path, transport_factory=lambda settings: _DetailTransport({})).create(
			_settings(tmp_path, query="\t")
		)


# ── #1583：超大 SingletonLock pid 按陈旧锁处理 ────────────────────


def test_oversized_singleton_lock_pid_is_treated_as_stale(tmp_path):
	import socket as socket_module

	profile = tmp_path / "profile"
	profile.mkdir(parents=True)
	target = f"{socket_module.gethostname()}-{'9' * 400}"
	(profile / "SingletonLock").symlink_to(target)

	assert DrissionCrawlerSession._profile_owner(profile) is None


# ── #1584：空 securityId 不伪造详情成功 ───────────────────────────


def test_missing_security_id_stays_pending(tmp_path):
	page = {
		"code": 0,
		"zpData": {
			"jobList": [
				{
					"encryptJobId": "job-1",
					"securityId": "",
					"jobName": "AI 工程师",
				}
			],
			"hasMore": False,
		},
	}
	transport = _DetailTransport(page)
	cache = CacheStore(tmp_path / "cache" / "boss_agent.db")
	service = CrawlService(
		cache,
		data_dir=tmp_path,
		transport_factory=lambda settings: transport,
		budget_factory=_NoDelayBudget,
	)
	with cache:
		outcome = service.create_and_run(_settings(tmp_path, with_detail=True))
		assert outcome.status == "completed"
		items = cache.list_crawl_jobs(outcome.run_id)
		assert len(items) == 1
		assert items[0]["detail_done"] is False
		assert items[0]["payload"]["detail_status"] == "missing-security-id"
		assert transport.detail_calls == []


def test_fetch_detail_rejects_empty_security_id(tmp_path):
	session = DrissionCrawlerSession(
		profile_path=tmp_path / "profile",
		chrome_path=None,
		cdp_port=_free_port(),
		hook_profile="none",
		hook_dir=None,
	)
	with pytest.raises(ValueError, match="security_id"):
		session.fetch_detail("")


# ── #1585：已完成的 workflow 停止给出明确错误码 ───────────────────


def test_wizard_stop_on_completed_run_reports_already_finished(tmp_path):
	plan = build_plan(
		WizardInput.from_mapping(
			{
				"role": "candidate",
				"platform": "zhipin",
				"goal": "shortlist",
				"inputs": {},
			}
		)
	)
	with WorkflowStore(tmp_path) as store:
		store.create("done-run", plan)
		store.update_run("done-run", status="completed", current_step=None)

	result = _invoke(tmp_path, "wizard", "--stop", "done-run")
	assert result.exit_code == 1, result.output
	envelope = json.loads(result.stdout)
	assert envelope["ok"] is False
	assert envelope["error"]["code"] == "WORKFLOW_ALREADY_FINISHED"
	assert envelope["error"]["recoverable"] is False
	assert any("wizard --status" in action for action in envelope["hints"]["next_actions"])
	assert not any(action.endswith(" wizard") for action in envelope["hints"]["next_actions"])


# ── #1586：断点 Hook 目录坏按本地 IO 错误收口 ─────────────────────


def test_wizard_crawl_resume_maps_hook_dir_error_to_local_io_error():
	from boss_agent_cli.wizard.actions import _crawl_hook_dir_error

	error = _crawl_hook_dir_error(CrawlHookDirInvalid("坏的 Hook 目录", hook_dir="/tmp/hook-dir"))
	assert error.code == "LOCAL_IO_ERROR"
	assert error.recoverable is False
	assert error.replay_safe is False
	assert error.auto_retry is False
	assert "/tmp/hook-dir" in error.recovery_action
