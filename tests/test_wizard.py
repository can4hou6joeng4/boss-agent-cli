import json
import os
import pty
import select
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import click
import pytest
from click.testing import CliRunner

from boss_agent_cli.cache.store import CacheStore
from boss_agent_cli.crawler.service import CrawlOutcome
from boss_agent_cli.resume.models import ResumeData
from boss_agent_cli.resume.store import ResumeStore
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
	_main_menu_options,
	ask_continue_session,
	collect_entity_follow_up,
	collect_result_follow_up,
	collect_wizard_input,
)


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
					"role": "recruiter",
					"platform": "zhilian",
					"goal": "jobs_list",
					"inputs": {},
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
		return SimpleNamespace(items=[{"job_id": "job-1"}], has_more=True, total=27)

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
		return SimpleNamespace(items=[], has_more=False, total=0)

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


def test_placeholder_platform_fails_with_not_supported_before_auth(tmp_path):
	context = _action_context(tmp_path)
	context = ActionContext(
		data_dir=context.data_dir,
		platform="qiancheng",
		role="candidate",
		logger=context.logger,
	)
	with pytest.raises(WorkflowActionError) as exc_info:
		DEFAULT_ACTIONS["auth_status"](context, {}, {})
	assert exc_info.value.code == "NOT_SUPPORTED"
	assert exc_info.value.recoverable is True


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
		candidate_factory=(lambda name, auth, delay, cdp_url: candidate if candidate is not None else None),
		recruiter_factory=(lambda name, auth, delay, cdp_url: recruiter if recruiter is not None else None),
	)


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


def test_local_and_ai_actions_return_structured_recovery(tmp_path):
	context = _action_context(tmp_path)
	with CacheStore(tmp_path / "cache" / "boss_agent.db") as cache:
		cache.add_shortlist({"security_id": "sec-1", "job_id": "job-1", "title": "Backend"})
	shortlist = DEFAULT_ACTIONS["local_shortlist"](context, {}, {})
	assert shortlist.data["total"] == 1
	assert shortlist.data["items"][0]["title"] == "Backend"

	added = DEFAULT_ACTIONS["candidate_watch"](
		context,
		{"action": "add", "name": "daily", "query": "Python", "welfare": ["双休"]},
		{},
	)
	assert added.data["params"]["welfare"] == ["双休"]
	listed = DEFAULT_ACTIONS["candidate_watch"](context, {"action": "list"}, {})
	assert listed.data["items"][0]["name"] == "daily"

	ResumeStore(tmp_path / "resumes").save(ResumeData(name="default", title="后端工程师"))
	with pytest.raises(WorkflowActionError) as exc_info:
		DEFAULT_ACTIONS["ai_assist"](context, {"resume": "default", "prompt": "优化"}, {})
	assert exc_info.value.code == "AI_NOT_CONFIGURED"
	assert exc_info.value.recoverable is True


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
	assert status.data["checkpoint"]["resume_command"] == "boss crawl resume crawl-existing"
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
	assert last["next_action"] == "boss wizard --resume auth-required-run"
	assert any("boss --platform zhipin login" in action for action in last["operator_actions"])

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
	assert follow_up.inputs == {"security_id": "sec-friend", "gid": "gid-9"}
	assert "sec-friend" not in " ".join(option.label for _, options in menu.menus for option in options)


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
		startup_deadline = time.monotonic() + 5
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
			if "BOSS 求职助手" in output.decode("utf-8", errors="ignore"):
				break
		for _ in range(5):
			os.write(stderr_master_fd, b"\x1b[B")
			time.sleep(0.05)
		os.write(stderr_master_fd, b"\r")
		completion_deadline = time.monotonic() + 5
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
		if process.poll() is None:
			process.kill()
			pytest.fail("prompt_toolkit PTY did not exit after arrow-key selection")
		process.wait(timeout=2)
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

	stderr_text = output.decode("utf-8", errors="replace")
	assert process.returncode == 0
	assert stdout_output == b""
	assert "BOSS 求职助手" in stderr_text
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
	assert json.loads(stop.stdout)["error"]["code"] == "JOB_NOT_FOUND"


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
	assert last["next_action"] == "boss wizard --resume operator-actions-run"
	actions = last["operator_actions"]
	assert len(actions) == 2
	assert any("扫码" in action for action in actions)


def test_auth_status_without_workflow_control_falls_back(tmp_path):
	"""直接调 action（无 WorkflowControl）时 resume 命令要有兜底，不能抛 AttributeError。"""
	result = DEFAULT_ACTIONS["auth_status"](_action_context(tmp_path), {}, {})
	assert result.status is WorkflowStatus.WAITING_INPUT
	assert result.next_action == "boss wizard"
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
	assert "←/Esc 返回" in source


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
		lambda run: rendered.append(str(run.get("run_id"))),
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
		lambda run: rendered.append(str(run.get("run_id"))),
	)
	monkeypatch.setattr("boss_agent_cli.wizard.renderer.clear_wizard_screen", lambda: None)

	run = _crawl_completed_run(job_count=RESULT_PAGE_SIZE + 3)
	menu = _ScriptedMenu(["__result_more__", str(RESULT_PAGE_SIZE), "job_detail"])
	collect_result_follow_up(run, menu=menu)

	assert len(rendered) == 2, f"两页应各重绘一次摘要，实际 {len(rendered)} 次"


def test_result_list_menu_still_returns_selection(monkeypatch):
	"""回归护栏：加重绘不得改变选择结果。"""
	from boss_agent_cli.wizard.prompts import collect_result_follow_up

	monkeypatch.setattr("boss_agent_cli.wizard.renderer.render_run", lambda run: None)
	monkeypatch.setattr("boss_agent_cli.wizard.renderer.clear_wizard_screen", lambda: None)

	menu = _ScriptedMenu(["1", "job_detail"])
	follow = collect_result_follow_up(_crawl_completed_run(), menu=menu)

	assert isinstance(follow, WizardInput)
	assert follow.inputs["security_id"] == "sec-1"
