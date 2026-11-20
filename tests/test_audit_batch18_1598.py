"""第六轮审计批次：Issue #1598–#1601 的回归测试。

全部测试离线运行：不调用平台 API，不启动浏览器。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from click.testing import CliRunner

from boss_agent_cli.api.zhipin_errors import is_platform_success_code
from boss_agent_cli.automation.boss_adapter import _action_result, BossRecruiterAutomationPlatform
from boss_agent_cli.automation.config import AutomationConfig, automation_config_from_dict
from boss_agent_cli.automation.safety import SafetyGuard
from boss_agent_cli.automation.storage import AutomationStore
from boss_agent_cli.cache.store import CacheStore
from boss_agent_cli.crawler.service import CrawlBudget, CrawlRunBudget, CrawlService, CrawlSettings
from boss_agent_cli.main import cli
from boss_agent_cli.output import Logger
from boss_agent_cli.wizard.actions import ActionContext, _positive_int
from boss_agent_cli.wizard.runner import WorkflowActionError


def _store(tmp_path: Path, stop: dict) -> AutomationStore:
	root = tmp_path / "automation"
	root.mkdir(parents=True, exist_ok=True)
	(root / "stop.json").write_text(json.dumps(stop), encoding="utf-8")
	return AutomationStore(tmp_path)


def _settings(tmp_path: Path) -> CrawlSettings:
	return CrawlSettings(
		query="AI",
		city_code="101210100",
		pages=1,
		with_detail=False,
		profile_path=tmp_path / "profile",
		chrome_path=None,
		cdp_port=9222,
		hook_profile="none",
	)


class _NoDelayBudget(CrawlBudget):
	def wait(self, kind: str) -> None:
		pass


class _PayloadTransport:
	def __init__(self, page: dict) -> None:
		self.page = page

	def open(self) -> list:
		return []

	def fetch_page(self, query: str, city_code: str, page_no: int) -> dict:
		return self.page

	def fetch_detail(self, security_id: str) -> dict:
		return self.page

	def close(self, *, quit_browser: bool = True) -> None:
		pass


def _invoke(data_dir: Path, *args: str):
	return CliRunner().invoke(cli, ["--json", "--data-dir", str(data_dir), *args])


# ── #1598：stop.json 缺少 open 字段 ─────────────────────────────────


@pytest.mark.parametrize("reason", ["operator requested stop", None])
def test_missing_open_fails_closed_and_can_be_resumed(tmp_path: Path, reason: str | None) -> None:
	stop = {"reason": reason} if reason is not None else {}
	store = _store(tmp_path, stop)

	latch = store.read_stop()
	assert latch["open"] is True
	assert latch["corrupt"] is True
	guard = SafetyGuard(AutomationConfig(), store.read_state(), dry_run=True, store=store)
	assert guard.stop_reason()

	# 与类型漂移一样，显式恢复后闩锁必须可解除。
	assert store.resume() is True
	assert store.read_stop()["open"] is False
	assert SafetyGuard(AutomationConfig(), store.read_state(), dry_run=True, store=store).stop_reason() is None


def test_missing_open_is_reported_by_agent_stats(tmp_path: Path) -> None:
	_store(tmp_path, {"reason": "operator requested stop"})

	result = _invoke(tmp_path, "agent", "stats")

	assert result.exit_code == 0
	breaker = json.loads(result.output)["data"]["circuit_breaker"]
	assert breaker["open"] is True
	assert breaker["corrupt"] is True


# ── #1599：code=false 不能当成功 ────────────────────────────────────


@pytest.mark.parametrize("value", [False, True])
def test_boolean_code_is_not_a_success_code(value: bool) -> None:
	assert is_platform_success_code(value) is False


@pytest.mark.parametrize("code", [False, "false"])
def test_false_code_in_list_response_is_an_error(tmp_path: Path, code: object) -> None:
	cache = CacheStore(tmp_path / "cache" / "boss_agent.db")
	transport = _PayloadTransport({"code": code, "message": "bad payload", "zpData": {"jobList": [], "hasMore": False}})
	service = CrawlService(
		cache,
		data_dir=tmp_path,
		transport_factory=lambda settings: transport,
		budget_factory=_NoDelayBudget,
	)
	settings = _settings(tmp_path)
	# 执行器内部方法要求断点存在（Issue #2208 的 fail-closed 检查）。
	run_id = service.create(settings)

	with cache, pytest.raises(RuntimeError, match="接口错误"):
		service._fetch_list(
			run_id, transport, _NoDelayBudget(cache), CrawlRunBudget(settings), settings, 1
		)


@pytest.mark.parametrize("code", [False, "false"])
def test_false_code_in_detail_response_is_an_error(tmp_path: Path, code: object) -> None:
	cache = CacheStore(tmp_path / "cache" / "boss_agent.db")
	transport = _PayloadTransport({"code": code, "message": "bad payload"})
	service = CrawlService(
		cache,
		data_dir=tmp_path,
		transport_factory=lambda settings: transport,
		budget_factory=_NoDelayBudget,
	)

	settings = _settings(tmp_path)
	# 执行器内部方法要求断点存在（Issue #2208 的 fail-closed 检查）。
	run_id = service.create(settings)

	with cache, pytest.raises(RuntimeError, match="接口错误"):
		service._fetch_detail(
			run_id, transport, _NoDelayBudget(cache), CrawlRunBudget(settings), "sec-1"
		)


@pytest.mark.parametrize("code, expected", [(False, "blocked"), (0, "executed")])
def test_boolean_and_zero_code_in_write_response_are_distinguished(code: object, expected: str) -> None:
	result = _action_result({"code": code, "message": "拒绝"})

	assert result.status == expected


@pytest.mark.parametrize("code", [False, "false"])
def test_false_code_in_checked_response_is_not_accepted(code: object) -> None:
	class _Recruiter:
		def greet_list(self, page: int = 1, job_id: str | None = None) -> dict:
			return {"code": code, "message": "拒绝"}

	adapter = BossRecruiterAutomationPlatform(_Recruiter())
	with pytest.raises(RuntimeError, match="拒绝"):
		adapter._checked_response(lambda: adapter._recruiter.greet_list())


# ── #1600：automation 整数字段拒绝小数 / 字符串 ──────────────────────


@pytest.mark.parametrize(
	"raw",
	[
		{"max_per_tab": 1.9},
		{"max_actions_per_run": 3.0},
		{"max_consecutive_errors": 0.5},
		{"max_per_tab": "3"},
	],
)
def test_automation_config_rejects_non_integer_numbers(raw: dict) -> None:
	with pytest.raises(ValueError, match="必须是整数"):
		automation_config_from_dict(raw)


def test_automation_config_accepts_integer_limits() -> None:
	config = automation_config_from_dict({"max_per_tab": 3, "max_actions_per_run": 0})
	assert config.max_per_tab == 3
	assert config.max_actions_per_run == 0


# ── #1601：wizard _positive_int 拒绝 bool / 小数 ─────────────────────


@pytest.mark.parametrize("value", [True, False, 2.9])
def test_positive_int_rejects_bool_and_non_integer_float(value: object) -> None:
	with pytest.raises(WorkflowActionError) as caught:
		_positive_int(value, name="pages")

	assert caught.value.code == "INVALID_PARAM"
	assert "pages" in str(caught.value)


def test_positive_int_accepts_integer_and_integer_string() -> None:
	assert _positive_int(3, name="pages") == 3
	assert _positive_int("3", name="pages") == 3


def test_wizard_crawl_rejects_boolean_pages_before_cache_write(tmp_path: Path) -> None:
	context = ActionContext(tmp_path, "zhipin", "candidate", Logger())
	from boss_agent_cli.wizard.actions import DEFAULT_ACTIONS

	with pytest.raises(WorkflowActionError) as caught:
		DEFAULT_ACTIONS["crawl_start"](context, {"query": "AI", "city": "北京", "pages": True}, {})

	assert caught.value.code == "INVALID_PARAM"
	db_path = tmp_path / "cache" / "boss_agent.db"
	if db_path.exists():
		with sqlite3.connect(db_path) as connection:
			assert connection.execute("SELECT COUNT(*) FROM crawl_runs").fetchone()[0] == 0
