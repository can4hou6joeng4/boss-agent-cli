import json
import os
import pty
import select
import shlex
import sqlite3
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import click
import pytest
from click.testing import CliRunner

from boss_agent_cli.ai.config import AIConfigStore
from boss_agent_cli.cache.store import CacheStore
from boss_agent_cli.crawler.service import CrawlOutcome
from boss_agent_cli.resume.models import ResumeData
from boss_agent_cli.resume.store import ResumeStore
from boss_agent_cli.display import risk_error_message
from boss_agent_cli.schema.error_codes import ERROR_CODES
from boss_agent_cli.wizard.models import StepResult, WorkflowInputError, WorkflowPlan, WorkflowStatus, WizardInput
from boss_agent_cli.wizard.catalog import GOALS, build_plan, catalog_data
from boss_agent_cli.wizard.actions import ActionContext, DEFAULT_ACTIONS, execute_candidate_search
from boss_agent_cli.output import Logger
from boss_agent_cli.main import cli
from boss_agent_cli.wizard.runner import WorkflowActionError, WorkflowRunner
from boss_agent_cli.wizard.store import WorkflowStore
from boss_agent_cli.wizard.prompts import (
	ClickMenu,
	MenuOption,
	ResilientMenu,
	WizardBack,
	WizardCancelled,
	WizardControl,
	WizardReturnHome,
	_collect_control,
	_main_menu_options,
	ask_continue_session,
	collect_entity_follow_up,
	collect_result_follow_up,
	collect_wizard_input,
)

# PTY 用例要真起一个 CLI 进程并等 prompt_toolkit 渲染完首屏，耗时随机器负载浮动：
# 本机约 1s，而 CI 的 P0 baseline job 在同一个 job 里串跑 ruff / pytest / mypy，
# 曾因 5s 硬期限超时假红并挡住 #380 的合并（同一 commit 上五个测试矩阵 job 全过）。
# 放宽期限不会拖慢快机器——命中同步标记或进程退出就立刻跳出循环。
_PTY_STARTUP_TIMEOUT = float(os.environ.get("BOSS_TEST_PTY_STARTUP_TIMEOUT", "30"))
_PTY_EXIT_TIMEOUT = float(os.environ.get("BOSS_TEST_PTY_EXIT_TIMEOUT", "20"))


class _ScriptedMenu:
	def __init__(self, selections, texts=()):
		self.selections = iter(selections)
		self.texts = iter(texts)
		self.menus = []
		self.last_kwargs = {}
		self.kwargs_log = []

	def select(self, title, options, *, default=None, allow_back=True, allow_exit=True, clear_before=True):
		self.last_kwargs = {
			"allow_back": allow_back,
			"allow_exit": allow_exit,
			"clear_before": clear_before,
		}
		self.kwargs_log.append(dict(self.last_kwargs))
		self.menus.append((title, list(options)))
		value = next(self.selections)
		if value == "返回":
			raise WizardBack
		assert value in {option.value for option in options}
		return value

	def text(self, label, *, default="", required=True):
		return next(self.texts, default)


def _plan(*steps: str) -> WorkflowPlan:
	return WorkflowPlan(
		role="candidate",
		platform="zhipin",
		goal="job_search",
		inputs={"query": "Python"},
		requested_steps=steps,
		mode="headless",
	)


def test_wizard_input_validates_headless_contract():
	parsed = WizardInput.from_mapping(
		{
			"role": "candidate",
			"platform": "zhipin",
			"goal": "job_search",
			"inputs": {"query": "Python"},
			"requested_steps": ["auth_status", "candidate_search"],
		}
	)
	assert parsed.requested_steps == ("auth_status", "candidate_search")
	with pytest.raises(WorkflowInputError):
		WizardInput.from_mapping({"role": "other", "platform": "zhipin", "goal": "x"})


def test_catalog_builds_candidate_and_recruiter_plans():
	catalog = catalog_data()
	assert "job_search" in catalog["roles"]["candidate"]["goals"]
	assert catalog["roles"]["recruiter"]["platforms"] == ["zhipin"]
	candidate = build_plan(
		WizardInput.from_mapping(
			{
				"role": "candidate",
				"platform": "zhipin",
				"goal": "job_search",
				"inputs": {"query": "Python"},
			}
		)
	)
	recruiter = build_plan(
		WizardInput.from_mapping(
			{
				"role": "recruiter",
				"platform": "zhipin",
				"goal": "jobs_list",
				"inputs": {},
			}
		)
	)
	assert candidate.requested_steps == ("auth_status", "candidate_search")
	assert recruiter.requested_steps == ("auth_status", "recruiter_jobs_list")


def test_catalog_rejects_platform_and_missing_inputs():
	with pytest.raises(WorkflowInputError, match="不支持平台"):
		build_plan(
			WizardInput.from_mapping(
				{
					"role": "candidate",
					"platform": "zhilian",
					"goal": "job_search",
					"inputs": {"query": "Python"},
				}
			)
		)
	with pytest.raises(WorkflowInputError, match="缺少 inputs"):
		build_plan(
			WizardInput.from_mapping(
				{
					"role": "candidate",
					"platform": "zhipin",
					"goal": "job_search",
					"inputs": {},
				}
			)
		)


def test_catalog_unknown_goal_lists_available_goals():
	"""未知 goal 的报错要带可选值，Agent 不必再调一次 boss schema 才能猜对。"""
	with pytest.raises(WorkflowInputError) as excinfo:
		build_plan(
			WizardInput.from_mapping(
				{"role": "candidate", "platform": "zhipin", "goal": "bogus", "inputs": {}}
			)
		)
	message = str(excinfo.value)
	assert "不支持 goal 'bogus'" in message
	assert "可选:" in message
	assert "job_search" in message
	assert "crawl_start" in message


def test_catalog_unknown_steps_list_valid_steps():
	"""requested_steps 写错时同时列出该 goal 的合法步骤。"""
	with pytest.raises(WorkflowInputError) as excinfo:
		build_plan(
			WizardInput.from_mapping(
				{
					"role": "candidate",
					"platform": "zhipin",
					"goal": "job_search",
					"inputs": {"query": "Python"},
					"requested_steps": ["bogus_step"],
				}
			)
		)
	message = str(excinfo.value)
	assert "bogus_step" in message
	assert "可选:" in message
	assert "candidate_search" in message


def test_catalog_goals_reference_exactly_the_registered_actions():
	catalog_steps = {step for goals in GOALS.values() for goal in goals.values() for step in goal.steps}
	assert catalog_steps == set(DEFAULT_ACTIONS)


def test_catalog_and_action_required_checks_accept_json_containers():
	plan = build_plan(
		WizardInput.from_mapping(
			{
				"role": "candidate",
				"platform": "zhipin",
				"goal": "job_search",
				"inputs": {"query": ["Python", "Golang"]},
			}
		)
	)
	assert plan.inputs["query"] == ["Python", "Golang"]


def test_candidate_search_helper_preserves_filters_welfare_and_pagination():
	captured = {}

	def pipeline(platform, cache, logger, **kwargs):
		captured.update(kwargs)
		return SimpleNamespace(items=[{"job_id": "job-1"}], has_more=True, has_more_reported=True, total=27)

	result = execute_candidate_search(
		object(),
		object(),
		Logger(),
		{
			"query": "Python",
			"city": "北京",
			"page": 2,
			"max_pages": 3,
			"welfare_conditions": ["双休", "五险一金"],
			"raw_params": {"degree": "本科"},
		},
		pipeline=pipeline,
	)
	assert result.total == 27
	assert captured["criteria"].query == "Python"
	assert captured["criteria"].city == "北京"
	assert captured["criteria"].raw_params == {"degree": "本科"}
	assert captured["start_page"] == 2
	assert captured["max_pages"] == 3
	assert captured["welfare_conditions"] == ["双休", "五险一金"]


def test_candidate_search_normalizes_welfare_input_and_uses_detail_page_budget():
	captured = {}

	def pipeline(platform, cache, logger, **kwargs):
		captured.update(kwargs)
		return SimpleNamespace(items=[], has_more=False, has_more_reported=False, total=0)

	execute_candidate_search(
		object(),
		object(),
		Logger(),
		{"query": "Python", "welfare": "双休,五险一金"},
		pipeline=pipeline,
	)

	assert captured["max_pages"] == 5
	assert [condition[0] for condition in captured["welfare_conditions"]] == ["双休", "五险一金"]
	assert "周末双休" in captured["welfare_conditions"][0][1]


def test_schema_exposes_wizard_catalog_for_headless_agents(tmp_path):
	result = CliRunner().invoke(cli, ["--json", "--data-dir", str(tmp_path), "schema"])
	payload = json.loads(result.stdout)
	assert result.exit_code == 0
	assert payload["data"]["wizard_catalog"] == catalog_data()
	assert "job_search" in payload["data"]["wizard_catalog"]["roles"]["candidate"]["goals"]
	assert "jobs_list" in payload["data"]["wizard_catalog"]["roles"]["recruiter"]["goals"]


def test_wizard_unknown_goal_envelope_lists_goals_and_schema_hint(tmp_path):
	"""非交互模式输入未知 goal：消息列出可选 goal，hints 给出可执行的 boss schema。"""
	result = CliRunner().invoke(
		cli,
		[
			"--json",
			"--data-dir",
			str(tmp_path),
			"wizard",
			"--input-json",
			json.dumps({"role": "candidate", "platform": "zhipin", "goal": "bogus"}),
		],
	)
	assert result.exit_code == 1
	payload = json.loads(result.stdout)
	assert payload["error"]["code"] == "INVALID_PARAM"
	assert "job_search" in payload["error"]["message"]
	assert payload["hints"]["next_actions"] == ["boss schema"]


def test_wizard_duplicate_requested_steps_returns_invalid_param_envelope(tmp_path):
	"""重复步骤会撞 workflow_steps 主键；必须在建 plan 时拒绝并给出信封（Issue #879）。"""
	result = CliRunner().invoke(
		cli,
		[
			"--json",
			"--data-dir",
			str(tmp_path),
			"wizard",
			"--input-json",
			json.dumps(
				{
					"role": "candidate",
					"platform": "zhipin",
					"goal": "shortlist",
					"inputs": {},
					"requested_steps": ["local_shortlist", "local_shortlist"],
					"mode": "headless",
				}
			),
		],
	)
	assert result.exit_code == 1
	payload = json.loads(result.stdout)
	assert set(payload) == {"ok", "schema_version", "command", "data", "pagination", "error", "hints"}
	assert payload["error"]["code"] == "INVALID_PARAM"
	assert "不能重复" in payload["error"]["message"]
	# 与「未知 goal」同一路径：输入本身不成立，需要改成新输入而不是原样重试。
	assert payload["error"]["recoverable"] is False
	assert payload["hints"]["next_actions"] == ["boss schema"]


class _FakeCandidatePlatform:
	def __init__(self):
		self.calls = []

	def __enter__(self):
		return self

	def __exit__(self, *args):
		return None

	def apply(self, security_id, job_id, lid=""):
		self.calls.append(("apply", security_id, job_id, lid))
		return {"code": 0, "data": {"applied": True}}

	def greet(self, security_id, job_id, message=""):
		self.calls.append(("greet", security_id, job_id, message))
		return {"code": 0, "data": {"greeted": True}}

	def friend_list(self, *, page=1):
		self.calls.append(("friend_list", page))
		return {
			"code": 0,
			"data": {
				"result": [{
					"uid": 10001,
					"securityId": "sid_current",
					"name": "联系人甲",
					"friendSource": 0,
				}],
				"hasMore": False,
			},
		}

	def exchange_contact(self, security_id, uid, name, exchange_type=1):
		self.calls.append(("exchange_contact", security_id, uid, name, exchange_type))
		return {"code": 0, "data": {"requested": True}}

	def friend_label(self, uid, label_id, friend_source=0, remove=False):
		self.calls.append(("friend_label", uid, label_id, friend_source, remove))
		return {"code": 0, "data": {"updated": True}}

	def chat_history(self, gid, security_id, *, page=1, count=20):
		self.calls.append(("chat_history", gid, security_id, page, count))
		return {"code": 0, "data": {"messages": [{"text": "你好"}]}}

	def is_success(self, response):
		return response["code"] == 0

	def unwrap_data(self, response):
		return response["data"]

	def parse_error(self, response):
		return "ACCOUNT_RISK", "blocked"


def _action_context(tmp_path, *, role="candidate", candidate=None, recruiter=None):
	return ActionContext(
		data_dir=tmp_path,
		platform="zhipin",
		role=role,
		logger=Logger(),
		candidate_factory=(lambda name, auth, delay, cdp_url, browser_source=None: candidate if candidate is not None else None),
		recruiter_factory=(lambda name, auth, delay, cdp_url, browser_source=None: recruiter if recruiter is not None else None),
	)


def test_candidate_pipeline_action_aggregates_every_friend_list_page(tmp_path):
	"""向导 pipeline 只看第 1 页会静默漏掉后续页的沟通（Issue #881）。"""
	from boss_agent_cli.wizard.actions import _candidate_pipeline_data

	class PagedPlatform(_FakeCandidatePlatform):
		def friend_list(self, *, page=1):
			self.calls.append(("friend_list", page))
			page_one = [{"uid": 10001, "securityId": "sid_1", "name": "联系人甲", "friendSource": 0}]
			page_two = [{"uid": 10002, "securityId": "sid_2", "name": "联系人乙", "friendSource": 0}]
			return {
				"code": 0,
				"data": {"result": page_one if page == 1 else page_two, "hasMore": page == 1},
			}

		def interview_data(self):
			return {"code": 0, "data": {"interviewList": []}}

	platform = PagedPlatform()
	context = _action_context(tmp_path, candidate=platform)
	items, pages_scanned, truncated = _candidate_pipeline_data(context, {})

	assert [page for _, page in platform.calls if _ == "friend_list"] == [1, 2]
	assert {item["security_id"] for item in items} == {"sid_1", "sid_2"}
	assert pages_scanned == 2
	assert truncated is False


def test_candidate_write_actions_persist_idempotence_records(tmp_path):
	platform = _FakeCandidatePlatform()
	context = _action_context(tmp_path, candidate=platform)
	apply_inputs = {"security_id": "sec-1", "job_id": "job-1", "lid": "lid-1"}
	greet_inputs = {"security_id": "sec-2", "job_id": "job-2", "message": "你好"}
	assert DEFAULT_ACTIONS["candidate_apply"](context, apply_inputs, {}).data["result"]["applied"] is True
	assert DEFAULT_ACTIONS["candidate_greet"](context, greet_inputs, {}).data["result"]["greeted"] is True
	with pytest.raises(WorkflowActionError, match="已对该职位") as applied:
		DEFAULT_ACTIONS["candidate_apply"](context, apply_inputs, {})
	with pytest.raises(WorkflowActionError, match="打过招呼") as greeted:
		DEFAULT_ACTIONS["candidate_greet"](context, greet_inputs, {})
	assert applied.value.code == "ALREADY_APPLIED"
	assert greeted.value.code == "ALREADY_GREETED"
	assert platform.calls == [
		("apply", "sec-1", "job-1", "lid-1"),
		("greet", "sec-2", "job-2", "你好"),
	]


def test_candidate_write_actions_mark_local_state_failure_as_not_retryable(tmp_path, monkeypatch):
	"""平台确认成功、本地记账失败时不能回成可重试的 NETWORK_ERROR。"""
	platform = _FakeCandidatePlatform()
	context = _action_context(tmp_path, candidate=platform)

	def fail_record(*args, **kwargs):
		raise OSError("disk full")

	monkeypatch.setattr(CacheStore, "record_apply", fail_record)
	with pytest.raises(WorkflowActionError) as applied:
		DEFAULT_ACTIONS["candidate_apply"](context, {"security_id": "sec-1", "job_id": "job-1"}, {})
	assert applied.value.code == "LOCAL_STATE_WRITE_FAILED"
	assert applied.value.recoverable is False
	assert applied.value.replay_safe is False
	assert applied.value.recovery_action == ERROR_CODES["LOCAL_STATE_WRITE_FAILED"]["recovery_action"]
	assert applied.value.details == {
		"security_id": "sec-1",
		"job_id": "job-1",
		"applied": True,
		"recorded": False,
	}

	monkeypatch.setattr(CacheStore, "record_greet", fail_record)
	with pytest.raises(WorkflowActionError) as greeted:
		DEFAULT_ACTIONS["candidate_greet"](
			context, {"security_id": "sec-2", "job_id": "job-2", "message": "你好"}, {}
		)
	assert greeted.value.code == "LOCAL_STATE_WRITE_FAILED"
	assert greeted.value.recoverable is False
	assert greeted.value.replay_safe is False
	assert greeted.value.details == {
		"security_id": "sec-2",
		"job_id": "job-2",
		"greeted": True,
		"recorded": False,
	}

	# 平台动作各只发生一次，失败只来自本地记账。
	assert platform.calls == [
		("apply", "sec-1", "job-1", ""),
		("greet", "sec-2", "job-2", "你好"),
	]


def test_candidate_search_action_reports_unknown_has_more_as_null(tmp_path):
	"""向导 search 与顶层 `boss search` 同口径：平台没说下一页时 has_more 为 null（Issue #887）。"""
	platform = _FakeCandidatePlatform()
	context = _action_context(tmp_path, candidate=platform)

	class _Result:
		items = [{"job_id": "job-1"}]
		has_more = False
		has_more_reported = None
		total = 1

	with patch("boss_agent_cli.wizard.actions.execute_candidate_search", return_value=_Result()):
		result = DEFAULT_ACTIONS["candidate_search"](context, {"query": "golang"}, {})

	pagination = result.data["pagination"]
	assert pagination["has_more"] is None
	assert pagination["has_more_source"] == "unknown"
	assert pagination["total_kind"] == "returned_page_count"


def test_candidate_export_scopes_search_to_current_account(tmp_path):
	"""导出动作必须把账号指纹传给搜索管线，否则 greeted 标记会全部失真。"""
	platform = _FakeCandidatePlatform()
	context = _action_context(tmp_path, candidate=platform)
	captured: dict = {}

	class _Result:
		items: list = []

	def fake_search(platform_arg, cache, logger, inputs):
		captured.update(inputs)
		return _Result()

	with (
		patch("boss_agent_cli.wizard.actions.account_scope", return_value="acct-1"),
		patch("boss_agent_cli.wizard.actions.execute_candidate_search", side_effect=fake_search),
	):
		result = DEFAULT_ACTIONS["candidate_export"](
			context, {"query": "golang", "output": str(tmp_path / "out.json")}, {}
		)

	assert captured["account"] == "acct-1"
	assert result.data["count"] == 0


def test_candidate_export_expands_tilde_in_output_path(tmp_path, monkeypatch):
	"""wizard 的 output 来自 JSON 输入（MCP），`~` 不会经 shell 展开。"""
	platform = _FakeCandidatePlatform()
	context = _action_context(tmp_path, candidate=platform)
	monkeypatch.setenv("HOME", str(tmp_path))
	monkeypatch.setenv("USERPROFILE", str(tmp_path))

	class _Result:
		items: list = []

	with patch("boss_agent_cli.wizard.actions.execute_candidate_search", return_value=_Result()):
		result = DEFAULT_ACTIONS["candidate_export"](
			context, {"query": "golang", "output": "~/wizard/out.json"}, {}
		)

	expected = tmp_path / "wizard" / "out.json"
	assert result.data["path"] == str(expected)
	assert expected.exists()


def test_candidate_contact_actions_refresh_rotating_security_id(tmp_path):
	platform = _FakeCandidatePlatform()
	context = _action_context(tmp_path, candidate=platform)

	exchanged = DEFAULT_ACTIONS["candidate_exchange"](
		context, {"security_id": "10001", "type": "wechat"}, {}
	)
	marked = DEFAULT_ACTIONS["candidate_mark"](
		context, {"security_id": "10001", "label": "沟通中"}, {}
	)
	history = DEFAULT_ACTIONS["candidate_chat_history"](
		context, {"gid": "10001"}, {}
	)

	assert exchanged.data["uid"] == "10001"
	assert exchanged.data["security_id"] == "sid_current"
	assert marked.data["uid"] == "10001"
	assert history.data["uid"] == "10001"
	assert history.data["security_id"] == "sid_current"
	assert platform.calls == [
		("friend_list", 1),
		("exchange_contact", "sid_current", "10001", "联系人甲", 2),
		("friend_list", 1),
		("friend_label", "10001", 2, 0, False),
		("friend_list", 1),
		("chat_history", "10001", "sid_current", 1, 20),
	]


@pytest.mark.parametrize("value", ["wecaht", "PHONE", "phone ", "wx", 2])
def test_candidate_exchange_rejects_unknown_type(tmp_path, value):
	"""非法交换类型不能静默按手机号发出请求（Issue #905）。"""
	platform = _FakeCandidatePlatform()
	context = _action_context(tmp_path, candidate=platform)

	with pytest.raises(WorkflowActionError) as exc_info:
		DEFAULT_ACTIONS["candidate_exchange"](
			context, {"security_id": "10001", "type": value}, {}
		)

	assert exc_info.value.code == "INVALID_PARAM"
	assert "wechat" in str(exc_info.value)
	assert platform.calls == []


def test_local_and_ai_actions_return_structured_recovery(tmp_path):
	context = _action_context(tmp_path)
	with CacheStore(tmp_path / "cache" / "boss_agent.db") as cache:
		cache.add_shortlist({"security_id": "sec-1", "job_id": "job-1", "title": "Backend"})
	shortlist = DEFAULT_ACTIONS["local_shortlist"](context, {}, {})
	assert shortlist.data["total"] == 1
	assert shortlist.data["items"][0]["title"] == "Backend"

	added = DEFAULT_ACTIONS["candidate_watch"](
		context,
		{"action": "add", "name": "daily", "query": "Python", "welfare": ["双休", "五险一金"]},
		{},
	)
	# 存储格式与 CLI `watch add --welfare` 一致：逗号分隔字符串。写成数组会让
	# 之后 `boss watch run` 在 `.split(",")` 上抛 AttributeError。
	assert added.data["params"]["welfare"] == "双休,五险一金"
	listed = DEFAULT_ACTIONS["candidate_watch"](context, {"action": "list"}, {})
	assert listed.data["items"][0]["name"] == "daily"

	ResumeStore(tmp_path / "resumes").save(ResumeData(name="default", title="后端工程师"))
	with pytest.raises(WorkflowActionError) as exc_info:
		DEFAULT_ACTIONS["ai_assist"](context, {"resume": "default", "prompt": "优化"}, {})
	assert exc_info.value.code == "AI_NOT_CONFIGURED"
	assert exc_info.value.recoverable is True


def test_candidate_watch_stores_string_params(tmp_path):
	"""wizard 写入的 watch 参数必须是字符串，坏类型在写入时就拒绝。"""
	context = _action_context(tmp_path)
	DEFAULT_ACTIONS["candidate_watch"](
		context, {"action": "add", "name": "daily", "query": "Python", "welfare": ["双休"]}, {}
	)
	with CacheStore(tmp_path / "cache" / "boss_agent.db") as cache:
		record = cache.get_saved_search("daily")
	assert record is not None
	assert record["params"]["welfare"] == "双休"
	assert all(isinstance(v, (str, type(None))) for v in record["params"].values())

	with pytest.raises(WorkflowActionError) as exc_info:
		DEFAULT_ACTIONS["candidate_watch"](
			context, {"action": "add", "name": "bad", "query": ["Python"]}, {}
		)
	assert exc_info.value.code == "INVALID_PARAM"
	assert "query" in str(exc_info.value)


def test_candidate_watch_corrupt_params_reports_recovery(tmp_path):
	"""wizard 复用本地 watch 时参数写坏要给删除重建指引，而不是 NETWORK_ERROR。"""
	context = _action_context(tmp_path)
	DEFAULT_ACTIONS["candidate_watch"](context, {"action": "add", "name": "daily", "query": "Python"}, {})
	conn = sqlite3.connect(tmp_path / "cache" / "boss_agent.db")
	conn.execute("UPDATE saved_searches SET params = ? WHERE name = ?", ("{not json", "daily"))
	conn.commit()
	conn.close()

	listed = DEFAULT_ACTIONS["candidate_watch"](context, {"action": "list"}, {})
	assert listed.data["total"] == 0
	assert listed.data["corrupt"] == ["daily"]

	with pytest.raises(WorkflowActionError) as exc_info:
		DEFAULT_ACTIONS["candidate_watch"](context, {"action": "run", "name": "daily"}, {})
	assert exc_info.value.code == "SAVED_SEARCH_INVALID"
	assert "boss watch remove daily" in exc_info.value.recovery_action


def test_candidate_watch_corrupt_params_quotes_name_with_spaces(tmp_path):
	"""watch 名是 wizard 的自由文本输入：恢复指引里的名字带空格时必须整体加引号。"""
	context = _action_context(tmp_path)
	DEFAULT_ACTIONS["candidate_watch"](
		context, {"action": "add", "name": "我的 监控", "query": "Python"}, {}
	)
	conn = sqlite3.connect(tmp_path / "cache" / "boss_agent.db")
	conn.execute("UPDATE saved_searches SET params = ? WHERE name = ?", ("{not json", "我的 监控"))
	conn.commit()
	conn.close()

	with pytest.raises(WorkflowActionError) as exc_info:
		DEFAULT_ACTIONS["candidate_watch"](context, {"action": "run", "name": "我的 监控"}, {})
	assert exc_info.value.code == "SAVED_SEARCH_INVALID"
	assert "boss watch remove '我的 监控'" in exc_info.value.recovery_action
	assert "boss watch add '我的 监控' <关键词>" in exc_info.value.recovery_action


def _crawl_outcome(run_id, status="completed"):
	return CrawlOutcome(
		run_id=run_id,
		status=status,
		next_page=2,
		pages_completed=1,
		jobs_seen=3,
		detail_checks=0,
		output_paths={"json": f"/tmp/{run_id}.json"},
		hooks=(),
	)


class _FakeCrawlService:
	def __init__(self):
		self.created_settings = None
		self.resumed = []

	def create_and_run(self, settings):
		self.created_settings = settings
		return _crawl_outcome("crawl-created")

	def resume(self, run_id, **kwargs):
		self.resumed.append((run_id, kwargs))
		return _crawl_outcome(run_id)


def test_crawl_actions_use_fixture_service_and_persist_stop(tmp_path, monkeypatch):
	service = _FakeCrawlService()
	monkeypatch.setattr("boss_agent_cli.wizard.actions._crawl_service", lambda context, cache: service)
	context = _action_context(tmp_path)
	started = DEFAULT_ACTIONS["crawl_start"](
		context,
		{"query": "Python", "city": "北京", "pages": 2, "with_detail": True},
		{},
	)
	assert started.data["run_id"] == "crawl-created"
	assert service.created_settings.query == "Python"
	assert service.created_settings.pages == 2
	assert service.created_settings.with_detail is True

	with CacheStore(tmp_path / "cache" / "boss_agent.db") as cache:
		cache.create_crawl_run(
			"crawl-existing",
			{"query": "Python", "city_code": "101010100"},
			str(tmp_path / "crawl" / "runs" / "crawl-existing"),
		)
	status = DEFAULT_ACTIONS["crawl_status"](context, {"run_id": "crawl-existing"}, {})
	assert status.data["run_id"] == "crawl-existing"
	assert shlex.split(status.data["checkpoint"]["resume_command"]) == [
		"boss", "--data-dir", str(tmp_path.resolve()), "crawl", "resume", "crawl-existing",
	]
	resumed = DEFAULT_ACTIONS["crawl_resume"](
		context,
		{"run_id": "crawl-existing", "pages": 4, "with_detail": True},
		{},
	)
	assert resumed.data["status"] == "completed"
	assert service.resumed == [
		("crawl-existing", {"pages": 4, "with_detail": True, "clear_stop": True}),
	]
	stopped = DEFAULT_ACTIONS["crawl_stop"](context, {"run_id": "crawl-existing"}, {})
	assert stopped.data == {"run_id": "crawl-existing", "status": "stop_requested"}
	with CacheStore(tmp_path / "cache" / "boss_agent.db") as cache:
		assert cache.get_crawl_run("crawl-existing")["stop_requested"] is True


def test_crawl_action_honors_configured_zero_max_retries(tmp_path, monkeypatch):
	service = _FakeCrawlService()
	monkeypatch.setattr("boss_agent_cli.wizard.actions._crawl_service", lambda context, cache: service)
	context = replace(
		_action_context(tmp_path),
		config={"crawl": {"max_retries": 0, "cdp_port": 9333, "chrome_path": "/opt/chrome"}},
	)
	DEFAULT_ACTIONS["crawl_start"](context, {"query": "Python", "city": "北京"}, {})
	settings = service.created_settings
	assert settings is not None
	assert settings.max_retries == 0
	assert settings.cdp_port == 9333
	assert settings.chrome_path == "/opt/chrome"


@pytest.mark.parametrize(
	("crawl_config", "field"),
	[
		({"max_requests": [1]}, "max_requests"),
		({"max_requests": 0}, "max_requests"),
		({"max_retries": -1}, "max_retries"),
		({"cdp_port": "abc"}, "cdp_port"),
		({"cdp_port": 70000}, "cdp_port"),
		({"chrome_path": 5}, "chrome_path"),
	],
)
def test_crawl_action_rejects_invalid_config(tmp_path, monkeypatch, crawl_config, field):
	service = _FakeCrawlService()
	monkeypatch.setattr("boss_agent_cli.wizard.actions._crawl_service", lambda context, cache: service)
	context = replace(_action_context(tmp_path), config={"crawl": crawl_config})
	with pytest.raises(WorkflowActionError) as excinfo:
		DEFAULT_ACTIONS["crawl_start"](context, {"query": "Python", "city": "北京"}, {})
	assert excinfo.value.code == "INVALID_PARAM"
	assert field in str(excinfo.value)
	assert excinfo.value.recovery_action == "boss config reset crawl"
	assert service.created_settings is None


@pytest.mark.parametrize("pages", ["abc", 0])
def test_crawl_action_rejects_invalid_pages(tmp_path, pages):
	context = replace(_action_context(tmp_path), config={})
	with pytest.raises(WorkflowActionError) as excinfo:
		DEFAULT_ACTIONS["crawl_start"](context, {"query": "Python", "city": "北京", "pages": pages}, {})
	assert excinfo.value.code == "INVALID_PARAM"
	assert "pages" in str(excinfo.value)


@pytest.mark.parametrize("hook_profile", ["screenshot-full", "weird"])
def test_crawl_action_rejects_unsupported_hook_profile(tmp_path, monkeypatch, hook_profile):
	service = _FakeCrawlService()
	monkeypatch.setattr("boss_agent_cli.wizard.actions._crawl_service", lambda context, cache: service)
	context = _action_context(tmp_path)
	with pytest.raises(WorkflowActionError) as excinfo:
		DEFAULT_ACTIONS["crawl_start"](
			context,
			{"query": "Python", "city": "北京", "hook_profile": hook_profile},
			{},
		)
	assert excinfo.value.code == "INVALID_PARAM"
	assert "hook_profile" in str(excinfo.value)
	assert "boss crawl run" in str(excinfo.value.recovery_action)
	assert service.created_settings is None


@pytest.mark.parametrize("pages", ["abc", 0])
def test_crawl_resume_rejects_invalid_pages(tmp_path, monkeypatch, pages):
	service = _FakeCrawlService()
	monkeypatch.setattr("boss_agent_cli.wizard.actions._crawl_service", lambda context, cache: service)
	context = _action_context(tmp_path)
	with pytest.raises(WorkflowActionError) as excinfo:
		DEFAULT_ACTIONS["crawl_resume"](context, {"run_id": "crawl-existing", "pages": pages}, {})
	assert excinfo.value.code == "INVALID_PARAM"
	assert "pages" in str(excinfo.value)
	assert service.resumed == []


class _FakeRecruiterPlatform:
	def __init__(self, *, fail=False):
		self.fail = fail
		self.calls = []

	def __enter__(self):
		return self

	def __exit__(self, *args):
		return None

	def list_jobs(self):
		self.calls.append(("list_jobs",))
		return self._response({"jobs": [{"id": "job-1"}]})

	def search_geeks(self, query, **kwargs):
		self.calls.append(("search_geeks", query, kwargs))
		return self._response({"items": [{"geek_id": "geek-1"}], "hasMore": True})

	def send_message_by_friend(self, friend_id, message):
		self.calls.append(("reply", friend_id, message))
		return self._response({"sent": True})

	def chat_history(self, friend_id, *, count=20, max_msg_id=None):
		self.calls.append(("chat_history", friend_id, count, max_msg_id))
		return self._response({"messages": [{"id": max_msg_id or 1}]})

	def friend_list(self, *, page=1, label_id=0, job_id=None):
		self.calls.append(("friend_list", page, label_id, job_id))
		return self._response({"friendList": [{"friendId": 42}, {"friend_id": "43"}]})

	def last_messages(self, friend_ids):
		self.calls.append(("last_messages", friend_ids))
		return self._response({"messages": [{"friendId": friend_ids[0]}]})

	def exchange_request_by_friend(self, friend_id, exchange_type):
		self.calls.append(("request_resume", friend_id, exchange_type))
		return self._response({"requested": True})

	def job_detail(self, job_id):
		self.calls.append(("job_detail", job_id))
		return self._response({"job_id": job_id})

	def job_online(self, job_id):
		self.calls.append(("job_online", job_id))
		return self._response({"online": True})

	def job_offline(self, job_id):
		self.calls.append(("job_offline", job_id))
		return self._response({"offline": True})

	def _response(self, data):
		return {"code": 37 if self.fail else 0, "data": data}

	def is_success(self, response):
		return response["code"] == 0

	def unwrap_data(self, response):
		return response["data"]

	def parse_error(self, response):
		return "ACCOUNT_RISK", "risk blocked"


def test_recruiter_action_uses_injected_platform_and_returns_structured_data(tmp_path):
	platform = _FakeRecruiterPlatform()
	context = _action_context(tmp_path, role="recruiter", recruiter=platform)
	result = DEFAULT_ACTIONS["recruiter_jobs_list"](context, {}, {})
	assert result.data["result"]["jobs"][0]["id"] == "job-1"


def test_recruiter_jobs_list_recognizes_management_data_rows(tmp_path):
	class ManagementJobs(_FakeRecruiterPlatform):
		def list_jobs(self, page: int = 1):
			self.calls.append(("list_jobs", page))
			return self._response(
				{
					"page": page,
					"hasMore": False,
					"totalSize": 1,
					"data": [{"encryptJobId": "j1", "jobName": "role"}],
				}
			)

	platform = ManagementJobs()
	context = _action_context(tmp_path, role="recruiter", recruiter=platform)
	result = DEFAULT_ACTIONS["recruiter_jobs_list"](context, {}, {})
	assert result.data["items"] == [{"encryptJobId": "j1", "jobName": "role"}]
	assert result.data["result"]["data"][0]["encryptJobId"] == "j1"
	assert result.data["result"]["hasMore"] is False
	assert result.data["result"]["totalSize"] == 1
	assert result.data["result"]["page"] == 1
	assert platform.calls == [("list_jobs", 1)]


def test_recruiter_read_write_and_job_actions_preserve_arguments(tmp_path):
	platform = _FakeRecruiterPlatform()
	context = _action_context(tmp_path, role="recruiter", recruiter=platform)
	candidates = DEFAULT_ACTIONS["recruiter_candidates"](
		context,
		{"query": "Python", "city": "101010100", "page": 2, "select": True},
		{},
	)
	assert candidates.data["result"]["items"][0]["geek_id"] == "geek-1"
	assert (
		DEFAULT_ACTIONS["recruiter_reply"](
			context,
			{"friend_id": 42, "message": "请发简历"},
			{},
		).data["result"]["sent"]
		is True
	)
	assert (
		DEFAULT_ACTIONS["recruiter_chat_history"](
			context,
			{"friend_id": 42, "count": 10, "max_msg_id": 99},
			{},
		).data["result"]["messages"][0]["id"]
		== 99
	)
	assert DEFAULT_ACTIONS["recruiter_last_messages"](
		context,
		{"page": 2, "label_id": 1, "job_id": "job-1"},
		{},
	).data["friend_ids"] == [42, 43]
	assert (
		DEFAULT_ACTIONS["recruiter_exchange_contact"](
			context,
			{"friend_id": 42, "type": "wechat"},
			{},
		).data["result"]["requested"]
		is True
	)
	assert (
		DEFAULT_ACTIONS["recruiter_request_resume"](
			context,
			{"friend_id": 42},
			{},
		).data["result"]["requested"]
		is True
	)
	assert (
		DEFAULT_ACTIONS["recruiter_jobs_detail"](
			context,
			{"job_id": "job-1"},
			{},
		).data["result"]["job_id"]
		== "job-1"
	)
	assert (
		DEFAULT_ACTIONS["recruiter_jobs_online"](
			context,
			{"job_id": "job-1"},
			{},
		).data["result"]["online"]
		is True
	)
	assert (
		DEFAULT_ACTIONS["recruiter_jobs_offline"](
			context,
			{"job_id": "job-1"},
			{},
		).data["result"]["offline"]
		is True
	)
	assert platform.calls == [
		(
			"search_geeks",
			"Python",
			{
				"city": "101010100",
				"page": 2,
				"job_id": None,
				"experience": None,
				"degree": None,
				"age": None,
				"school_level": None,
				"activeness": None,
				"source": None,
				"select": True,
				"salary": None,
			},
		),
		("reply", 42, "请发简历"),
		("chat_history", 42, 10, 99),
		("friend_list", 2, 1, "job-1"),
		("last_messages", [42, 43]),
		("request_resume", 42, 2),
		("request_resume", 42, 4),
		("job_detail", "job-1"),
		("job_online", "job-1"),
		("job_offline", "job-1"),
	]


def test_recruiter_exchange_rejects_unknown_type(tmp_path):
	"""招聘者侧非法交换类型同样不能静默按手机号发出请求（Issue #905）。"""
	platform = _FakeRecruiterPlatform()
	context = _action_context(tmp_path, role="recruiter", recruiter=platform)

	with pytest.raises(WorkflowActionError) as exc_info:
		DEFAULT_ACTIONS["recruiter_exchange_contact"](
			context, {"friend_id": 42, "type": "wecaht"}, {}
		)

	assert exc_info.value.code == "INVALID_PARAM"
	assert "wechat" in str(exc_info.value)
	assert platform.calls == []


def test_platform_error_and_not_supported_are_not_reported_as_success(tmp_path):
	failing = _FakeRecruiterPlatform(fail=True)
	context = _action_context(tmp_path, role="recruiter", recruiter=failing)
	with pytest.raises(WorkflowActionError) as risk:
		DEFAULT_ACTIONS["recruiter_jobs_list"](context, {}, {})
	assert risk.value.code == "ACCOUNT_RISK"
	assert risk.value.recoverable is False

	class Unsupported(_FakeRecruiterPlatform):
		def list_jobs(self):
			raise NotImplementedError("fixture platform does not implement jobs")

	plan = WorkflowPlan(
		role="recruiter",
		platform="zhipin",
		goal="jobs_list",
		inputs={},
		requested_steps=("recruiter_jobs_list",),
		mode="headless",
	)
	with WorkflowStore(tmp_path) as store:
		run = WorkflowRunner(store, DEFAULT_ACTIONS).run(
			plan,
			_action_context(tmp_path, role="recruiter", recruiter=Unsupported()),
			run_id="unsupported-run",
		)
	assert run["status"] == "failed"
	assert run["error"]["code"] == "NOT_SUPPORTED"


def test_runner_normalizes_auth_and_network_failures(tmp_path):
	auth_plan = WorkflowPlan(
		role="candidate",
		platform="zhipin",
		goal="recommendations",
		inputs={},
		requested_steps=("auth_status",),
		mode="headless",
	)
	with WorkflowStore(tmp_path) as store:
		auth_run = WorkflowRunner(store, DEFAULT_ACTIONS).run(
			auth_plan,
			_action_context(tmp_path),
			run_id="auth-required-run",
		)
	# 未登录不是失败，是「在等人」：run 挂起可恢复，而不是被打成 failed。
	assert auth_run["status"] == "waiting_input"
	assert auth_run["error"] is None
	last = auth_run["last_result"]
	assert last["status"] == "waiting_input"
	assert last["data"]["authenticated"] is False
	assert shlex.split(last["next_action"]) == ["boss", "--data-dir", str(tmp_path.resolve()), "wizard", "--resume", "auth-required-run"]
	login_action = f"boss --data-dir {shlex.quote(str(tmp_path.resolve()))} login"
	assert any(login_action in action for action in last["operator_actions"])


def test_runner_auth_wait_quotes_run_id_in_resume_next_action(tmp_path):
	"""等待登录时给出的 --resume next_action 同样要转义 run_id。"""
	auth_plan = WorkflowPlan(
		role="candidate",
		platform="zhipin",
		goal="recommendations",
		inputs={},
		requested_steps=("auth_status",),
		mode="headless",
	)
	with WorkflowStore(tmp_path) as store:
		run = WorkflowRunner(store, DEFAULT_ACTIONS).run(
			auth_plan,
			_action_context(tmp_path),
			run_id="auth; echo pwn",
		)
	assert run["status"] == "waiting_input"
	assert shlex.split(run["last_result"]["next_action"]) == [
		"boss", "--data-dir", str(tmp_path.resolve()), "wizard", "--resume", "auth; echo pwn",
	]

	def broken(context, inputs, prior):
		raise OSError("offline")

	with WorkflowStore(tmp_path) as store:
		network_run = WorkflowRunner(store, {"broken": broken}).run(
			_plan("broken"),
			object(),
			run_id="network-run",
		)
	assert network_run["error"] == {
		"code": "NETWORK_ERROR",
		"message": "offline",
		"recoverable": True,
		"recovery_action": "重试当前 run_id",
	}


def test_runner_reports_invalid_input_as_invalid_param(tmp_path):
	"""inputs 类型写错时不能兜成 NETWORK_ERROR：重试同一条输入必然复现。"""

	def bad_input(context, inputs, prior):
		raise WorkflowInputError("wizard 输入 page 必须是整数，收到 'abc'")

	with WorkflowStore(tmp_path) as store:
		run = WorkflowRunner(store, {"bad_input": bad_input}).run(
			_plan("bad_input"),
			object(),
			run_id="invalid-input-run",
		)
	assert run["status"] == "failed"
	assert run["error"]["code"] == "INVALID_PARAM"
	assert "page" in run["error"]["message"]


def test_recruiter_search_rejects_non_integer_page_input():
	"""`--input-json` 把 page 写成字符串时，错误要指向 inputs 而不是被当成平台故障。"""
	from boss_agent_cli.wizard.actions import execute_recruiter_candidate_search

	class _Platform:
		def search_geeks(self, *args, **kwargs):
			raise AssertionError("输入校验失败时不应调用平台")

	with pytest.raises(WorkflowInputError, match="page"):
		execute_recruiter_candidate_search(_Platform(), {"query": "Python", "page": "abc"})


@pytest.mark.parametrize("page", [0, -1])
def test_recruiter_search_rejects_non_positive_page_input(page):
	"""页码 0 / 负数会被原样发给平台；这类输入重试多少次都是同一个结果。"""
	from boss_agent_cli.wizard.actions import execute_recruiter_candidate_search

	class _Platform:
		def search_geeks(self, *args, **kwargs):
			raise AssertionError("输入校验失败时不应调用平台")

	with pytest.raises(WorkflowInputError, match="page"):
		execute_recruiter_candidate_search(_Platform(), {"query": "Python", "page": page})


@pytest.mark.parametrize("max_pages", [0, -3])
def test_candidate_search_rejects_non_positive_max_pages(max_pages):
	"""`max_pages=-3` 会让 `range(max_pages)` 一次都不循环：workflow 以成功收尾但结果为空。"""
	from boss_agent_cli.wizard.actions import execute_candidate_search

	def _pipeline(*args, **kwargs):
		raise AssertionError("输入校验失败时不应发起搜索请求")

	with pytest.raises(WorkflowInputError, match="max_pages"):
		execute_candidate_search(
			object(),
			None,  # type: ignore[arg-type]
			object(),  # type: ignore[arg-type]
			{"query": "Python", "max_pages": max_pages},
			pipeline=_pipeline,
		)


def test_int_input_does_not_turn_explicit_zero_into_default():
	"""显式写的 0 不能被当成缺省悄悄换成默认值（`0 or default` 会让 count: 0 变成 20 条）。"""
	from boss_agent_cli.wizard.actions import _int_input

	assert _int_input({"days_stale": 0}, "days_stale", 3) == 0
	assert _int_input({}, "days_stale", 3) == 3
	with pytest.raises(WorkflowInputError, match="count"):
		_int_input({"count": 0}, "count", 20, minimum=1)


@pytest.mark.parametrize("value", ["abc", True, 2.5])
def test_int_input_rejects_non_integer_values(value):
	from boss_agent_cli.wizard.actions import _int_input

	with pytest.raises(WorkflowInputError, match="page"):
		_int_input({"page": value}, "page", 1, minimum=1)


def test_wizard_models_import_on_python_310_compatible_surface():
	result = subprocess.run(
		[
			sys.executable,
			"-c",
			"from boss_agent_cli.wizard.models import WorkflowStatus; print(WorkflowStatus.COMPLETED.value)",
		],
		capture_output=True,
		text=True,
		check=False,
	)
	assert result.returncode == 0
	assert result.stdout.strip() == "completed"


def test_cache_workflow_state_round_trip(tmp_path):
	with CacheStore(tmp_path / "cache" / "boss_agent.db") as cache:
		cache.create_workflow_run(
			"run-1",
			role="candidate",
			platform="zhipin",
			goal="job_search",
			mode="headless",
			params={"inputs": {"query": "Python"}},
			steps=("auth_status", "candidate_search"),
		)
		assert cache.update_workflow_step("run-1", "auth_status", status="completed", result={"authenticated": True})
		assert cache.update_workflow_run(
			"run-1", status="running", current_step="candidate_search", last_result={"authenticated": True}
		)
		run = cache.get_workflow_run("run-1")
		steps = cache.list_workflow_steps("run-1")

	assert run["params"]["inputs"]["query"] == "Python"
	assert run["last_result"] == {"authenticated": True}
	assert [step["step_name"] for step in steps] == ["auth_status", "candidate_search"]
	assert steps[0]["status"] == "completed"


def test_runner_persists_success_and_explicit_run_id(tmp_path):
	events = []

	def auth(context, inputs, prior):
		return StepResult({"authenticated": True})

	def search(context, inputs, prior):
		assert prior["auth_status"]["data"]["authenticated"] is True
		return StepResult({"items": [{"title": inputs["query"]}]}, artifacts=("result.json",))

	with WorkflowStore(tmp_path) as store:
		runner = WorkflowRunner(store, {"auth_status": auth, "candidate_search": search})
		run = runner.run(
			_plan("auth_status", "candidate_search"),
			object(),
			run_id="run-fixed",
			on_event=lambda kind, step, data: events.append((kind, step)),
		)

	assert run["run_id"] == "run-fixed"
	assert run["status"] == WorkflowStatus.COMPLETED.value
	assert [step["status"] for step in run["steps"]] == ["completed", "completed"]
	assert events[-1] == ("step_finished", "candidate_search")
	json.dumps(run, ensure_ascii=False)


def test_runner_persists_recoverable_failure_and_resumes_completed_steps(tmp_path):
	calls = []

	def done(context, inputs, prior):
		calls.append("done")
		return StepResult({"ok": True})

	def fail(context, inputs, prior):
		calls.append("fail")
		raise WorkflowActionError("RATE_LIMITED", "slow down", recoverable=True, recovery_action="retry")

	with WorkflowStore(tmp_path) as store:
		runner = WorkflowRunner(store, {"one": done, "two": fail})
		first = runner.run(_plan("one", "two"), object(), run_id="run-resume")
		assert first["status"] == "failed"
		assert first["error"]["code"] == "RATE_LIMITED"
		runner = WorkflowRunner(store, {"one": done, "two": done})
		second = runner.run(_plan("one", "two"), object(), run_id="run-resume")

	assert second["status"] == "completed"
	assert calls == ["done", "fail", "done"]


def test_runner_resume_does_not_replay_unconfirmed_write_step(tmp_path):
	calls = []

	def done(context, inputs, prior):
		calls.append("done")
		return StepResult({"ok": True})

	def unconfirmed(context, inputs, prior):
		calls.append("unconfirmed")
		raise WorkflowActionError(
			"ACTION_UNCONFIRMED",
			"发送结果未确认",
			recoverable=False,
			recovery_action="先核对平台实际记录；不要重试",
			replay_safe=False,
		)

	with WorkflowStore(tmp_path) as store:
		first = WorkflowRunner(store, {"one": done, "two": unconfirmed}).run(
			_plan("one", "two"), object(), run_id="run-unconfirmed"
		)
		assert first["status"] == "failed"
		assert first["steps"][1]["error"]["replay_safe"] is False
		second = WorkflowRunner(store, {"one": done, "two": done}).run(
			_plan("one", "two"), object(), run_id="run-unconfirmed"
		)

	assert second["status"] == "failed"
	assert second["error"]["code"] == "ACTION_UNCONFIRMED"
	assert second["error"]["recovery_action"] == "先核对平台实际记录；不要重试"
	assert calls == ["done", "unconfirmed"]


def test_runner_rejects_mismatched_plan_for_existing_run_id(tmp_path):
	with WorkflowStore(tmp_path) as store:
		runner = WorkflowRunner(store, {"one": lambda context, inputs, prior: StepResult({"ok": True})})
		original = _plan("one")
		assert runner.run(original, object(), run_id="run-bound")["status"] == "completed"
		mismatch = WorkflowPlan(
			role="recruiter",
			platform="zhipin",
			goal="jobs",
			inputs={},
			requested_steps=("one", "two"),
			mode="headless",
		)
		with pytest.raises(WorkflowActionError) as exc_info:
			runner.run(mismatch, object(), run_id="run-bound")
		persisted = store.get("run-bound")

	assert exc_info.value.code == "WORKFLOW_PLAN_MISMATCH"
	assert persisted["role"] == "candidate"
	assert [step["step_name"] for step in persisted["steps"]] == ["one"]


def test_runner_retries_only_recoverable_errors(tmp_path):
	attempts = 0

	def flaky(context, inputs, prior):
		nonlocal attempts
		attempts += 1
		if attempts == 1:
			raise WorkflowActionError("RATE_LIMITED", "retry", recoverable=True)
		return StepResult({"attempts": attempts})

	with WorkflowStore(tmp_path) as store:
		run = WorkflowRunner(store, {"flaky": flaky}).run(_plan("flaky"), object(), run_id="run-retry", max_retries=1)

	assert run["status"] == "completed"
	assert attempts == 2


def test_automatic_retry_reuses_newly_bound_inner_checkpoint(tmp_path):
	"""同一次运行中的自动重试必须读取刚保存的内部绑定，不能另建采集任务。"""
	seen_inner_ids = []
	created_inner_ids = []

	def crawl(context, inputs, prior):
		# 刷新当前步骤 checkpoint 时，已完成前置步骤的结果也必须保留。
		assert prior["auth_status"]["data"] == {"ok": True}
		inner_run_id = prior.get("crawl_start", {}).get("inner_run_id")
		seen_inner_ids.append(inner_run_id)
		if inner_run_id is None:
			inner_run_id = f"fixture-inner-{len(created_inner_ids) + 1}"
			created_inner_ids.append(inner_run_id)
			context.workflow_control.bind_inner_run(inner_run_id)
		if len(seen_inner_ids) == 1:
			raise WorkflowActionError("NETWORK_ERROR", "离线模拟采集中断", recoverable=True)
		return StepResult({"run_id": inner_run_id})

	plan = replace(
		_plan("auth_status", "crawl_start"), goal="crawl_start", inputs={"query": "Python", "city": "北京"},
	)
	with WorkflowStore(tmp_path) as store:
		run = WorkflowRunner(
			store,
			{"auth_status": lambda c, i, p: StepResult({"ok": True}), "crawl_start": crawl},
		).run(plan, _action_context(tmp_path), run_id="run-retry-bound", max_retries=1)

	assert run["status"] == "completed"
	assert seen_inner_ids == [None, "fixture-inner-1"]
	assert created_inner_ids == ["fixture-inner-1"]
	assert run["steps"][1]["result"]["data"]["run_id"] == "fixture-inner-1"


def test_runner_honors_persisted_stop_between_steps(tmp_path):
	with WorkflowStore(tmp_path) as store:
		runner = WorkflowRunner(
			store,
			{
				"one": lambda context, inputs, prior: StepResult({"done": True}),
				"two": lambda context, inputs, prior: StepResult({"should_not_run": True}),
			},
		)

		def stop_after_first(kind, step, data):
			if kind == "step_finished" and step == "one":
				assert store.request_stop("run-stop") is True

		run = runner.run(_plan("one", "two"), object(), run_id="run-stop", on_event=stop_after_first)

	assert run["status"] == "stopped"
	assert run["steps"][0]["status"] == "completed"
	assert run["steps"][1]["status"] == "pending"


def test_workflow_stop_forwards_to_bound_crawl_run(tmp_path):
	plan = WorkflowPlan(
		role="candidate",
		platform="zhipin",
		goal="crawl_start",
		inputs={"query": "Python", "city": "北京"},
		requested_steps=("crawl_start",),
		mode="headless",
	)
	with WorkflowStore(tmp_path) as store:
		store.create("run-crawl-stop", plan)
		store.update_run("run-crawl-stop", status="running", current_step="crawl_start")
		store.update_step(
			"run-crawl-stop",
			"crawl_start",
			status="running",
			result={"inner_run_id": "inner-crawl"},
		)
		store._cache.create_crawl_run(
			"inner-crawl",
			{"query": "Python"},
			str(tmp_path / "crawl" / "runs" / "inner-crawl"),
		)

		assert store.request_stop("run-crawl-stop") is True
		run = store.get("run-crawl-stop")
		inner = store._cache.get_crawl_run("inner-crawl")

	assert run["status"] == "stopped"
	assert inner["stop_requested"] is True


def test_resume_after_timeout_forwards_bound_inner_checkpoint_to_action(tmp_path):
	"""超时后恢复：failed 步骤的 inner_run_id 必须传给 action，且不能被重跑清空（Issue #882）。"""
	priors = []
	store_seen = []

	with WorkflowStore(tmp_path) as store:

		def flaky(context, inputs, prior):
			store.update_step(
				"run-resume-bound", "crawl_start", status="running", result={"inner_run_id": "crw_inner"}
			)
			raise WorkflowActionError(
				"WORKFLOW_TIMEOUT",
				"workflow 超过设定时限",
				recoverable=True,
				recovery_action="boss crawl resume crw_inner",
			)

		runner = WorkflowRunner(store, {"crawl_start": flaky})
		plan = WorkflowPlan(
			role="candidate",
			platform="zhipin",
			goal="crawl_start",
			inputs={"query": "Python", "city": "北京"},
			requested_steps=("crawl_start",),
			mode="headless",
		)
		first = runner.run(plan, object(), run_id="run-resume-bound")
		assert first["error"]["code"] == "WORKFLOW_TIMEOUT"
		assert first["steps"][0]["result"] == {"inner_run_id": "crw_inner"}

		def recovered(context, inputs, prior):
			priors.append(prior)
			step = next(s for s in store.get("run-resume-bound")["steps"] if s["step_name"] == "crawl_start")
			store_seen.append(step["result"])
			return StepResult({"ok": True})

		runner._actions["crawl_start"] = recovered
		second = runner.run(plan, object(), run_id="run-resume-bound")
	assert second["status"] == "completed"
	assert priors[0]["crawl_start"] == {"inner_run_id": "crw_inner"}
	# 重跑开始时不得清空已写入的绑定，否则再次中断就永久丢失内部任务。
	assert store_seen[0] == {"inner_run_id": "crw_inner"}


def test_deadline_before_step_keeps_bound_inner_checkpoint(tmp_path):
	"""恢复时 deadline 已过：_fail_controlled 也不能清掉上次绑定（Issue #882）。"""
	plan = WorkflowPlan(
		role="candidate",
		platform="zhipin",
		goal="crawl_start",
		inputs={"query": "Python", "city": "北京"},
		requested_steps=("crawl_start",),
		mode="headless",
	)
	with WorkflowStore(tmp_path) as store:
		store.create("run-deadline-bind", plan)
		store.update_run("run-deadline-bind", status="running", current_step="crawl_start")
		store.update_step("run-deadline-bind", "crawl_start", status="running", result={"inner_run_id": "crw_inner"})
		runner = WorkflowRunner(store, {"crawl_start": lambda c, i, p: StepResult({"ok": True})})
		run = runner.run(plan, object(), run_id="run-deadline-bind", timeout_seconds=0)
	assert run["error"]["code"] == "WORKFLOW_TIMEOUT"
	assert run["steps"][0]["result"] == {"inner_run_id": "crw_inner"}


def test_runner_records_timeout_at_action_boundary(tmp_path):
	def slow(context, inputs, prior):
		time.sleep(0.01)
		return StepResult({"done": True})

	with WorkflowStore(tmp_path) as store:
		run = WorkflowRunner(store, {"slow": slow}).run(
			_plan("slow"), object(), run_id="run-timeout", timeout_seconds=0.001
		)

	assert run["status"] == "failed"
	assert run["error"]["code"] == "WORKFLOW_TIMEOUT"
	assert run["error"]["recoverable"] is True


def test_runner_timeout_quotes_run_id_in_recovery_action(tmp_path):
	"""run_id 是本地自由文本，拼进可执行的 --resume 提示前必须按 shell 参数转义。"""
	def slow(context, inputs, prior):
		time.sleep(0.01)
		return StepResult({"done": True})

	with WorkflowStore(tmp_path) as store:
		run = WorkflowRunner(store, {"slow": slow}).run(
			_plan("slow"), object(), run_id="run; echo pwn", timeout_seconds=0.001
		)

	assert run["error"]["code"] == "WORKFLOW_TIMEOUT"
	assert run["error"]["recovery_action"] == "boss wizard --resume 'run; echo pwn'"


def _shortlist_input() -> str:
	return json.dumps(
		{
			"role": "candidate",
			"platform": "zhipin",
			"goal": "shortlist",
			"inputs": {},
		},
		ensure_ascii=False,
	)


def test_chinese_menu_groups_candidate_goals_without_exposing_internal_ids():
	menu = _ScriptedMenu(["new", "candidate", "zhipin", "找职位", "shortlist"])
	selection = collect_wizard_input(
		default_role="candidate",
		default_platform="zhipin",
		menu=menu,
	)

	assert isinstance(selection, WizardInput)
	assert selection.goal == "shortlist"
	titles = [title for title, _ in menu.menus]
	assert titles == [
		"请选择要进行的操作",
		"请选择使用身份",
		"请选择招聘平台",
		"请选择目标分类",
		"请选择本次要完成的事项",
	]
	goal_options = menu.menus[-1][1]
	assert len(goal_options) == 5
	displayed = " ".join(option.label for _, options in menu.menus for option in options)
	assert "我是求职者" in displayed
	assert "BOSS 直聘" in displayed
	assert "查看本地候选池" in displayed
	assert "candidate" not in displayed
	assert "job_search" not in displayed
	assert "local_shortlist" not in displayed


def test_chinese_menu_groups_recruiter_goals_and_collects_control_actions():
	menu = _ScriptedMenu(["new", "recruiter", "zhipin", "职位管理", "jobs_list"])
	selection = collect_wizard_input(
		default_role="candidate",
		default_platform="zhipin",
		menu=menu,
	)
	assert isinstance(selection, WizardInput)
	assert selection.role == "recruiter"
	assert len(menu.menus[-1][1]) == 4
	assert [option.label for option in menu.menus[-1][1]] == ["查看职位列表", "查看职位详情", "上线职位", "下线职位"]

	control_menu = _ScriptedMenu(["retry", "wrn_test"])
	control = collect_wizard_input(
		default_role="candidate",
		default_platform="zhipin",
		menu=control_menu,
		available_runs=[
			{
				"run_id": "wrn_test",
				"status": "failed",
				"role": "candidate",
				"platform": "zhipin",
				"goal": "job_search",
			}
		],
	)
	assert control == WizardControl(action="retry", run_id="wrn_test")


def test_control_menu_selects_recent_run_without_manual_id_and_localizes_metadata():
	menu = _ScriptedMenu(["resume", "wrn_recent"])
	control = collect_wizard_input(
		default_role="candidate",
		default_platform="zhipin",
		menu=menu,
		available_runs=[
			{
				"run_id": "wrn_recent",
				"role": "candidate",
				"platform": "zhipin",
				"goal": "job_search",
				"status": "failed",
			}
		],
	)

	assert control == WizardControl(action="resume", run_id="wrn_recent")
	assert menu.menus[-1][0] == "请选择任务"
	displayed = " ".join(
		part
		for title, options in menu.menus
		for part in (title, *(option.label + " " + option.description for option in options))
	)
	assert "搜索和筛选职位" in displayed
	assert "执行失败" in displayed
	assert "编号 wrn_recent" in displayed
	assert "1. 搜索和筛选职位 · 执行失败" in displayed
	assert "candidate" not in displayed
	assert "job_search" not in displayed


def test_search_follow_up_selects_job_and_passes_ids_without_displaying_them():
	run = {
		"role": "candidate",
		"platform": "zhipin",
		"goal": "job_search",
		"last_result": {
			"data": {
				"items": [
					{
						"title": "Python 工程师",
						"company": "示例公司",
						"city": "北京",
						"security_id": "sec-hidden",
						"job_id": "job-hidden",
						"lid": "list.1",
					}
				]
			}
		},
	}
	menu = _ScriptedMenu(["0", "apply"])
	follow_up = collect_result_follow_up(run, menu=menu)

	assert isinstance(follow_up, WizardInput)
	assert follow_up.goal == "apply"
	assert follow_up.inputs == {
		"security_id": "sec-hidden",
		"job_id": "job-hidden",
		"lid": "list.1",
	}
	displayed = " ".join(
		part
		for title, options in menu.menus
		for part in (title, *(option.label + " " + option.description for option in options))
	)
	assert "1  Python 工程师" in displayed
	assert "示例公司" in displayed
	assert "sec-hidden" not in displayed
	assert "job-hidden" not in displayed
	assert "list.1" not in displayed


def test_recruiter_follow_up_passes_candidate_ids_and_collects_reply():
	run = {
		"role": "recruiter",
		"platform": "zhipin",
		"goal": "candidates",
		"last_result": {
			"data": {
				"result": {
					"items": [
						{
							"geekId": "geek-hidden",
							"jobId": "job-hidden",
							"friendId": 42,
							"geekName": "张三",
							"expectPosition": "后端工程师",
						}
					]
				}
			}
		},
	}
	menu = _ScriptedMenu(["0", "reply"], ["请发送简历"])
	follow_up = collect_result_follow_up(run, menu=menu)

	assert isinstance(follow_up, WizardInput)
	assert follow_up.goal == "reply"
	assert follow_up.inputs["friend_id"] == 42
	assert follow_up.inputs["message"] == "请发送简历"
	displayed = " ".join(
		part
		for title, options in menu.menus
		for part in (title, *(option.label + " " + option.description for option in options))
	)
	assert "1  张三" in displayed
	assert "geek-hidden" not in displayed
	assert "job-hidden" not in displayed


def test_candidate_communication_follow_up_uses_friend_ids():
	run = {
		"role": "candidate",
		"platform": "zhipin",
		"goal": "communication",
		"last_result": {
			"data": {
				"result": {
					"result": [
						{
							"name": "李四",
							"brandName": "示例科技",
							"securityId": "sec-friend",
							"uid": "gid-9",
							"lastMsg": "你好",
						}
					]
				}
			}
		},
	}
	menu = _ScriptedMenu(["0", "chat_history"])
	follow_up = collect_result_follow_up(run, menu=menu)

	assert isinstance(follow_up, WizardInput)
	assert follow_up.goal == "chat_history"
	assert follow_up.inputs == {"gid": "gid-9"}
	assert "sec-friend" not in " ".join(option.label for _, options in menu.menus for option in options)

	menu = _ScriptedMenu(["0", "exchange", "phone"])
	follow_up = collect_result_follow_up(run, menu=menu)
	assert isinstance(follow_up, WizardInput)
	assert follow_up.goal == "exchange"
	assert follow_up.inputs == {"security_id": "gid-9", "gid": "gid-9", "type": "phone"}


def test_shortlist_and_pipeline_follow_ups_cover_management_paths():
	shortlist_run = {
		"role": "candidate",
		"platform": "zhipin",
		"goal": "shortlist",
		"last_result": {
			"data": {
				"items": [
					{
						"title": "收藏职位",
						"company": "本地公司",
						"security_id": "sec-s",
						"job_id": "job-s",
					}
				]
			}
		},
	}
	shortlist_follow = collect_result_follow_up(shortlist_run, menu=_ScriptedMenu(["0", "job_detail"]))
	assert isinstance(shortlist_follow, WizardInput)
	assert shortlist_follow.goal == "job_detail"
	assert shortlist_follow.inputs["security_id"] == "sec-s"

	pipeline_run = {
		"role": "candidate",
		"platform": "zhipin",
		"goal": "pipeline",
		"last_result": {
			"data": {
				"items": [
					{
						"title": "后端工程师",
						"company": "进度公司",
						"stage": "follow_up",
						"security_id": "sec-p",
						"job_id": "job-p",
						"reason": "需要继续推进",
					}
				]
			}
		},
	}
	pipeline_follow = collect_result_follow_up(
		pipeline_run,
		menu=_ScriptedMenu(["0", "mark", "沟通中"]),
	)
	assert isinstance(pipeline_follow, WizardInput)
	assert pipeline_follow.goal == "mark"
	assert pipeline_follow.inputs["label"] == "沟通中"


def test_ask_continue_session_defaults_to_continue():
	assert ask_continue_session(menu=_ScriptedMenu(["continue"])) is True
	assert ask_continue_session(menu=_ScriptedMenu(["exit"])) is False


def test_ask_continue_session_does_not_request_auto_exit_option():
	menu = _ScriptedMenu(["continue"])
	assert ask_continue_session(menu=menu) is True
	assert menu.last_kwargs.get("allow_exit") is False
	assert menu.last_kwargs.get("allow_back") is False
	labels = [option.label for option in menu.menus[0][1]]
	assert labels == ["继续使用", "退出向导"]


def test_main_menu_hides_resume_when_only_completed_runs():
	options = _main_menu_options(
		[
			{"run_id": "a", "status": "completed"},
			{"run_id": "b", "status": "completed"},
		]
	)
	labels = [item.label for item in options]
	assert "开始新任务" in labels
	assert "查看任务状态" in labels
	assert "恢复已有任务" not in labels
	assert "重试失败任务" not in labels
	assert "停止运行中的任务" not in labels


def test_main_menu_shows_resume_and_retry_for_failed_runs():
	options = _main_menu_options([{"run_id": "f", "status": "failed"}])
	labels = [item.label for item in options]
	assert "恢复已有任务" in labels
	assert "重试失败任务" in labels


def _blocked_failed_run(run_id: str = "f") -> dict:
	"""动作可能已生效、结果未确认的失败 run（--resume 只会返回原失败）。"""
	return {
		"run_id": run_id,
		"status": "failed",
		"steps": [
			{
				"step_name": "candidate_apply",
				"status": "failed",
				"error": {"code": "LOCAL_STATE_WRITE_FAILED", "replay_safe": False},
			}
		],
	}


def test_main_menu_hides_resume_and_retry_when_only_blocked_failed_runs():
	options = _main_menu_options([_blocked_failed_run()])
	labels = [item.label for item in options]
	assert "恢复已有任务" not in labels
	assert "重试失败任务" not in labels
	assert "查看任务状态" in labels


def test_main_menu_keeps_resume_when_a_replayable_failed_run_exists():
	options = _main_menu_options([_blocked_failed_run(), {"run_id": "ok", "status": "failed"}])
	labels = [item.label for item in options]
	assert "恢复已有任务" in labels
	assert "重试失败任务" in labels


def test_collect_control_excludes_blocked_runs_from_resume_picker():
	menu = _ScriptedMenu(["ok"])
	control = _collect_control(menu, "resume", [_blocked_failed_run(), {"run_id": "ok", "status": "failed"}])
	assert control.run_id == "ok"
	assert [option.value for option in menu.menus[0][1]] == ["ok"]


def test_collect_control_status_view_marks_blocked_run_for_manual_check():
	menu = _ScriptedMenu(["f"])
	control = _collect_control(menu, "status", [_blocked_failed_run()])
	assert control.run_id == "f"
	assert "人工核对" in menu.menus[0][1][0].description


def test_entity_follow_up_offers_greet_after_job_detail():
	run = {
		"role": "candidate",
		"platform": "zhipin",
		"goal": "job_detail",
		"status": "completed",
		"params": {"inputs": {"security_id": "sec-1", "job_id": "job-1", "lid": "lid-1"}},
		"last_result": {
			"data": {
				"job": {
					"title": "Golang",
					"company": "示例公司",
					"security_id": "sec-1",
					"job_id": "job-1",
				}
			}
		},
	}
	menu = _ScriptedMenu(["greet"], [""])
	follow = collect_entity_follow_up(run, menu=menu, allow_list_return=True)
	assert isinstance(follow, WizardInput)
	assert follow.goal == "greet"
	assert follow.inputs["security_id"] == "sec-1"
	assert follow.inputs["job_id"] == "job-1"
	assert "向招聘者打招呼" in " ".join(o.label for o in menu.menus[0][1])
	assert "再选其他职位" in " ".join(o.label for o in menu.menus[0][1])


def test_entity_follow_up_home_raises_return_home():
	run = {
		"role": "candidate",
		"platform": "zhipin",
		"goal": "job_detail",
		"params": {"inputs": {"security_id": "sec-1", "job_id": "job-1"}},
		"last_result": {"data": {"job": {"title": "X", "security_id": "sec-1", "job_id": "job-1"}}},
	}
	with pytest.raises(WizardReturnHome):
		collect_entity_follow_up(run, menu=_ScriptedMenu(["home"]), allow_list_return=False)


def test_waiting_input_recovery_offers_crawl_resume():
	from boss_agent_cli.wizard.prompts import collect_waiting_recovery

	run = {
		"role": "candidate",
		"platform": "zhipin",
		"goal": "crawl_start",
		"status": "waiting_input",
		"last_result": {
			"data": {
				"run_id": "crawl-inner",
				"status": "risk_stopped",
				"error": "第 1 页 joblist.json 返回非 JSON 内容，已停止以避免继续触发验证",
			},
			"next_action": "boss crawl resume crawl-inner",
		},
	}
	menu = _ScriptedMenu(["crawl_resume"])
	decision = collect_waiting_recovery(run, menu=menu, clear_before=False)
	assert isinstance(decision, WizardInput)
	assert decision.goal == "crawl_resume"
	assert decision.inputs["run_id"] == "crawl-inner"
	assert menu.last_kwargs.get("clear_before") is False
	assert "继续采集" in " ".join(o.label for o in menu.menus[0][1])


def test_ask_continue_can_preserve_screen_after_status():
	menu = _ScriptedMenu(["continue"])
	assert ask_continue_session(menu=menu, clear_before=False) is True
	assert menu.last_kwargs.get("clear_before") is False


def test_render_run_shows_crawl_summary_for_completed_crawl(monkeypatch):
	from boss_agent_cli.wizard import renderer as renderer_mod

	captured = {}

	def _capture(panel, *args, **kwargs):
		captured["panel"] = panel

	monkeypatch.setattr(renderer_mod.display.console, "print", _capture)
	run = {
		"run_id": "wrn_crawl",
		"role": "candidate",
		"goal": "crawl_start",
		"status": "completed",
		"last_result": {
			"data": {
				"run_id": "crawl-inner",
				"status": "completed",
				"jobs_seen": 75,
				"pages_completed": 3,
				"next_page": 4,
				"query": "AIAgent",
				"city_code": "101280600",
				"output_paths": {"json": "/tmp/jobs.json"},
				"jobs": [
					{
						"title": "AI Agent工程师",
						"company": "腾讯",
						"city": "深圳",
						"salary": "30-50K",
					},
					{
						"title": "AI Agent软件开发岗",
						"company": "外企德科",
						"city": "广州",
						"salary": "20-35K",
					},
				],
				"sample_titles": ["AI Agent工程师 · 腾讯", "AI Agent软件开发岗 · 外企德科"],
				"error": "",
			},
			"next_action": "boss crawl resume crawl-inner",
		},
	}
	assert renderer_mod._render_crawl_summary(run) is True
	renderer_mod.render_run(run)
	assert str(captured["panel"].title) == "采集已完成"
	# Panel body is a Rich Group — walk renderables for structured preview columns.
	from rich.console import Group
	from rich.table import Table
	from rich.text import Text

	body = captured["panel"].renderable
	assert isinstance(body, Group)
	chunks: list[str] = []
	headers: list[str] = []
	for part in body.renderables:
		if isinstance(part, Text):
			chunks.append(part.plain)
		elif isinstance(part, Table):
			for column in part.columns:
				headers.append(str(getattr(column, "header", "") or ""))
				for cell in getattr(column, "_cells", None) or getattr(column, "cells", []) or []:
					chunks.append(str(cell))
		else:
			chunks.append(str(part))
	joined = "\n".join(chunks)
	assert "职位预览" in joined
	assert "AI Agent工程师" in joined
	assert "腾讯" in joined
	assert "30-50K" in joined
	assert "jobs.json" in joined
	assert "75" in joined
	# Structured preview uses column headers.
	assert "职位" in headers and "公司" in headers and "薪资" in headers


def test_preview_jobs_prefers_structured_fields_over_sample_strings():
	from boss_agent_cli.wizard.renderer import _preview_jobs_from_data

	rows = _preview_jobs_from_data(
		{
			"jobs": [
				{"title": "后端", "company": "A公司", "city": "深圳", "salary": "20-30K"},
			],
			"sample_titles": ["应被忽略 · 旧串"],
		},
		limit=5,
	)
	assert rows == [{"title": "后端", "company": "A公司", "city": "深圳", "salary": "20-30K"}]
	fallback = _preview_jobs_from_data({"sample_titles": ["标题X · 公司Y"]}, limit=3)
	assert fallback[0]["title"] == "标题X"
	assert fallback[0]["company"] == "公司Y"


def test_menu_nav_options_are_detected_and_default_skips_exit():
	from boss_agent_cli.wizard.prompts import (
		MenuOption,
		_default_select_index,
		_is_nav_option,
	)

	items = [
		MenuOption("0", "1  职位A", "公司A", kind="item"),
		MenuOption("__result_more__", "› 下一页", kind="nav"),
		MenuOption("__wizard_exit__", "退出向导", kind="danger"),
	]
	assert _is_nav_option(items[0]) is False
	assert _is_nav_option(items[1]) is True
	assert _is_nav_option(items[2]) is True
	# Without explicit default, land on first content item — not exit.
	assert _default_select_index(items, None) == 0
	assert _default_select_index(items, "__result_more__") == 1


def test_goal_menus_include_secondary_hints():
	from boss_agent_cli.wizard.prompts import GOAL_GROUP_HINTS, GOAL_HINTS, GOAL_GROUPS, GOALS

	assert "求职管理" in GOAL_GROUP_HINTS
	for name in GOAL_GROUPS["candidate"][2][1]:  # 求职管理 goals
		assert name in GOAL_HINTS
	# Every catalog goal should have a same-line hint for menu alignment UX.
	for role, goals in GOALS.items():
		for name in goals:
			assert name in GOAL_HINTS, f"missing GOAL_HINTS for {role}/{name}"


def test_list_actions_normalize_items_and_action_panel(monkeypatch):
	from boss_agent_cli.wizard import renderer as renderer_mod
	from boss_agent_cli.wizard.prompts import has_result_follow_up

	# recommendations with nested platform payload flattened via items
	run = {
		"role": "candidate",
		"goal": "recommendations",
		"status": "completed",
		"platform": "zhipin",
		"last_result": {
			"data": {
				"items": [
					{
						"title": "推荐岗",
						"company": "公司A",
						"security_id": "sec",
						"job_id": "job",
					}
				],
				"total": 1,
			}
		},
	}
	assert has_result_follow_up(run) is True
	panels = []
	monkeypatch.setattr(
		renderer_mod.display.console,
		"print",
		lambda *a, **k: panels.append(a[0] if a else None),
	)
	renderer_mod.render_run(
		{
			"role": "candidate",
			"goal": "apply",
			"status": "completed",
			"last_result": {"data": {"security_id": "sec-1", "job_id": "job-1"}},
		}
	)
	assert any(getattr(p, "title", None) == "操作成功" for p in panels)


def test_render_management_empty_and_digest(monkeypatch):
	from boss_agent_cli.wizard import renderer as renderer_mod

	panels = []

	def _capture(obj, *args, **kwargs):
		panels.append(obj)

	monkeypatch.setattr(renderer_mod.display.console, "print", _capture)
	renderer_mod.render_run(
		{
			"role": "candidate",
			"goal": "resumes",
			"status": "completed",
			"last_result": {"data": {"items": [], "total": 0}},
		}
	)
	assert any(getattr(p, "title", None) == "暂无内容" for p in panels)
	panels.clear()
	renderer_mod.render_run(
		{
			"role": "candidate",
			"goal": "digest",
			"status": "completed",
			"last_result": {
				"data": {
					"new_match_count": 2,
					"follow_up_count": 1,
					"interview_count": 0,
					"summary": "ok",
					"new_matches": [{"title": "A", "company": "C"}],
					"follow_ups": [],
					"interviews": [],
				}
			},
		}
	)
	assert any(getattr(p, "title", None) == "求职日报" for p in panels)


def test_menu_descriptions_are_column_aligned_after_padded_labels():
	from boss_agent_cli.wizard.prompts import (
		MenuOption,
		_display_width,
		_menu_label_widths,
		_pad_label,
	)

	items = [
		MenuOption("browse", "浏览职位列表", "选择职位查看详情或打招呼"),
		MenuOption("home", "返回主菜单", "稍后再处理"),
		MenuOption("exit", "退出向导", kind="danger"),
	]
	content_w, _ = _menu_label_widths(items)
	# CJK-aware pad: both labels share the same display width.
	assert _display_width(_pad_label("浏览职位列表", content_w)) == content_w
	assert _display_width(_pad_label("返回主菜单", content_w)) == content_w
	# Descriptions should start at the same visual column after "  ·  ".
	left_a = f"  ●  {_pad_label('浏览职位列表', content_w)}  ·  "
	left_b = f"  ●  {_pad_label('返回主菜单', content_w)}  ·  "
	assert _display_width(left_a) == _display_width(left_b)


def test_enrich_run_with_live_crawl_surfaces_jobs(tmp_path):
	from boss_agent_cli.wizard.live_crawl import enrich_run_with_live_crawl

	db = tmp_path / "cache" / "boss_agent.db"
	db.parent.mkdir(parents=True)
	with CacheStore(db) as cache:
		cache.create_crawl_run(
			"crawl-live",
			{"query": "AIAgent", "city_code": "101280600"},
			str(tmp_path / "crawl" / "runs" / "crawl-live"),
			status="completed",
		)
		cache.put_crawl_job(
			"crawl-live",
			"k1",
			1,
			{
				"title": "AI Agent工程师",
				"company": "腾讯",
				"city": "深圳",
				"salary": "30-50K",
				"security_id": "sec-1",
				"job_id": "job-1",
			},
			detail_done=False,
		)
		stale = {
			"run_id": "wrn_stale",
			"role": "candidate",
			"goal": "crawl_resume",
			"status": "waiting_input",
			"last_result": {
				"data": {
					"run_id": "crawl-live",
					"status": "risk_stopped",
					"jobs_seen": 0,
					"error": "非 JSON",
				}
			},
		}
		enriched = enrich_run_with_live_crawl(stale, cache)
		assert enriched["effective_completed"] is True
		assert enriched["live_jobs_seen"] == 1
		assert enriched["last_result"]["data"]["jobs"][0]["title"] == "AI Agent工程师"
		assert enriched["last_result"]["data"]["jobs"][0]["security_id"] == "sec-1"
		from boss_agent_cli.wizard.prompts import has_result_follow_up

		assert has_result_follow_up(enriched) is True


def test_crawl_follow_up_selects_job_from_enriched_result():
	from boss_agent_cli.wizard.prompts import collect_result_follow_up

	run = {
		"run_id": "wrn_done",
		"role": "candidate",
		"platform": "zhipin",
		"goal": "crawl_resume",
		"status": "completed",
		"live_jobs_seen": 1,
		"last_result": {
			"data": {
				"run_id": "crawl-x",
				"status": "completed",
				"jobs_seen": 1,
				"jobs": [
					{
						"title": "AI Agent软件开发岗",
						"company": "外企德科",
						"security_id": "sec-crawl",
						"job_id": "job-crawl",
						"source": "crawl",
						"crawl_page": 1,
					}
				],
			}
		},
	}
	# Select first job, skip local brief, open online detail.
	menu = _ScriptedMenu(["0", "job_detail"])
	follow = collect_result_follow_up(run, menu=menu)
	assert isinstance(follow, WizardInput)
	assert follow.goal == "job_detail"
	assert follow.inputs["security_id"] == "sec-crawl"
	assert follow.inputs["job_id"] == "job-crawl"


def test_crawl_follow_up_local_brief_then_done(monkeypatch):
	from boss_agent_cli.wizard import renderer as renderer_mod
	from boss_agent_cli.wizard.prompts import collect_result_follow_up

	shown = []
	monkeypatch.setattr(
		renderer_mod,
		"render_crawl_job_brief",
		lambda job, with_description=False: shown.append((job.get("title"), with_description)),
	)
	run = {
		"role": "candidate",
		"platform": "zhipin",
		"goal": "crawl_start",
		"status": "completed",
		"last_result": {
			"data": {
				"jobs": [
					{
						"title": "本地职位A",
						"company": "公司A",
						"security_id": "sec-a",
						"job_id": "job-a",
						"source": "crawl",
						"description": "一段本地描述",
					}
				]
			}
		},
	}
	menu = _ScriptedMenu(["0", "local_brief", "local_detail", "done"])
	assert collect_result_follow_up(run, menu=menu) is None
	assert shown == [("本地职位A", False), ("本地职位A", True)]


def test_result_follow_up_paginates_long_crawl_list():
	from boss_agent_cli.wizard.prompts import RESULT_PAGE_SIZE, collect_result_follow_up

	jobs = [
		{
			"title": f"职位{i}",
			"company": f"公司{i}",
			"security_id": f"sec-{i}",
			"job_id": f"job-{i}",
			"source": "crawl",
		}
		for i in range(RESULT_PAGE_SIZE + 3)
	]
	run = {
		"role": "candidate",
		"platform": "zhipin",
		"goal": "crawl_status",
		"status": "completed",
		"last_result": {"data": {"jobs": jobs, "jobs_seen": len(jobs)}},
	}
	# Page 0 → more → select absolute index RESULT_PAGE_SIZE → job_detail
	target = str(RESULT_PAGE_SIZE)
	menu = _ScriptedMenu(["__result_more__", target, "job_detail"])
	follow = collect_result_follow_up(run, menu=menu)
	assert isinstance(follow, WizardInput)
	assert follow.inputs["security_id"] == f"sec-{RESULT_PAGE_SIZE}"


def test_crawl_start_resumes_existing_run_from_prior_waiting_result():
	from boss_agent_cli.wizard.actions import ActionContext, DEFAULT_ACTIONS
	from boss_agent_cli.output import Logger

	resumed = []

	class _Svc:
		def resume(self, run_id, **kwargs):
			resumed.append(run_id)
			from boss_agent_cli.crawler.service import CrawlOutcome

			return CrawlOutcome(
				run_id=run_id,
				status="completed",
				jobs_seen=1,
				pages_completed=1,
				next_page=2,
				requests_attempted=1,
				detail_requests_attempted=0,
				detail_checks=0,
				elapsed_seconds=1,
				output_paths={"json": f"/tmp/{run_id}.json"},
				error="",
				hooks=[],
			)

	ctx = ActionContext(
		data_dir=Path("/tmp"),
		platform="zhipin",
		role="candidate",
		logger=Logger("error"),
	)
	import boss_agent_cli.wizard.actions as actions_mod

	orig = actions_mod._crawl_service
	actions_mod._crawl_service = lambda context, cache: _Svc()
	try:
		result = DEFAULT_ACTIONS["crawl_start"](
			ctx,
			{"query": "Golang", "city": "广州"},
			{
				"crawl_start": {
					"data": {"run_id": "crawl-inner", "status": "risk_stopped"},
					"status": "waiting_input",
				}
			},
		)
	finally:
		actions_mod._crawl_service = orig
	assert resumed == ["crawl-inner"]
	assert result.status.value == "completed"


def test_crawl_start_resumes_bound_inner_run_from_interrupted_step():
	"""中断步骤只留下 {inner_run_id}：恢复时续跑该 crawl，不新建第二份采集（Issue #882）。"""
	from boss_agent_cli.wizard.actions import DEFAULT_ACTIONS

	resumed = []
	created = []

	class _Svc:
		def resume(self, run_id, **kwargs):
			resumed.append(run_id)
			return CrawlOutcome(
				run_id=run_id,
				status="completed",
				jobs_seen=1,
				pages_completed=1,
				next_page=2,
				requests_attempted=1,
				detail_requests_attempted=0,
				detail_checks=0,
				elapsed_seconds=1,
				output_paths={"json": f"/tmp/{run_id}.json"},
				error="",
				hooks=[],
			)

		def create_and_run(self, settings):
			created.append(settings)
			raise AssertionError("中断后恢复不应新建采集任务")

	ctx = ActionContext(
		data_dir=Path("/tmp"),
		platform="zhipin",
		role="candidate",
		logger=Logger("error"),
	)
	import boss_agent_cli.wizard.actions as actions_mod

	orig = actions_mod._crawl_service
	actions_mod._crawl_service = lambda context, cache: _Svc()
	try:
		result = DEFAULT_ACTIONS["crawl_start"](
			ctx,
			{"query": "Golang", "city": "广州"},
			{"crawl_start": {"inner_run_id": "crw_bound"}},
		)
	finally:
		actions_mod._crawl_service = orig
	assert resumed == ["crw_bound"]
	assert created == []
	assert result.status.value == "completed"


def test_augment_menu_options_dedupes_exit_label():
	from boss_agent_cli.wizard.prompts import _EXIT, _augment_menu_options

	items = _augment_menu_options(
		[
			MenuOption("continue", "继续使用"),
			MenuOption("exit", "退出向导", "结束本次交互"),
		],
		allow_back=False,
		allow_exit=True,
	)
	exit_labels = [item.label for item in items if item.label == "退出向导"]
	assert len(exit_labels) == 1
	assert items[-1].value == "exit"
	assert _EXIT not in {item.value for item in items}


def test_execute_candidate_detail_falls_back_to_job_card_when_detail_rejects_params():
	from boss_agent_cli.wizard.actions import execute_candidate_detail

	class _Platform:
		def job_detail(self, job_id):
			return {"code": 1, "message": "缺少必要参数", "zpData": None}

		def job_card(self, security_id, lid=""):
			assert security_id == "sec-1"
			assert lid == "list.9"
			return {
				"code": 0,
				"zpData": {
					"jobCard": {
						"encryptJobId": "job-1",
						"jobName": "Golang",
						"brandName": "示例公司",
						"salaryDesc": "20-30K",
						"cityName": "广州",
						"experienceName": "3-5年",
						"degreeName": "本科",
						"postDescription": "负责服务端",
						"jobLabels": ["Golang"],
						"bossName": "张三",
						"bossTitle": "HR",
					}
				},
			}

		def is_success(self, response):
			return response.get("code") == 0

		def unwrap_data(self, response):
			return response.get("zpData") or {}

		def parse_error(self, response):
			return "UNKNOWN", str(response.get("message") or "")

	job = execute_candidate_detail(
		_Platform(),
		security_id="sec-1",
		job_id="job-1",
		lid="list.9",
	)
	assert job["title"] == "Golang"
	assert job["company"] == "示例公司"
	assert job["channel"] == "job_card"
	assert job["security_id"] == "sec-1"


def test_error_message_prefers_platform_chinese_text():
	from boss_agent_cli.wizard.renderer import error_message, recovery_message

	assert error_message("NETWORK_ERROR", "缺少必要参数") == "缺少必要参数"
	assert "换一条" in (recovery_message("INVALID_PARAM") or "")


def test_wizard_risk_recovery_requires_explicit_unlock_command():
	"""风控锁不会自动解除：向导恢复文案必须给出本人执行的显式解锁命令。

	登记表对 ACCOUNT_RISK / ENVIRONMENT_RISK / ENVIRONMENT_RISK_LOCKED 都要求
	`boss clean --risk-lock`；向导文案若只说「等几分钟后再继续」，用户会在锁
	未解除时反复重试同一个必然失败的步骤。
	"""
	from boss_agent_cli.wizard.renderer import RECOVERY_LABELS

	for code in ("ACCOUNT_RISK", "ENVIRONMENT_RISK", "ENVIRONMENT_RISK_LOCKED"):
		assert "boss clean --risk-lock" in RECOVERY_LABELS[code], code


def _gate_logged_in(monkeypatch):
	"""让向导入口的登录门直接放行（模拟已登录用户）。"""
	monkeypatch.setattr(
		"boss_agent_cli.auth.manager.AuthManager.check_status",
		lambda self: {"cookies": {"wt2": "x"}, "stoken": "s"},
	)


def test_interactive_wizard_loops_follow_up_from_original_list_results(tmp_path, monkeypatch):
	_gate_logged_in(monkeypatch)
	list_run = {
		"run_id": "search-run",
		"role": "candidate",
		"platform": "zhipin",
		"goal": "job_search",
		"mode": "tty",
		"status": "completed",
		"steps": [],
		"last_result": {
			"data": {
				"items": [
					{
						"title": "Python 工程师",
						"company": "示例公司",
						"security_id": "sec-1",
						"job_id": "job-1",
					},
					{
						"title": "Go 工程师",
						"company": "另一家公司",
						"security_id": "sec-2",
						"job_id": "job-2",
					},
				]
			}
		},
	}
	detail_run = {
		"run_id": "detail-run",
		"role": "candidate",
		"platform": "zhipin",
		"goal": "job_detail",
		"mode": "tty",
		"status": "completed",
		"steps": [],
		"last_result": {"data": {"result": {"title": "Python 工程师"}}},
	}
	executed_goals: list[str] = []

	class _FakeRunner:
		def __init__(self, store, actions):
			self.store = store
			self.actions = actions

		def run(self, plan, context, **kwargs):
			executed_goals.append(plan.goal)
			if plan.goal == "job_search":
				return list_run
			assert plan.goal == "job_detail"
			assert plan.inputs == {"security_id": "sec-1", "job_id": "job-1"}
			return detail_run

	follow_ups = iter(
		[
			WizardInput(
				role="candidate",
				platform="zhipin",
				goal="job_detail",
				inputs={"security_id": "sec-1", "job_id": "job-1"},
				mode="tty",
			),
			None,
		]
	)
	monkeypatch.setattr("boss_agent_cli.commands.wizard._is_interactive", lambda ctx: True)
	monkeypatch.setattr(
		"boss_agent_cli.commands.wizard.collect_wizard_input",
		lambda **kwargs: WizardInput(
			role="candidate",
			platform="zhipin",
			goal="job_search",
			inputs={"query": "Python"},
			mode="tty",
		),
	)
	monkeypatch.setattr(
		"boss_agent_cli.commands.wizard.collect_result_follow_up",
		lambda run, menu=None: next(follow_ups),
	)
	monkeypatch.setattr(
		"boss_agent_cli.commands.wizard.collect_entity_follow_up",
		lambda *args, **kwargs: (_ for _ in ()).throw(WizardReturnHome()),
	)
	monkeypatch.setattr("boss_agent_cli.commands.wizard.WorkflowRunner", _FakeRunner)
	monkeypatch.setattr("boss_agent_cli.display.is_json_mode", lambda ctx: False)

	# After job detail returns home, next main-menu collection exits.
	calls = {"n": 0}

	def collect(**kwargs):
		calls["n"] += 1
		if calls["n"] == 1:
			return WizardInput(
				role="candidate",
				platform="zhipin",
				goal="job_search",
				inputs={"query": "Python"},
				mode="tty",
			)
		raise WizardCancelled

	monkeypatch.setattr("boss_agent_cli.commands.wizard.collect_wizard_input", collect)

	result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "wizard"])

	assert result.exit_code == 0
	assert executed_goals == ["job_search", "job_detail"]
	assert result.stdout == ""
	# 列表摘要的渲染职责已移入 collect_result_follow_up（它才知道当前翻到第几页，
	# 需要逐页重绘）。本用例把该函数整个 mock 掉了，因此观察不到摘要——
	# 真实路径的渲染由 test_result_list_keeps_summary_visible_above_menu 覆盖。
	assert "职位详情" in result.stderr
	assert "Python 工程师" in result.stderr


def test_interactive_wizard_exit_during_entity_follow_up_does_not_traceback(tmp_path, monkeypatch):
	"""WizardCancelled from job follow-up menu must exit cleanly (no uncaught traceback)."""
	from boss_agent_cli.commands import wizard as wizard_mod
	from boss_agent_cli.wizard.prompts import WizardCancelled

	def collect(**kwargs):
		return WizardInput(
			role="candidate",
			platform="zhipin",
			goal="job_detail",
			inputs={"security_id": "sec-1", "job_id": "job-1"},
			mode="tty",
		)

	def fake_run_plan(*args, **kwargs):
		# Simulate: plan completed and entity follow-up raised cancel (退出向导).
		raise WizardCancelled

	cancelled = {"n": 0}

	def note_cancelled():
		cancelled["n"] += 1

	monkeypatch.setattr("boss_agent_cli.commands.wizard._is_interactive", lambda ctx: True)
	monkeypatch.setattr("boss_agent_cli.commands.wizard.collect_wizard_input", collect)
	monkeypatch.setattr("boss_agent_cli.commands.wizard._run_plan", fake_run_plan)
	monkeypatch.setattr(wizard_mod, "render_cancelled", note_cancelled)

	result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "wizard"])
	assert result.exit_code == 0
	assert cancelled["n"] == 1
	assert "Traceback" not in (result.stderr or "")
	assert "WizardCancelled" not in (result.stderr or "")


def test_interactive_wizard_returns_to_main_menu_after_success(tmp_path, monkeypatch):
	_gate_logged_in(monkeypatch)
	plans = iter(
		[
			WizardInput(role="candidate", platform="zhipin", goal="shortlist", inputs={}, mode="tty"),
			WizardInput(role="candidate", platform="zhipin", goal="resumes", inputs={}, mode="tty"),
		]
	)
	executed: list[str] = []

	class _FakeRunner:
		def __init__(self, store, actions):
			pass

		def run(self, plan, context, **kwargs):
			executed.append(plan.goal)
			return {
				"run_id": f"run-{plan.goal}",
				"role": plan.role,
				"platform": plan.platform,
				"goal": plan.goal,
				"mode": "tty",
				"status": "completed",
				"steps": [],
				"last_result": {"data": {"items": []}},
			}

	def collect(**kwargs):
		try:
			return next(plans)
		except StopIteration as exc:
			raise WizardCancelled from exc

	monkeypatch.setattr("boss_agent_cli.commands.wizard._is_interactive", lambda ctx: True)
	monkeypatch.setattr("boss_agent_cli.commands.wizard.collect_wizard_input", collect)
	monkeypatch.setattr("boss_agent_cli.commands.wizard.collect_result_follow_up", lambda run, menu=None: None)
	monkeypatch.setattr("boss_agent_cli.commands.wizard.collect_entity_follow_up", lambda *a, **k: None)
	monkeypatch.setattr("boss_agent_cli.commands.wizard.WorkflowRunner", _FakeRunner)
	monkeypatch.setattr("boss_agent_cli.display.is_json_mode", lambda ctx: False)

	result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "wizard"])

	assert result.exit_code == 0
	assert executed == ["shortlist", "resumes"]


def test_click_fallback_converts_eof_and_interrupt_to_cancel(monkeypatch):
	def abort(*args, **kwargs):
		raise click.Abort

	monkeypatch.setattr(click, "prompt", abort)
	with pytest.raises(WizardCancelled):
		ClickMenu().select("选择", [SimpleNamespace(value="one", label="一", description="")])
	with pytest.raises(WizardCancelled):
		ClickMenu().text("输入")


def test_prompt_toolkit_terminal_failure_falls_back_to_click(monkeypatch):
	monkeypatch.setattr(
		"boss_agent_cli.wizard.prompts.PromptToolkitMenu.select",
		lambda *args, **kwargs: (_ for _ in ()).throw(OSError("terminal unavailable")),
	)
	monkeypatch.setattr(click, "prompt", lambda *args, **kwargs: "1")
	menu = ResilientMenu()

	assert menu.select("选择", [MenuOption("one", "第一项")]) == "one"


def test_click_fallback_cancel_is_chinese_stderr_only(tmp_path, monkeypatch):
	monkeypatch.setattr("boss_agent_cli.commands.wizard._is_interactive", lambda ctx: True)
	# 无历史任务时主菜单只有「开始新任务」+ 自动「退出向导」
	result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "wizard"], input="2\n")

	assert result.exit_code == 0
	assert result.stdout == ""
	assert "请选择要进行的操作" in result.stderr
	assert "退出向导" in result.stderr
	assert "已退出向导" in result.stderr
	assert "恢复已有任务" not in result.stderr
	assert "candidate" not in result.stderr
	assert "workflow" not in result.stderr


@pytest.mark.skipif(sys.platform == "win32", reason="PTY is POSIX-only")
def test_prompt_toolkit_pty_uses_stderr_and_arrow_keys(tmp_path):
	stderr_master_fd, stderr_slave_fd = pty.openpty()
	stdout_master_fd, stdout_slave_fd = pty.openpty()
	env = os.environ.copy()
	env.pop("PYTEST_CURRENT_TEST", None)
	env.update(
		{
			"TERM": "xterm-256color",
			"PROMPT_TOOLKIT_NO_CPR": "1",
			"PYTHONPATH": os.path.join(os.getcwd(), "src"),
		}
	)
	command = [
		sys.executable,
		"-c",
		"from boss_agent_cli.main import cli; cli()",
		"--data-dir",
		str(tmp_path),
		"wizard",
	]
	process = subprocess.Popen(
		command,
		stdin=stderr_slave_fd,
		stdout=stdout_slave_fd,
		stderr=stderr_slave_fd,
		env=env,
		cwd=os.getcwd(),
	)
	os.close(stderr_slave_fd)
	os.close(stdout_slave_fd)
	output = bytearray()
	stdout_output = bytearray()
	try:
		menu_rendered = False
		startup_deadline = time.monotonic() + _PTY_STARTUP_TIMEOUT
		while time.monotonic() < startup_deadline:
			ready, _, _ = select.select([stderr_master_fd], [], [], 0.1)
			if not ready:
				continue
			try:
				chunk = os.read(stderr_master_fd, 65536)
			except OSError:
				break
			if not chunk:
				break
			output.extend(chunk)
			# 提示行恒在内容区，用作「菜单已渲染」的同步信号；
			# 标题现在画在框上（D1），不再是内容区的第一行。
			if "↑↓ 选择" in output.decode("utf-8", errors="ignore"):
				menu_rendered = True
				break
		# 必须确认菜单真渲染出来了再发按键。此前超时也照发，prompt_toolkit 尚未接管
		# 输入，按键被丢掉，最终报成「没退出」——把「启动慢」误诊为「交互坏」，
		# 而且看不到任何有用线索。
		if not menu_rendered:
			process.kill()
			pytest.fail(
				f"prompt_toolkit 菜单在 {_PTY_STARTUP_TIMEOUT}s 内未渲染；"
				f"已捕获输出：{output.decode('utf-8', errors='replace')!r}"
			)
		for _ in range(5):
			os.write(stderr_master_fd, b"\x1b[B")
			time.sleep(0.05)
		os.write(stderr_master_fd, b"\r")
		completion_deadline = time.monotonic() + _PTY_EXIT_TIMEOUT
		while process.poll() is None and time.monotonic() < completion_deadline:
			ready, _, _ = select.select([stderr_master_fd], [], [], 0.1)
			if ready:
				try:
					chunk = os.read(stderr_master_fd, 65536)
				except OSError:
					break
				if not chunk:
					break
				output.extend(chunk)
		# PTY 的 EOF 可以先于 waitpid 观察到退出。读取结束仍须用剩余时限
		# 等待进程，不能提前把正常终端关闭报成「选中后未退出」。
		try:
			process.wait(timeout=max(0.0, completion_deadline - time.monotonic()))
		except subprocess.TimeoutExpired:
			process.kill()
			process.wait(timeout=2)
			pytest.fail(
				f"prompt_toolkit PTY 在选中后 {_PTY_EXIT_TIMEOUT}s 内未退出；"
				f"已捕获输出：{output.decode('utf-8', errors='replace')!r}"
			)
		while True:
			ready, _, _ = select.select([stderr_master_fd], [], [], 0.05)
			if not ready:
				break
			try:
				chunk = os.read(stderr_master_fd, 65536)
			except OSError:
				break
			if not chunk:
				break
			output.extend(chunk)
		while True:
			ready, _, _ = select.select([stdout_master_fd], [], [], 0.05)
			if not ready:
				break
			try:
				chunk = os.read(stdout_master_fd, 65536)
			except OSError:
				break
			if not chunk:
				break
			stdout_output.extend(chunk)
	finally:
		os.close(stderr_master_fd)
		os.close(stdout_master_fd)
		if process.poll() is None:
			process.kill()
		process.wait(timeout=2)

	stderr_text = output.decode("utf-8", errors="replace")
	assert process.returncode == 0
	assert stdout_output == b""
	# 该场景首屏是登录门菜单，框标题即它的 title（D1：标题=当前步骤）
	assert "需要先登录才能继续" in stderr_text, "框标题应为当前步骤（D1）"
	assert "\x1b[?1049h" in stderr_text, "交互会话应进入备用屏（R1）"
	assert stderr_text.rstrip().endswith("\x1b[?1049l"), "退出应还原备用屏（AC6）"
	assert "BOSS 求职助手" not in stderr_text, "常驻应用名行已移除（D1）"
	assert "退出向导" in stderr_text
	assert "已退出向导" in stderr_text


def test_headless_wizard_executes_without_reading_stdin(tmp_path):
	result = CliRunner().invoke(
		cli,
		[
			"--json",
			"--data-dir",
			str(tmp_path),
			"wizard",
			"--input-json",
			_shortlist_input(),
		],
		input="must-not-be-read",
	)
	payload = json.loads(result.stdout)
	assert result.exit_code == 0
	assert payload["ok"] is True
	assert payload["data"]["status"] == "completed"
	assert payload["data"]["steps"][0]["step_name"] == "local_shortlist"


def test_wizard_accepts_local_json_flag_after_subcommand(tmp_path):
	result = CliRunner().invoke(
		cli,
		[
			"--data-dir",
			str(tmp_path),
			"wizard",
			"--json",
			"--input-json",
			_shortlist_input(),
		],
	)
	assert result.exit_code == 0
	assert json.loads(result.stdout)["ok"] is True


def test_root_headless_without_input_returns_structured_error(tmp_path):
	result = CliRunner().invoke(cli, ["--json", "--data-dir", str(tmp_path)])
	payload = json.loads(result.stdout)
	assert result.exit_code == 1
	assert payload["error"]["code"] == "WIZARD_INPUT_REQUIRED"


def test_mixed_tty_headless_forces_json_envelope(tmp_path, monkeypatch):
	monkeypatch.setattr("boss_agent_cli.commands.wizard._is_interactive", lambda ctx: False)
	result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path)])
	payload = json.loads(result.stdout)
	assert result.exit_code == 1
	assert payload["error"]["code"] == "WIZARD_INPUT_REQUIRED"
	assert result.stderr == ""


def test_empty_selector_value_is_invalid_param(tmp_path):
	"""传了 ``--resume ""`` 不能回「非交互模式需要 --json」。

	旧逻辑只看值是否为 None，空串被当成「没有选择器」，于是一边拒绝一边让
	Agent 重复刚做过的动作；真实原因是 flag 后面少了值。
	"""
	for flag in ("--input-json", "--resume", "--status", "--stop"):
		result = CliRunner().invoke(cli, ["--json", "--data-dir", str(tmp_path), "wizard", flag, ""])
		payload = json.loads(result.stdout)
		assert result.exit_code == 1, flag
		assert payload["error"]["code"] == "INVALID_PARAM", flag
		assert flag in payload["error"]["message"], flag
		assert "不能为空" in payload["error"]["message"], flag


def _once_then_cancel(value):
	"""Return value once then exit the interactive main loop."""
	state = {"used": False}

	def collect(**kwargs):
		if state["used"]:
			raise WizardCancelled
		state["used"] = True
		return value

	return collect


def test_root_tty_and_explicit_wizard_share_prompt_collector(tmp_path, monkeypatch):
	collected = WizardInput(
		role="candidate",
		platform="zhipin",
		goal="shortlist",
		inputs={},
		mode="tty",
	)
	monkeypatch.setattr("boss_agent_cli.commands.wizard._is_interactive", lambda ctx: True)
	monkeypatch.setattr("boss_agent_cli.commands.wizard.collect_entity_follow_up", lambda *a, **k: None)
	for args in (["--data-dir", str(tmp_path)], ["--data-dir", str(tmp_path), "wizard"]):
		monkeypatch.setattr(
			"boss_agent_cli.commands.wizard.collect_wizard_input",
			_once_then_cancel(collected),
		)
		result = CliRunner().invoke(cli, args)
		assert result.exit_code == 0


def test_tty_root_and_explicit_wizard_render_only_to_stderr(tmp_path, monkeypatch):
	_gate_logged_in(monkeypatch)
	collected = WizardInput(
		role="candidate",
		platform="zhipin",
		goal="shortlist",
		inputs={},
		mode="tty",
	)
	monkeypatch.setattr("boss_agent_cli.commands.wizard._is_interactive", lambda ctx: True)
	monkeypatch.setattr("boss_agent_cli.commands.wizard.collect_entity_follow_up", lambda *a, **k: None)
	monkeypatch.setattr("boss_agent_cli.display.is_json_mode", lambda ctx: False)

	for args in (["--data-dir", str(tmp_path)], ["--data-dir", str(tmp_path), "wizard"]):
		monkeypatch.setattr(
			"boss_agent_cli.commands.wizard.collect_wizard_input",
			_once_then_cancel(collected),
		)
		result = CliRunner().invoke(cli, args)
		assert result.exit_code == 0
		assert result.stdout == ""
		assert "读取本地候选池" in result.stderr or "查看本地候选池" in result.stderr
		assert "local_shortlist" not in result.stderr
		assert "candidate" not in result.stderr
		assert "shortlist" not in result.stderr


def test_root_help_does_not_start_wizard(tmp_path, monkeypatch):
	monkeypatch.setattr(
		"boss_agent_cli.commands.wizard.collect_wizard_input",
		lambda **kwargs: pytest.fail("help must not start the wizard"),
	)
	result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "--help"])
	assert result.exit_code == 0
	assert "Usage: boss" in result.stdout


def test_wizard_status_and_stop_use_explicit_run_id(tmp_path):
	created = CliRunner().invoke(
		cli,
		[
			"--json",
			"--data-dir",
			str(tmp_path),
			"wizard",
			"--input-json",
			_shortlist_input(),
		],
	)
	run_id = json.loads(created.stdout)["data"]["run_id"]
	status = CliRunner().invoke(
		cli,
		[
			"--json",
			"--data-dir",
			str(tmp_path),
			"wizard",
			"--status",
			run_id,
		],
	)
	assert json.loads(status.stdout)["data"]["run_id"] == run_id
	stop = CliRunner().invoke(
		cli,
		[
			"--json",
			"--data-dir",
			str(tmp_path),
			"wizard",
			"--stop",
			run_id,
		],
	)
	assert stop.exit_code == 1
	envelope = json.loads(stop.stdout)
	assert envelope["error"]["code"] == "WORKFLOW_NOT_FOUND"
	# 任务记录缺失是本地状态问题：Agent 需要一条能直接执行的重建入口。
	assert envelope["error"]["recovery_action"]
	assert envelope["hints"]["next_actions"] == [f"boss --data-dir {tmp_path.resolve()} wizard"]


def test_wizard_status_reports_resume_next_action(tmp_path):
	"""`--status` 成功后不能到此为止：挂起/停止的 run 要给出恢复入口，
	否则 Agent 查完状态就断了（主执行路径跑完反而提示 `--status`）。"""
	created = CliRunner().invoke(
		cli,
		["--json", "--data-dir", str(tmp_path), "wizard", "--input-json", _shortlist_input()],
	)
	run_id = json.loads(created.stdout)["data"]["run_id"]
	status = CliRunner().invoke(
		cli,
		["--json", "--data-dir", str(tmp_path), "wizard", "--status", run_id],
	)

	assert status.exit_code == 0
	envelope = json.loads(status.stdout)
	assert envelope["data"]["run_id"] == run_id
	assert envelope["data"]["status"] == "completed"
	assert [shlex.split(action) for action in envelope["hints"]["next_actions"]] == [
		["boss", "--data-dir", str(tmp_path.resolve()), "wizard"],
		["boss", "--data-dir", str(tmp_path.resolve()), "wizard", "--status", run_id],
	]


def test_wizard_status_and_stop_after_stop_report_only_terminals(tmp_path):
	"""stopped 是终态：不能给 `--resume`，否则 Agent 会陷入永远 ok 的空转（Issue #870）。"""
	plan = WorkflowPlan(
		role="candidate",
		platform="zhipin",
		goal="shortlist",
		inputs={},
		requested_steps=(),
		mode="headless",
	)
	with WorkflowStore(tmp_path) as store:
		store.create("wrn_stopped", plan)

	stop = CliRunner().invoke(cli, ["--json", "--data-dir", str(tmp_path), "wizard", "--stop", "wrn_stopped"])
	assert stop.exit_code == 0
	stopped = json.loads(stop.stdout)
	assert stopped["data"]["status"] == "stopped"
	expected = [
		["boss", "--data-dir", str(tmp_path.resolve()), "wizard"],
		["boss", "--data-dir", str(tmp_path.resolve()), "wizard", "--status", "wrn_stopped"],
	]
	assert [shlex.split(action) for action in stopped["hints"]["next_actions"]] == expected

	status = CliRunner().invoke(cli, ["--json", "--data-dir", str(tmp_path), "wizard", "--status", "wrn_stopped"])
	envelope = json.loads(status.stdout)
	assert [shlex.split(action) for action in envelope["hints"]["next_actions"]] == expected


def test_wizard_stopped_run_offers_bound_crawl_resume(tmp_path):
	"""绑定了内部 crawl 的 stopped run：先给恢复了数据目录的 crawl resume（Issue #870）。"""
	plan = WorkflowPlan(
		role="candidate",
		platform="zhipin",
		goal="crawl_start",
		inputs={"query": "Python", "city": "北京"},
		requested_steps=("crawl_start",),
		mode="headless",
	)
	with WorkflowStore(tmp_path) as store:
		store.create("wrn_crawl", plan)
		store.update_run("wrn_crawl", status="running", current_step="crawl_start")
		store.update_step(
			"wrn_crawl", "crawl_start", status="running", result={"inner_run_id": "crw_inner"}
		)
		store._cache.create_crawl_run(
			"crw_inner",
			{"query": "Python"},
			str(tmp_path / "crawl" / "runs" / "crw_inner"),
		)
		store.request_stop("wrn_crawl")

	status = CliRunner().invoke(cli, ["--json", "--data-dir", str(tmp_path), "wizard", "--status", "wrn_crawl"])
	envelope = json.loads(status.stdout)
	assert envelope["data"]["status"] == "stopped"
	assert [shlex.split(action) for action in envelope["hints"]["next_actions"]] == [
		["boss", "--data-dir", str(tmp_path.resolve()), "crawl", "resume", "crw_inner"],
		["boss", "--data-dir", str(tmp_path.resolve()), "wizard"],
		["boss", "--data-dir", str(tmp_path.resolve()), "wizard", "--status", "wrn_crawl"],
	]


def test_wizard_stop_hints_quote_run_id_with_shell_metacharacters(tmp_path):
	"""run_id 是本地自由文本，拼进可执行提示前必须按 shell 参数转义。"""
	plan = WorkflowPlan(
		role="candidate",
		platform="zhipin",
		goal="shortlist",
		inputs={},
		requested_steps=(),
		mode="headless",
	)
	run_id = "wrn; echo pwn"
	with WorkflowStore(tmp_path) as store:
		store.create(run_id, plan)

	stop = CliRunner().invoke(cli, ["--json", "--data-dir", str(tmp_path), "wizard", "--stop", run_id])
	assert stop.exit_code == 0, stop.stdout
	assert [shlex.split(action) for action in json.loads(stop.stdout)["hints"]["next_actions"]] == [
		["boss", "--data-dir", str(tmp_path.resolve()), "wizard"],
		["boss", "--data-dir", str(tmp_path.resolve()), "wizard", "--status", run_id],
	]


# ── 未登录 = waiting_input（不是 failed，也不阻塞）─────────────


def _auth_plan():
	return WorkflowPlan(
		role="candidate",
		platform="zhipin",
		goal="recommendations",
		inputs={},
		requested_steps=("auth_status",),
		mode="headless",
	)


def test_auth_status_waiting_input_is_resumable_and_reruns_step(tmp_path):
	"""A6：未登录挂起后，同一 run_id 可 resume，且 auth_status 会重跑而非被跳过。"""
	run_id = "waiting-login-run"
	with WorkflowStore(tmp_path) as store:
		first = WorkflowRunner(store, DEFAULT_ACTIONS).run(
			_auth_plan(), _action_context(tmp_path), run_id=run_id
		)
	assert first["status"] == "waiting_input"
	assert first["error"] is None

	# 人「登录完」后重跑同一条 run：auth_status 必须重新执行。
	calls = []

	def logged_in(context, inputs, prior):
		calls.append(prior)
		return StepResult({"authenticated": True, "platform": "zhipin", "role": "candidate"})

	with WorkflowStore(tmp_path) as store:
		resumed = WorkflowRunner(store, {"auth_status": logged_in}).run(
			_auth_plan(), _action_context(tmp_path), run_id=run_id
		)
	assert len(calls) == 1, "waiting_input 步骤 resume 时必须重跑，不能被 prior_results 跳过"
	assert resumed["status"] == "completed"


def test_auth_status_waiting_input_does_not_block(tmp_path):
	"""A7：未登录路径不得阻塞轮询等待扫码。"""
	started = time.monotonic()
	with WorkflowStore(tmp_path) as store:
		run = WorkflowRunner(store, DEFAULT_ACTIONS).run(
			_auth_plan(), _action_context(tmp_path), run_id="nonblocking-run"
		)
	assert run["status"] == "waiting_input"
	assert time.monotonic() - started < 2.0


def test_auth_status_operator_actions_are_natural_language(tmp_path):
	"""operator_actions 面向真人；next_action 才是给 Agent 的命令。"""
	with WorkflowStore(tmp_path) as store:
		run = WorkflowRunner(store, DEFAULT_ACTIONS).run(
			_auth_plan(), _action_context(tmp_path), run_id="operator-actions-run"
		)
	last = run["last_result"]
	assert shlex.split(last["next_action"]) == ["boss", "--data-dir", str(tmp_path.resolve()), "wizard", "--resume", "operator-actions-run"]
	actions = last["operator_actions"]
	assert len(actions) == 2
	assert any("扫码" in action for action in actions)


def test_auth_status_without_workflow_control_falls_back(tmp_path):
	"""直接调 action（无 WorkflowControl）时 resume 命令要有兜底，不能抛 AttributeError。"""
	result = DEFAULT_ACTIONS["auth_status"](_action_context(tmp_path), {}, {})
	assert result.status is WorkflowStatus.WAITING_INPUT
	assert shlex.split(result.next_action) == ["boss", "--data-dir", str(tmp_path.resolve()), "wizard"]
	assert result.operator_actions


# ── waiting_input 面板：数据驱动 + 历史 run 兜底（A12）────────


def _capture_waiting_panel(monkeypatch, run):
	from boss_agent_cli.wizard import renderer as renderer_mod

	panels = []
	monkeypatch.setattr(renderer_mod.display.console, "print", lambda obj, *a, **k: panels.append(obj))
	renderer_mod.render_run(run)
	waiting = [p for p in panels if getattr(p, "title", None) == "等待继续"]
	assert waiting, "waiting_input 应渲染「等待继续」面板"
	return waiting[0].renderable


def test_waiting_panel_prefers_operator_actions(monkeypatch):
	"""有 operator_actions 时用它，不再靠 reason 子串猜文案。"""
	body = _capture_waiting_panel(
		monkeypatch,
		{
			"role": "candidate",
			"goal": "recommendations",
			"status": "waiting_input",
			"last_result": {
				"data": {"authenticated": False},
				"next_action": "boss wizard --resume r1",
				"operator_actions": ["运行 boss --platform zhipin login 并扫码", "登录完成后回到终端继续"],
			},
		},
	)
	assert "运行 boss --platform zhipin login 并扫码" in body
	assert "登录完成后回到终端继续" in body
	# 旧启发式文案不应出现
	assert "继续采集" not in body


def test_waiting_panel_falls_back_for_legacy_runs(monkeypatch):
	"""SQLite 里的历史 run 没有 operator_actions 字段，必须仍走旧启发式。"""
	body = _capture_waiting_panel(
		monkeypatch,
		{
			"role": "candidate",
			"goal": "crawl_start",
			"status": "waiting_input",
			"last_result": {
				"data": {
					"status": "risk_stopped",
					"browser_kept_open": True,
					"error": "环境异常",
				},
				"next_action": "boss crawl resume c1",
			},
		},
	)
	assert "采集用 Chrome 已保持打开" in body


@pytest.mark.parametrize(
	"status, error_code",
	[
		("risk_stopped", None),
		("budget_stopped", None),
		("timeout_stopped", "WORKFLOW_TIMEOUT"),
		("workflow_stopped", "WORKFLOW_STOPPED"),
		("stopped", "WORKFLOW_STOPPED"),
		("failed", "NETWORK_ERROR"),
	],
)
def test_crawl_outcome_recovery_keeps_checkpoint_directory(tmp_path, status, error_code):
	"""向导等待和错误路径都复用采集服务提供的恢复命令。"""
	from boss_agent_cli.wizard.actions import _crawl_outcome_result

	run_id = "run 'one'; $RUN"
	data_dir = tmp_path / "state 'one'; $STATE"
	outcome = replace(
		_crawl_outcome(run_id, status),
		data_dir=data_dir,
		error="stop requested" if status in {"workflow_stopped", "stopped"} else "fixture stop",
	)
	expected = ["boss", "--data-dir", str(data_dir.resolve()), "crawl", "resume", run_id]
	if error_code is not None:
		with pytest.raises(WorkflowActionError) as exc_info:
			_crawl_outcome_result(outcome)
		assert exc_info.value.code == error_code
		assert shlex.split(exc_info.value.recovery_action) == expected
	else:
		result = _crawl_outcome_result(outcome)
		assert result.status is WorkflowStatus.WAITING_INPUT
		assert shlex.split(result.next_action) == expected
		assert result.next_action == result.data["checkpoint"]["resume_command"]
		assert any(result.next_action in action for action in result.operator_actions)


def test_crawl_risk_stop_emits_browser_operator_actions():
	"""风控停止的文案改为由 step 声明数据，而不是 renderer 猜。"""
	from boss_agent_cli.wizard.actions import _crawl_outcome_result

	result = _crawl_outcome_result(_crawl_outcome("crawl-1", "risk_stopped"))
	assert result.status is WorkflowStatus.WAITING_INPUT
	assert result.next_action == "boss crawl resume crawl-1"
	assert any("Chrome 已保持打开" in action for action in result.operator_actions)
	assert any("boss crawl resume crawl-1" in action for action in result.operator_actions)


def test_crawl_budget_stop_does_not_claim_risk():
	"""预算停止不是账号异常，文案必须区分开。"""
	from boss_agent_cli.wizard.actions import _crawl_outcome_result

	result = _crawl_outcome_result(_crawl_outcome("crawl-1", "budget_stopped"))
	assert result.status is WorkflowStatus.WAITING_INPUT
	assert any("预算上限" in action for action in result.operator_actions)
	assert not any("Chrome 已保持打开" in action for action in result.operator_actions)


# ── 向导入口登录门（R6 / A13–A18）───────────────────────────


def _gate_ctx(tmp_path, *, platform="zhipin", role="candidate"):
	ctx = SimpleNamespace()
	ctx.obj = {
		"data_dir": tmp_path,
		"logger": Logger(),
		"platform": platform,
		"role": role,
		"cdp_url": None,
	}
	return ctx


def test_gate_is_silent_and_free_when_logged_in(tmp_path, monkeypatch, capsys):
	"""A13：已登录用户零输出、不预检浏览器内核、不开浏览器。"""
	from boss_agent_cli.wizard import preflight

	monkeypatch.setattr(
		"boss_agent_cli.auth.manager.AuthManager.check_status",
		lambda self: {"cookies": {"wt2": "x"}},
	)
	kernel_calls = []
	monkeypatch.setattr(preflight, "_browser_kernel_status", lambda: kernel_calls.append(1) or ("ok", ""))
	login_calls = []
	monkeypatch.setattr(
		"boss_agent_cli.auth.manager.AuthManager.login",
		lambda self, **kw: login_calls.append(kw),
	)

	assert preflight.ensure_login(_gate_ctx(tmp_path)) == preflight.GATE_READY
	assert kernel_calls == []
	assert login_calls == []
	captured = capsys.readouterr()
	assert captured.out == ""
	assert captured.err == ""


def test_gate_offers_login_before_any_goal_selection(tmp_path, monkeypatch):
	"""A14/A15：未登录时先给三选菜单；选「现在登录」直接开浏览器。"""
	from boss_agent_cli.wizard import preflight

	monkeypatch.setattr("boss_agent_cli.auth.manager.AuthManager.check_status", lambda self: None)
	monkeypatch.setattr(preflight, "_browser_kernel_status", lambda: ("ok", ""))
	login_calls = []
	monkeypatch.setattr(
		"boss_agent_cli.auth.manager.AuthManager.login",
		lambda self, **kw: login_calls.append(kw) or {"cookies": {"wt2": "x"}},
	)

	menu = _ScriptedMenu(["login"])
	assert preflight.ensure_login(_gate_ctx(tmp_path), menu=menu) == preflight.GATE_READY
	assert len(login_calls) == 1
	title, options = menu.menus[0]
	assert "现在登录" in title or "登录" in title
	assert [option.value for option in options] == ["login", "local", "exit"]


def test_gate_blocks_on_missing_browser_kernel_without_opening_browser(tmp_path, monkeypatch):
	"""A16：内核缺失时给安装指引，绝不尝试开浏览器。"""
	from boss_agent_cli.wizard import preflight

	monkeypatch.setattr("boss_agent_cli.auth.manager.AuthManager.check_status", lambda self: None)
	monkeypatch.setattr(
		preflight,
		"_browser_kernel_status",
		lambda: ("error", "patchright 需要 chromium-1234，请运行 patchright install chromium"),
	)
	login_calls = []
	monkeypatch.setattr(
		"boss_agent_cli.auth.manager.AuthManager.login",
		lambda self, **kw: login_calls.append(kw),
	)

	# 第一次选登录 → 被内核拦住回到菜单；第二次选退出。
	menu = _ScriptedMenu(["login", "exit"])
	assert preflight.ensure_login(_gate_ctx(tmp_path), menu=menu) == preflight.GATE_EXIT
	assert login_calls == [], "内核缺失时不得尝试打开浏览器"


def test_gate_login_risk_stops_without_offering_retry(tmp_path, monkeypatch):
	"""风控是硬边界：登录门展示停止指引后退出，不再回到「重试登录」菜单。"""
	from boss_agent_cli.api.client import AccountRiskError
	from boss_agent_cli.wizard import preflight

	monkeypatch.setattr("boss_agent_cli.auth.manager.AuthManager.check_status", lambda self: None)
	monkeypatch.setattr(preflight, "_browser_kernel_status", lambda: ("ok", ""))

	def _boom(self, **kw):
		raise AccountRiskError("fixture risk", is_cdp=True)

	monkeypatch.setattr("boss_agent_cli.auth.manager.AuthManager.login", _boom)

	menu = _ScriptedMenu(["login", "exit"])
	assert preflight.ensure_login(_gate_ctx(tmp_path), menu=menu) == preflight.GATE_EXIT
	assert len(menu.menus) == 1, "风控后不应再给重试登录的菜单"


def test_gate_login_failure_shows_operator_actions_and_retries(tmp_path, monkeypatch, capsys):
	"""登录失败复用 R5 的双通道文案，且可回菜单重试。"""
	from boss_agent_cli.wizard import preflight

	monkeypatch.setattr("boss_agent_cli.auth.manager.AuthManager.check_status", lambda self: None)
	monkeypatch.setattr(preflight, "_browser_kernel_status", lambda: ("ok", ""))

	def _boom(self, **kw):
		raise TimeoutError("login timeout")

	monkeypatch.setattr("boss_agent_cli.auth.manager.AuthManager.login", _boom)

	menu = _ScriptedMenu(["login", "exit"])
	assert preflight.ensure_login(_gate_ctx(tmp_path), menu=menu) == preflight.GATE_EXIT
	assert len(menu.menus) == 2, "登录失败后应回到菜单可重试"


def test_gate_local_only_filters_goal_menu(tmp_path, monkeypatch):
	"""A17：仅本地功能时 goal 菜单只剩不需登录的目标。"""
	from boss_agent_cli.wizard import preflight
	from boss_agent_cli.wizard.prompts import _visible_goal_groups

	monkeypatch.setattr("boss_agent_cli.auth.manager.AuthManager.check_status", lambda self: None)
	menu = _ScriptedMenu(["local"])
	assert preflight.ensure_login(_gate_ctx(tmp_path), menu=menu) == preflight.GATE_LOCAL_ONLY

	groups = _visible_goal_groups("candidate", local_only=True)
	visible = {goal for _name, goals in groups for goal in goals}
	assert visible == set(preflight.local_goal_names("candidate"))
	assert "job_search" not in visible and "shortlist" in visible


def test_gate_recruiter_has_no_local_goals(tmp_path, monkeypatch):
	"""A17：招聘者全部能力都需登录，菜单不该出现「仅看本地功能」。"""
	from boss_agent_cli.wizard import preflight
	from boss_agent_cli.wizard.prompts import _visible_goal_groups

	assert preflight.local_goal_names("recruiter") == ()
	assert _visible_goal_groups("recruiter", local_only=True) == ()

	monkeypatch.setattr("boss_agent_cli.auth.manager.AuthManager.check_status", lambda self: None)
	menu = _ScriptedMenu(["exit"])
	preflight.ensure_login(_gate_ctx(tmp_path, role="recruiter"), menu=menu)
	_title, options = menu.menus[0]
	assert [option.value for option in options] == ["login", "exit"]


def test_headless_paths_bypass_the_gate(tmp_path):
	"""A18：headless / --input-json 不经登录门，仍走 R3 的 waiting_input。"""
	result = CliRunner().invoke(
		cli,
		[
			"--json",
			"--data-dir",
			str(tmp_path),
			"wizard",
			"--input-json",
			json.dumps({"role": "candidate", "platform": "zhipin", "goal": "recommendations"}),
		],
	)
	payload = json.loads(result.stdout)
	assert payload["ok"] is True
	assert payload["data"]["status"] == "waiting_input"
	assert payload["data"]["error"] is None


# ── 恢复采集完成后必须停留在结果上，而不是闪回主菜单 ─────────


def test_recovery_completion_falls_through_to_result_browsing(tmp_path, monkeypatch):
	"""真实使用反馈：继续采集成功后摘要一闪即逝，要进「查看任务状态」才能看到。

	修复后：recovery 完成 → 落入 completed 处理器（渲染 + 可浏览列表），
	不再提前 return 让主菜单立刻清屏。
	"""
	from boss_agent_cli.commands import wizard as wizard_mod

	waiting_run = {
		"run_id": "outer-1",
		"role": "candidate",
		"platform": "zhipin",
		"goal": "crawl_start",
		"status": "waiting_input",
		"last_result": {"data": {"run_id": "crawl-inner", "status": "risk_stopped"}},
	}
	completed_run = {
		"run_id": "recovery-1",
		"role": "candidate",
		"platform": "zhipin",
		"goal": "crawl_resume",
		"status": "completed",
		"last_result": {"data": {"items": [{"title": "Golang 工程师"}]}},
	}

	class _FakeRunner:
		def __init__(self, store, actions):
			self.calls = 0

		def run(self, plan, context, **kwargs):
			self.calls += 1
			return waiting_run if self.calls == 1 else completed_run

	monkeypatch.setattr(wizard_mod, "WorkflowRunner", _FakeRunner)
	monkeypatch.setattr(wizard_mod, "_build_context", lambda ctx, plan: object())
	monkeypatch.setattr(wizard_mod, "render_run", lambda run: None)
	monkeypatch.setattr(
		wizard_mod,
		"collect_waiting_recovery",
		lambda run, **kw: WizardInput(
			role="candidate", platform="zhipin", goal="crawl_resume",
			inputs={"run_id": "crawl-inner"}, mode="tty",
		),
	)
	browsed = []
	monkeypatch.setattr(wizard_mod, "has_result_follow_up", lambda run: True)
	monkeypatch.setattr(
		wizard_mod,
		"_browse_list_results",
		lambda ctx, store, runner, run, **kw: browsed.append(run["run_id"]) or run,
	)

	ctx = SimpleNamespace()
	ctx.obj = {"data_dir": tmp_path, "logger": Logger(), "platform": "zhipin",
	           "role": "candidate", "cdp_url": None, "delay": (0, 0), "config": {}}
	plan = build_plan(WizardInput(
		role="candidate", platform="zhipin", goal="crawl_start",
		inputs={"query": "Golang", "city": "广州"}, mode="tty",
	))
	result = wizard_mod._run_plan(
		ctx, object(), plan,
		interactive=True, resume_run_id=None, timeout_seconds=None, max_retries=0,
	)
	assert browsed == ["recovery-1"], "recovery 完成后应进入结果浏览，而不是直接返回主菜单"
	assert result["run_id"] == "recovery-1"


def test_menu_hint_advertises_left_arrow_back():
	"""左方向键返回是公开交互承诺，提示行必须写出来。"""
	import inspect

	from boss_agent_cli.wizard.prompts import PromptToolkitMenu

	source = inspect.getsource(PromptToolkitMenu.select)
	assert 'bindings.add("left")' in source
	# 提示行随内容区一起抽到了 _menu_content_lines（框标题化重构）
	from boss_agent_cli.wizard.prompts import _menu_content_lines

	assert "←/Esc 返回" in inspect.getsource(_menu_content_lines)


# ── 采集完成后的列表：摘要必须留在菜单上方（与「查看任务状态」一致）──


def _crawl_completed_run(job_count: int = 3) -> dict:
	jobs = [
		{
			"title": f"职位{i}",
			"company": f"公司{i}",
			"security_id": f"sec-{i}",
			"job_id": f"job-{i}",
			"source": "crawl",
		}
		for i in range(job_count)
	]
	return {
		"role": "candidate",
		"platform": "zhipin",
		"goal": "crawl_status",
		"status": "completed",
		"run_id": "wrn_demo",
		"last_result": {"data": {"jobs": jobs, "jobs_seen": len(jobs), "status": "completed"}},
	}


def test_result_list_keeps_summary_visible_above_menu(monkeypatch):
	"""采集完成后的列表必须与「重新进入任务状态」看到同一个摘要框。

	此前 MenuDriver.select 的 clear_before 默认 True，会把 _run_plan 刚渲染的
	摘要 Panel 清掉，只剩一个光秃秃的选择列表——两个入口观感不一致。
	"""
	from boss_agent_cli.wizard.prompts import collect_result_follow_up

	rendered: list[str] = []
	monkeypatch.setattr(
		"boss_agent_cli.wizard.renderer.render_run",
		lambda run, *, with_preview=True: rendered.append(str(run.get("run_id"))),
	)
	monkeypatch.setattr("boss_agent_cli.wizard.renderer.clear_wizard_screen", lambda: None)

	menu = _ScriptedMenu(["0", "job_detail"])
	collect_result_follow_up(_crawl_completed_run(), menu=menu)

	# kwargs_log[0] 是列表菜单本身；后续是选中职位后的动作菜单
	assert menu.kwargs_log[0]["clear_before"] is False, "列表菜单不得清屏，否则摘要被抹掉"
	assert rendered == ["wrn_demo"], "选择菜单前应重绘任务摘要"


def test_result_list_redraws_summary_on_every_page(monkeypatch):
	"""翻页时摘要也要跟着重绘，否则翻一页就丢。"""
	from boss_agent_cli.wizard.prompts import RESULT_PAGE_SIZE, collect_result_follow_up

	rendered: list[str] = []
	monkeypatch.setattr(
		"boss_agent_cli.wizard.renderer.render_run",
		lambda run, *, with_preview=True: rendered.append(str(run.get("run_id"))),
	)
	monkeypatch.setattr("boss_agent_cli.wizard.renderer.clear_wizard_screen", lambda: None)

	run = _crawl_completed_run(job_count=RESULT_PAGE_SIZE + 3)
	menu = _ScriptedMenu(["__result_more__", str(RESULT_PAGE_SIZE), "job_detail"])
	collect_result_follow_up(run, menu=menu)

	assert len(rendered) == 2, f"两页应各重绘一次摘要，实际 {len(rendered)} 次"


def test_result_list_menu_still_returns_selection(monkeypatch):
	"""回归护栏：加重绘不得改变选择结果。"""
	from boss_agent_cli.wizard.prompts import collect_result_follow_up

	monkeypatch.setattr("boss_agent_cli.wizard.renderer.render_run", lambda run, *, with_preview=True: None)
	monkeypatch.setattr("boss_agent_cli.wizard.renderer.clear_wizard_screen", lambda: None)

	menu = _ScriptedMenu(["1", "job_detail"])
	follow = collect_result_follow_up(_crawl_completed_run(), menu=menu)

	assert isinstance(follow, WizardInput)
	assert follow.inputs["security_id"] == "sec-1"


# ── 浏览列表时摘要框不再重复展示职位预览 ──────────────────────


def _capture_wizard_console(monkeypatch):
	import io

	from rich.console import Console

	stream = io.StringIO()
	monkeypatch.setattr("boss_agent_cli.display.console", Console(file=stream, force_terminal=False, width=120))
	return stream


def test_crawl_summary_includes_preview_by_default(monkeypatch):
	"""状态视图（不进列表）仍需要职位预览——那时没有可选菜单。"""
	from boss_agent_cli.wizard.renderer import render_run

	stream = _capture_wizard_console(monkeypatch)
	render_run(_crawl_completed_run(job_count=8))

	assert "职位预览" in stream.getvalue()


def test_crawl_summary_omits_preview_when_browsing(monkeypatch):
	"""浏览列表时下方已有可选菜单，框里再列一遍职位是冗余且页码对不上。"""
	from boss_agent_cli.wizard.renderer import render_run

	stream = _capture_wizard_console(monkeypatch)
	render_run(_crawl_completed_run(job_count=8), with_preview=False)
	out = stream.getvalue()

	assert "职位预览" not in out
	assert "采集已完成" in out, "摘要本身仍要在"
	assert "75" in out or "8" in out, "职位总数仍要显示"


def test_list_header_disables_preview(monkeypatch):
	"""结果列表上方的摘要必须关掉预览。"""
	from boss_agent_cli.wizard.prompts import collect_result_follow_up

	captured: list[bool] = []
	monkeypatch.setattr(
		"boss_agent_cli.wizard.renderer.render_run",
		lambda run, *, with_preview=True: captured.append(with_preview),
	)
	monkeypatch.setattr("boss_agent_cli.wizard.renderer.clear_wizard_screen", lambda: None)

	collect_result_follow_up(_crawl_completed_run(), menu=_ScriptedMenu(["0", "job_detail"]))

	assert captured == [False], f"列表上方摘要应关闭预览，实际 {captured}"


# ── 整个交互会话独占一块备用屏缓冲 ────────────────────────────


_ALT_ENTER = "\033[?1049h"
_ALT_EXIT = "\033[?1049l"


def _force_screen_control(monkeypatch):
	"""解除 pytest / 非 TTY 的 no-op 守卫，让转义序列真的写出来。"""
	import io

	stream = io.StringIO()
	monkeypatch.setattr("boss_agent_cli.wizard.renderer._screen_control_unavailable", lambda: False)
	monkeypatch.setattr("boss_agent_cli.wizard.renderer.sys", type("S", (), {"stderr": stream})())
	return stream


def test_wizard_screen_enters_and_restores_alternate_buffer(monkeypatch):
	from boss_agent_cli.wizard.renderer import wizard_screen

	stream = _force_screen_control(monkeypatch)
	with wizard_screen():
		assert stream.getvalue() == _ALT_ENTER, "进入时应切到备用屏"
	assert stream.getvalue() == _ALT_ENTER + _ALT_EXIT, "退出时应还原"


def test_wizard_screen_restores_on_exception(monkeypatch):
	"""异常路径必须还原，否则用户终端卡在备用屏里（AC6）。"""
	from boss_agent_cli.wizard.renderer import wizard_screen

	stream = _force_screen_control(monkeypatch)
	with pytest.raises(ValueError):
		with wizard_screen():
			raise ValueError("boom")
	assert stream.getvalue().endswith(_ALT_EXIT)


def test_wizard_screen_restores_on_system_exit(monkeypatch):
	"""向导内部会 raise SystemExit(1)，同样必须还原。"""
	from boss_agent_cli.wizard.renderer import wizard_screen

	stream = _force_screen_control(monkeypatch)
	with pytest.raises(SystemExit):
		with wizard_screen():
			raise SystemExit(1)
	assert stream.getvalue().endswith(_ALT_EXIT)


def test_wizard_screen_restores_on_keyboard_interrupt(monkeypatch):
	"""Ctrl-C 也要还原（AC6）。"""
	from boss_agent_cli.wizard.renderer import wizard_screen

	stream = _force_screen_control(monkeypatch)
	with pytest.raises(KeyboardInterrupt):
		with wizard_screen():
			raise KeyboardInterrupt
	assert stream.getvalue().endswith(_ALT_EXIT)


def test_wizard_screen_is_noop_without_tty(monkeypatch):
	"""Agent / 管道 / dumb terminal 路径行为完全不变。"""
	import io

	from boss_agent_cli.wizard.renderer import wizard_screen

	stream = io.StringIO()
	monkeypatch.setattr("boss_agent_cli.wizard.renderer._screen_control_unavailable", lambda: True)
	monkeypatch.setattr("boss_agent_cli.wizard.renderer.sys", type("S", (), {"stderr": stream})())
	with wizard_screen():
		pass
	assert stream.getvalue() == ""


def test_interactive_session_runs_inside_alternate_screen(monkeypatch, tmp_path):
	"""多轮向导必须被备用屏包裹；单次 flag 路径不得进（AC7）。"""
	from boss_agent_cli.commands import wizard as wizard_mod

	order: list[str] = []

	class _FakeScreen:
		def __enter__(self):
			order.append("enter")
			return self

		def __exit__(self, *exc):
			order.append("exit")
			return False

	monkeypatch.setattr(wizard_mod, "wizard_screen", lambda: _FakeScreen())
	monkeypatch.setattr(wizard_mod, "_is_interactive", lambda ctx: True)
	monkeypatch.setattr(
		wizard_mod,
		"_run_interactive_session",
		lambda *a, **k: order.append("session"),
	)

	CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "wizard"])

	assert order == ["enter", "session", "exit"]


# ── 菜单包进带标题的边框 ──────────────────────────────────────


def test_menu_container_is_framed_with_step_title():
	"""框标题用当前步骤（D1），与采集摘要 Panel 同构。"""
	from prompt_toolkit.layout.controls import FormattedTextControl
	from prompt_toolkit.widgets import Frame

	from boss_agent_cli.wizard.prompts import _build_menu_container

	container = _build_menu_container("请选择职位（第 1-12 / 共 75 条）", FormattedTextControl(""))

	assert isinstance(container, Frame)
	assert container.title == "请选择职位（第 1-12 / 共 75 条）"


def test_menu_container_holds_the_content_control():
	"""框里装的必须是传入的菜单内容，不能丢。"""
	from prompt_toolkit.layout.controls import FormattedTextControl
	from prompt_toolkit.layout.containers import Window

	from boss_agent_cli.wizard.prompts import _build_menu_container

	control = FormattedTextControl("菜单内容")
	container = _build_menu_container("标题", control)

	found = []

	def walk(node):
		if isinstance(node, Window) and getattr(node, "content", None) is control:
			found.append(node)
		for child in getattr(node, "children", []) or []:
			walk(child)
		body = getattr(node, "body", None)
		if body is not None:
			walk(body)

	walk(container)
	assert found, "菜单内容控件应在框内"


def test_menu_content_does_not_repeat_title_inside_frame(monkeypatch):
	"""标题上了框，框内就不该再打印一遍（D1：去掉常驻应用名行）。"""
	from boss_agent_cli.wizard.prompts import _menu_content_lines, MenuOption

	lines = _menu_content_lines(
		[MenuOption("a", "选项A"), MenuOption("b", "选项B")],
		selected=0,
		title="请选择职位",
	)
	text = "".join(part[1] for part in lines)

	assert "请选择职位" not in text, "标题已在框上，内容区不应重复"
	assert "BOSS 求职助手" not in text, "常驻应用名行应移除"
	assert "选项A" in text and "选项B" in text


# ── runner 风控终止语义（Issue #419） ─────────────────────


def test_runner_platform_risk_is_terminal_and_never_retried(tmp_path):
	from boss_agent_cli.api.client import AccountRiskError, EnvironmentRiskError

	for exc_cls, code in ((AccountRiskError, "ACCOUNT_RISK"), (EnvironmentRiskError, "ENVIRONMENT_RISK")):
		attempts = 0

		def risky(context, inputs, prior, _exc_cls=exc_cls):
			nonlocal attempts
			attempts += 1
			raise _exc_cls("平台风控", is_cdp=True)

		with WorkflowStore(tmp_path) as store:
			run = WorkflowRunner(store, {"risky": risky, "after": lambda c, i, p: StepResult({"ok": True})}).run(
				_plan("risky", "after"),
				object(),
				run_id=f"risk-run-{code}",
				max_retries=2,
			)

		steps = {step["step_name"]: step for step in run["steps"]}
		assert attempts == 1, "风控异常不得重试"
		assert run["status"] == "failed"
		assert run["error"]["code"] == code
		assert run["error"]["message"] == risk_error_message(code)
		assert "平台风控" not in run["error"]["message"]
		assert run["error"]["recoverable"] is False
		assert "停止自动化访问" in run["error"]["recovery_action"]
		assert run["error"]["recovery_action"] != "重试当前 run_id"
		# checkpoint 落盘：失败步骤记录了错误，后续步骤未被执行
		assert steps["risky"]["status"] == "failed"
		assert steps["risky"]["error"]["code"] == code
		assert steps["after"]["status"] != "completed"


def test_runner_browser_source_unavailable_keeps_policy_error_code(tmp_path):
	from boss_agent_cli.api.browser_source import BrowserSourceUnavailable, resolve_policy

	policy = resolve_policy("stored-cookie")

	def no_channel(context, inputs, prior):
		raise BrowserSourceUnavailable(policy, attempted=("cdp",), detail="CDP 不可用")

	with WorkflowStore(tmp_path) as store:
		run = WorkflowRunner(store, {"no_channel": no_channel}).run(_plan("no_channel"), object(), run_id="bs-run")

	assert run["status"] == "failed"
	assert run["error"]["code"] == policy.failure_code
	assert run["error"]["code"] != "NETWORK_ERROR"
	assert run["error"]["recovery_action"] == policy.recovery_action


def test_runner_browser_navigation_timeout_is_not_generic_network_error(tmp_path):
	from boss_agent_cli.api.browser_client import BrowserNavigationError

	def stalled(context, inputs, prior):
		raise BrowserNavigationError(
			"https://www.zhipin.com/",
			attempts=2,
			timeout_ms=15000,
			channel="headless",
			cause=TimeoutError("Page.goto: Timeout 15000ms exceeded"),
		)

	with WorkflowStore(tmp_path) as store:
		run = WorkflowRunner(store, {"stalled": stalled}).run(_plan("stalled"), object(), run_id="nav-run", max_retries=2)

	assert run["status"] == "failed"
	assert run["error"]["code"] == "BROWSER_NAV_TIMEOUT"
	assert run["error"]["code"] != "NETWORK_ERROR"
	assert run["error"]["details"]["channel"] == "headless"
	assert "Page.goto" not in run["error"]["message"]
	assert "boss login" in run["error"]["recovery_action"]


def test_classify_action_error_uses_risk_contract_for_risk_codes():
	from boss_agent_cli.wizard.actions import _classify_action_error

	for code in ("ACCOUNT_RISK", "ENVIRONMENT_RISK"):
		mapped, recoverable, recovery = _classify_action_error(code, "任意文案")
		assert mapped == code
		assert recoverable is False
		assert "停止自动化访问" in recovery
		assert "稍后重试" not in recovery


def test_unwrap_risk_response_uses_registry_message():
	from boss_agent_cli.wizard.actions import _unwrap

	platform = MagicMock()
	platform.is_success.return_value = False
	platform.parse_error.return_value = ("ENVIRONMENT_RISK", "fixture-platform-response-token")
	with pytest.raises(WorkflowActionError) as caught:
		_unwrap(platform, {"code": 37, "message": "fixture-platform-response-token"}, "最近消息获取失败")
	assert caught.value.code == "ENVIRONMENT_RISK"
	assert str(caught.value) == risk_error_message("ENVIRONMENT_RISK")
	assert "fixture-platform-response-token" not in str(caught.value)
	assert caught.value.recoverable is False


def test_unwrap_marks_action_unconfirmed_as_not_replay_safe():
	from boss_agent_cli.wizard.actions import _unwrap

	platform = MagicMock()
	platform.is_success.return_value = False
	platform.parse_error.return_value = ("ACTION_UNCONFIRMED", "发送结果未确认")
	response = {
		"code": -1,
		"message": "发送结果未确认",
		"__cli_error_code__": "ACTION_UNCONFIRMED",
		"__cli_error_details__": {"outcome": "unknown", "replay_safe": False},
	}
	with pytest.raises(WorkflowActionError) as caught:
		_unwrap(platform, response, "消息发送失败")
	assert caught.value.code == "ACTION_UNCONFIRMED"
	assert caught.value.recoverable is False
	assert caught.value.replay_safe is False
	assert caught.value.to_dict()["replay_safe"] is False


def test_classify_action_error_keeps_action_unconfirmed_no_retry_contract():
	from boss_agent_cli.wizard.actions import _classify_action_error

	mapped, recoverable, recovery = _classify_action_error("ACTION_UNCONFIRMED", "任意文案")
	assert mapped == "ACTION_UNCONFIRMED"
	assert recoverable is False
	assert "boss hr chatmsg" in recovery
	assert "稍后重试" not in recovery


def test_classify_action_error_keeps_endpoint_deprecated_no_retry_contract():
	from boss_agent_cli.schema.error_codes import ERROR_CODES
	from boss_agent_cli.wizard.actions import _classify_action_error

	mapped, recoverable, recovery = _classify_action_error("ENDPOINT_DEPRECATED", "旧简历接口已下线")
	assert mapped == "ENDPOINT_DEPRECATED"
	assert recoverable is False
	assert recovery == ERROR_CODES["ENDPOINT_DEPRECATED"]["recovery_action"]
	assert "稍后重试" not in recovery


def test_runner_browser_source_unsupported_maps_to_not_supported(tmp_path):
	from boss_agent_cli.api.browser_source import BrowserSourceUnsupported

	def unsupported(context, inputs, prior):
		raise BrowserSourceUnsupported("otherplat", "stored-cookie")

	with WorkflowStore(tmp_path) as store:
		run = WorkflowRunner(store, {"unsupported": unsupported}).run(_plan("unsupported"), object(), run_id="bsu-run")

	assert run["status"] == "failed"
	assert run["error"]["code"] == "NOT_SUPPORTED"
	assert run["error"]["recoverable"] is True
	assert run["error"]["recovery_action"]


def test_resume_rejects_run_saved_with_removed_platform(tmp_path):
	"""升级前以 zhilian 保存的 run 恢复时必须返回 INVALID_PARAM，不能去读遗留登录态或被兜底成可重试错误。"""
	from boss_agent_cli.wizard.store import WorkflowStore

	with WorkflowStore(tmp_path) as store:
		store.create(
			"legacy-zhilian-run",
			WorkflowPlan(
				role="candidate",
				platform="zhilian",
				goal="job_search",
				inputs={"query": "Python"},
				requested_steps=("auth_status", "candidate_search"),
				mode="headless",
			),
		)

	result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "--json", "wizard", "--resume", "legacy-zhilian-run"])

	assert result.exit_code == 1, result.output
	payload = json.loads(result.output)
	assert payload["ok"] is False
	assert payload["error"]["code"] == "INVALID_PARAM"
	assert "zhilian" in payload["error"]["message"]
	assert "可选: zhipin" in payload["error"]["message"]


def _ai_reply(payload):
	response = MagicMock()
	response.raise_for_status = MagicMock()
	response.json.return_value = payload
	return response


def _configure_ai(tmp_path, monkeypatch, **config):
	monkeypatch.setenv("BOSS_AGENT_MACHINE_ID", "test-machine")
	ResumeStore(tmp_path / "resumes").save(ResumeData(name="default", title="后端工程师"))
	store = AIConfigStore(tmp_path)
	store.save_config(**config)
	store.save_api_key("sk-test")


def test_ai_assist_uses_configured_temperature_and_max_tokens(tmp_path, monkeypatch):
	_configure_ai(tmp_path, monkeypatch, ai_provider="moonshot", ai_model="moonshot-v1-8k", ai_temperature=0.2, ai_max_tokens=1536)
	reply = _ai_reply({"choices": [{"message": {"content": "建议"}}]})

	with patch("boss_agent_cli.ai.service.httpx.post", return_value=reply) as post:
		result = DEFAULT_ACTIONS["ai_assist"](_action_context(tmp_path), {"resume": "default", "prompt": "优化"}, {})

	assert result.data["result"] == "建议"
	payload = post.call_args.kwargs["json"]
	assert payload["temperature"] == 0.2
	assert payload["max_tokens"] == 1536


def test_ai_assist_truncation_recovery_points_to_max_tokens(tmp_path, monkeypatch):
	_configure_ai(tmp_path, monkeypatch, ai_provider="openai", ai_model="gpt-5")
	reply = _ai_reply({"choices": [{"finish_reason": "length", "message": {"content": ""}}]})

	with patch("boss_agent_cli.ai.service.httpx.post", return_value=reply):
		with pytest.raises(WorkflowActionError) as exc_info:
			DEFAULT_ACTIONS["ai_assist"](_action_context(tmp_path), {"resume": "default", "prompt": "优化"}, {})

	assert exc_info.value.code == "AI_API_ERROR"
	assert "max_completion_tokens=4096" in str(exc_info.value)
	assert "--max-tokens" in exc_info.value.recovery_action


def test_runner_reports_deterministic_action_bug_as_internal_error(tmp_path):
	"""KeyError 之类的确定性错误重试也一样失败，不能报成可重试的 NETWORK_ERROR。"""

	def broken(context, inputs, prior):
		raise KeyError("jobList")

	with WorkflowStore(tmp_path) as store:
		run = WorkflowRunner(store, {"broken": broken}).run(_plan("broken"), object(), run_id="internal-run")
	assert run["error"]["code"] == "INTERNAL_ERROR"
	assert run["error"]["recoverable"] is False
	assert run["error"]["message"] == "KeyError: 'jobList'"


def test_runner_reports_local_file_error_as_local_io_error(tmp_path):
	"""导出路径不可写时重试同一 run_id 也不会成功，不能报成可重试的 NETWORK_ERROR。"""

	def broken(context, inputs, prior):
		raise PermissionError(13, "Permission denied", str(tmp_path / "export.json"))

	with WorkflowStore(tmp_path) as store:
		run = WorkflowRunner(store, {"broken": broken}).run(_plan("broken"), object(), run_id="io-run")
	assert run["error"]["code"] == "LOCAL_IO_ERROR"
	assert run["error"]["recoverable"] is False
	assert "不要自动重试" in run["error"]["recovery_action"]


# ── 本地记录损坏 ──────────────────────────────────────────────────────


def _break_workflow_column(
	tmp_path,
	table: str,
	column: str,
	run_id: str = "corrupt-run",
	step_name: str = "auth_status",
	*,
	value: str = "{not json",
):
	db_path = tmp_path / "cache" / "boss_agent.db"
	with sqlite3.connect(db_path) as conn:
		if table == "workflow_steps":
			conn.execute(
				f"UPDATE workflow_steps SET {column} = ? WHERE run_id = ? AND step_name = ?",
				(value, run_id, step_name),
			)
		else:
			conn.execute(f"UPDATE {table} SET {column} = ? WHERE run_id = ?", (value, run_id))


def _seed_corrupt_workflow_run(tmp_path, run_id: str = "corrupt-run") -> None:
	with WorkflowStore(tmp_path) as store:
		store.create(
			run_id,
			WorkflowPlan(
				role="candidate",
				platform="zhipin",
				goal="job_search",
				inputs={"query": "Python"},
				requested_steps=("auth_status", "candidate_search"),
				mode="headless",
			),
		)


@pytest.mark.parametrize("flag", [["--status"], ["--resume"], ["--stop"]])
@pytest.mark.parametrize("column", ["params", "last_result"])
def test_corrupt_workflow_run_reports_envelope(tmp_path, flag, column):
	"""workflow 记录写坏时不能 traceback，也不能被兜底成可重试的 NETWORK_ERROR。"""
	_seed_corrupt_workflow_run(tmp_path)
	_break_workflow_column(tmp_path, "workflow_runs", column)
	result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "--json", "wizard", *flag, "corrupt-run"])
	assert result.exit_code == 1, result.output
	payload = json.loads(result.output)
	assert payload["ok"] is False
	assert payload["error"]["code"] == "CACHE_RECORD_INVALID"
	assert payload["error"]["recoverable"] is True
	assert "workflow" in payload["error"]["message"]


def test_corrupt_workflow_step_reports_envelope(tmp_path):
	_seed_corrupt_workflow_run(tmp_path)
	_break_workflow_column(tmp_path, "workflow_steps", "result")
	result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "--json", "wizard", "--status", "corrupt-run"])
	assert result.exit_code == 1, result.output
	assert json.loads(result.output)["error"]["code"] == "CACHE_RECORD_INVALID"


def test_list_recent_skips_corrupt_runs_and_reports_them(tmp_path):
	"""一条坏记录不该让整个任务列表不可用：跳过并点名，交给用户重建。"""
	_seed_corrupt_workflow_run(tmp_path, "good-run")
	_seed_corrupt_workflow_run(tmp_path, "corrupt-run")
	_break_workflow_column(tmp_path, "workflow_runs", "params")
	with WorkflowStore(tmp_path) as store:
		runs = store.list_recent()
		corrupt = store.corrupt_run_ids
	assert [run["run_id"] for run in runs] == ["good-run"]
	assert corrupt == ["corrupt-run"]


def test_list_recent_skips_runs_with_corrupt_steps(tmp_path):
	_seed_corrupt_workflow_run(tmp_path, "good-run")
	_seed_corrupt_workflow_run(tmp_path, "corrupt-run")
	_break_workflow_column(tmp_path, "workflow_steps", "result")
	with WorkflowStore(tmp_path) as store:
		runs = store.list_recent()
		corrupt = store.corrupt_run_ids
	assert [run["run_id"] for run in runs] == ["good-run"]
	assert corrupt == ["corrupt-run"]


def test_structurally_drifted_workflow_params_reports_envelope(tmp_path):
	"""params 是合法 JSON 但不是 object 时，_plan_from_run 会抛 AttributeError。"""
	_seed_corrupt_workflow_run(tmp_path)
	_break_workflow_column(tmp_path, "workflow_runs", "params", value='"not-an-object"')
	result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "--json", "wizard", "--resume", "corrupt-run"])
	assert result.exit_code == 1, result.output
	payload = json.loads(result.output)
	assert payload["error"]["code"] == "CACHE_RECORD_INVALID"
	assert "JSON object" in payload["error"]["message"]


@pytest.mark.parametrize(
	("params", "field"),
	[
		({"inputs": [1, 2], "requested_steps": ["auth_status"]}, "inputs"),
		({"inputs": {"query": "Python"}, "requested_steps": "auth_status"}, "requested_steps"),
	],
)
def test_structurally_drifted_workflow_params_nested_fields_report_envelope(tmp_path, params, field):
	"""params 顶层是 object、但 inputs / requested_steps 结构漂移时同样不能 traceback。

	`dict(params["inputs"])` 对数组抛 TypeError（没有任何信封），
	`tuple("auth_status")` 则会把步骤名静默拆成单个字符。
	"""
	_seed_corrupt_workflow_run(tmp_path)
	_break_workflow_column(tmp_path, "workflow_runs", "params", value=json.dumps(params))
	result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "--json", "wizard", "--resume", "corrupt-run"])
	assert result.exit_code == 1, result.output
	payload = json.loads(result.output)
	assert payload["error"]["code"] == "CACHE_RECORD_INVALID"
	assert payload["error"]["recoverable"] is True
	assert field in payload["error"]["message"]


# ── 布尔输入类型校验（Issue #945）────────────────────────────────────


def test_bool_input_only_accepts_json_booleans():
	"""字符串与数字不能被当成 true；null 与缺省使用默认值。"""
	from boss_agent_cli.wizard.actions import _bool_input

	assert _bool_input({}, "flag") is False
	assert _bool_input({"flag": None}, "flag") is False
	assert _bool_input({"flag": True}, "flag") is True
	assert _bool_input({"flag": False}, "flag", default=True) is False
	for raw in ("false", "0", 1, 0, [], {}):
		with pytest.raises(WorkflowInputError) as exc_info:
			_bool_input({"flag": raw}, "flag")
		assert "flag" in str(exc_info.value)
		assert "布尔" in str(exc_info.value)


def test_candidate_mark_rejects_string_remove_before_platform_calls(tmp_path):
	""""false" 不能被当成 true 删除标签；非法输入不能先查联系人。"""
	platform = _FakeCandidatePlatform()
	context = _action_context(tmp_path, candidate=platform)

	with pytest.raises(WorkflowInputError):
		DEFAULT_ACTIONS["candidate_mark"](
			context, {"security_id": "sid_current", "label": "沟通中", "remove": "false"}, {}
		)

	assert platform.calls == []

	# 真正的布尔 false 仍然表示「添加标签」。
	DEFAULT_ACTIONS["candidate_mark"](
		context, {"security_id": "sid_current", "label": "沟通中", "remove": False}, {}
	)
	assert ("friend_label", "10001", 2, 0, False) in platform.calls


def test_candidate_export_rejects_string_include_private_before_io(tmp_path):
	"""未脱敏导出是隐私开关：字符串 "false" 必须在任何搜索 / 写文件之前被拒绝。"""
	context = _action_context(tmp_path)

	with pytest.raises(WorkflowInputError):
		DEFAULT_ACTIONS["candidate_export"](context, {"query": "Python", "include_private": "false"}, {})

	assert not (tmp_path / "exports").exists()


def test_recruiter_candidates_rejects_string_select_before_request(tmp_path):
	"""字符串 "false" 不能把 select=true 发给平台。"""
	platform = _FakeRecruiterPlatform()
	context = _action_context(tmp_path, role="recruiter", recruiter=platform)

	with pytest.raises(WorkflowInputError):
		DEFAULT_ACTIONS["recruiter_candidates"](context, {"query": "Python", "select": "false"}, {})

	assert platform.calls == []


def test_crawl_actions_reject_string_with_detail(tmp_path):
	"""字符串 "false" 不能扩大采集范围。"""
	context = _action_context(tmp_path)

	with pytest.raises(WorkflowInputError):
		DEFAULT_ACTIONS["crawl_start"](context, {"query": "Python", "city": "北京", "with_detail": "false"}, {})
	with pytest.raises(WorkflowInputError):
		DEFAULT_ACTIONS["crawl_resume"](context, {"run_id": "run-1", "with_detail": "false"}, {})
