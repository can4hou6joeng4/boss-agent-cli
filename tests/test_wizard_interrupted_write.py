"""写步骤执行到一半、执行器没来得及记录结果就消失之后，恢复不得重放该写操作。"""

from __future__ import annotations

import ast
import json
import shlex
import sqlite3
import subprocess
import sys
import textwrap
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner

from boss_agent_cli.main import cli
from boss_agent_cli.output import Logger
from boss_agent_cli.platforms.zhipin import BossPlatform
from boss_agent_cli.platforms.zhipin_recruiter import BossRecruiterPlatform
from boss_agent_cli.wizard import actions as wizard_actions
from boss_agent_cli.wizard.actions import ActionContext, DEFAULT_ACTIONS
from boss_agent_cli.wizard.catalog import build_plan
from boss_agent_cli.wizard.models import StepResult, WizardInput, WorkflowPlan
from boss_agent_cli.wizard.runner import WorkflowRunner
from boss_agent_cli.wizard.store import WorkflowStore

ENVELOPE_KEYS = {"ok", "schema_version", "command", "data", "pagination", "error", "hints"}

_REPLY_INPUT = {
	"role": "recruiter", "platform": "zhipin", "goal": "reply",
	"inputs": {"friend_id": 42, "message": "offline fixture"},
	"requested_steps": ["recruiter_reply"],
}

# 全部写动作：(角色, 步骤, 客户端方法, 输入, 核对命令)。
_WRITE_ACTIONS = [
	("candidate", "candidate_apply", "apply", {"security_id": "synthetic-sid", "job_id": "synthetic-job"}, ["me", "--section", "deliver"]),
	("candidate", "candidate_greet", "greet", {"security_id": "synthetic-sid", "job_id": "synthetic-job"}, ["chat"]),
	("candidate", "candidate_exchange", "exchange_contact", {"security_id": "sid_current"}, ["chat"]),
	("candidate", "candidate_mark", "friend_label", {"security_id": "sid_current", "label": "沟通中"}, ["chat"]),
	("recruiter", "recruiter_reply", "send_message_by_friend", {"friend_id": 42, "message": "你好"}, ["hr", "chatmsg", "42"]),
	("recruiter", "recruiter_request_resume", "exchange_request_by_friend", {"friend_id": 42}, ["hr", "chatmsg", "42"]),
	("recruiter", "recruiter_exchange_contact", "exchange_request_by_friend", {"friend_id": 42}, ["hr", "chatmsg", "42"]),
	("recruiter", "recruiter_jobs_online", "job_online", {"job_id": "synthetic-job"}, ["hr", "jobs", "list"]),
	("recruiter", "recruiter_jobs_offline", "job_offline", {"job_id": "synthetic-job"}, ["hr", "jobs", "list"]),
	("recruiter", "recruiter_resume", "view_geek", {"geek_id": "synthetic-geek", "job_id": "synthetic-job"}, ["hr", "chat"]),
]

_FRIEND_LIST = {
	"code": 0,
	"zpData": {
		"result": [{"uid": 10001, "securityId": "sid_current", "name": "测试联系人", "friendSource": 0}],
		"hasMore": False,
	},
}


class _ExecutorGone(BaseException):
	"""执行器在这一刻消失。

	runner 只接管 ``Exception`` 与 ``KeyboardInterrupt``：这个异常穿出去之后，本地
	留下的状态与进程被 kill 时一致——步骤停在 running，没有任何结果被记录。
	"""


def _raise_executor_gone():
	raise _ExecutorGone()


def _plan(role, inputs, *steps):
	return WorkflowPlan(
		role=role, platform="zhipin", goal="offline", inputs=inputs, requested_steps=steps, mode="headless",
	)


def _context(data_dir, role, platform):
	return ActionContext(
		data_dir=data_dir, platform="zhipin", role=role, logger=Logger("error"),
		candidate_factory=lambda *args: platform,
		recruiter_factory=lambda *args: platform,
	)


def _adapter(role, client):
	return BossPlatform(client) if role == "candidate" else BossRecruiterPlatform(client)


def _check_command(data_dir, check_args):
	return shlex.join(["boss", "--data-dir", str(data_dir.resolve()), *check_args])


class _OfflineRecruiter:
	"""不经真实适配器的招聘者替身：响应按顺序给出，发送逐条记录。"""

	def __init__(self, responses, on_send=None):
		self.responses = list(responses)
		self.sends = []
		self._on_send = on_send

	def __enter__(self):
		return self

	def __exit__(self, *args):
		pass

	def send_message_by_friend(self, friend_id, message):
		self.sends.append((friend_id, message))
		if self._on_send is not None:
			self._on_send()
		return self.responses.pop(0)

	def is_success(self, response):
		return response["code"] == 0

	def unwrap_data(self, response):
		return response["data"]

	def parse_error(self, response):
		return response["error"], response["message"]


_SENT = {"code": 0, "data": {"sent": True}}
_RATE_LIMITED = {"code": 9, "error": "RATE_LIMITED", "message": "offline rate limit"}

_KILLED_EXECUTOR = textwrap.dedent("""\
import json
import os
import sys
from pathlib import Path
from boss_agent_cli.output import Logger
from boss_agent_cli.wizard.actions import ActionContext, DEFAULT_ACTIONS
from boss_agent_cli.wizard.catalog import build_plan
from boss_agent_cli.wizard.models import WizardInput
from boss_agent_cli.wizard.runner import WorkflowRunner
from boss_agent_cli.wizard.store import WorkflowStore

data_dir = Path(sys.argv[1])


class OfflineRecruiter:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def send_message_by_friend(self, friend_id, message):
        with (data_dir / "sends.log").open("a", encoding="utf-8") as handle:
            handle.write(f"{friend_id}\\n")
        # 请求已经发出，进程在拿到响应之前被杀：不执行任何 finally。
        os._exit(137)


plan = build_plan(WizardInput.from_mapping(json.loads(sys.argv[2])))
context = ActionContext(
    data_dir, "zhipin", "recruiter", Logger("error"), recruiter_factory=lambda *args: OfflineRecruiter(),
)
with WorkflowStore(data_dir) as store:
    WorkflowRunner(store, DEFAULT_ACTIONS).run(plan, context, run_id="run-killed")
""")


def test_resume_after_the_executor_was_killed_mid_reply_does_not_send_again(tmp_path, monkeypatch):
	"""真实进程在发送请求发出后被杀，随后经 CLI 恢复同一个 run。"""
	child = subprocess.run(
		[sys.executable, "-c", _KILLED_EXECUTOR, str(tmp_path), json.dumps(_REPLY_INPUT)],
		capture_output=True, text=True, timeout=60,
	)
	assert child.returncode == 137, child.stderr
	sends = tmp_path / "sends.log"
	assert sends.read_text(encoding="utf-8").splitlines() == ["42"]

	check_command = _check_command(tmp_path, ["hr", "chatmsg", "42"])
	with WorkflowStore(tmp_path) as store:
		orphan = store.get("run-killed")
	assert orphan["status"] == "running"
	[step] = orphan["steps"]
	assert step["status"] == "running"
	# 进程没有机会在死前写任何东西：这条记录是在请求发出之前落盘的。
	assert step["error"]["code"] == "ACTION_UNCONFIRMED"
	assert step["error"]["replay_safe"] is False
	assert check_command in step["error"]["recovery_action"]

	replayed = []
	monkeypatch.setitem(
		DEFAULT_ACTIONS, "recruiter_reply", lambda *args: replayed.append(args) or StepResult({"offline": True}),
	)
	resume = ["--data-dir", str(tmp_path), "--json", "wizard", "--resume", "run-killed"]
	for _ in range(2):
		# 第一次恢复把悬空的步骤落成失败；之后的恢复返回同一个失败。
		result = CliRunner().invoke(cli, resume)
		assert replayed == []
		assert result.exit_code == 1
		payload = json.loads(result.stdout)
		assert set(payload) == ENVELOPE_KEYS
		assert payload["ok"] is False
		error = payload["error"]
		assert error["code"] == "ACTION_UNCONFIRMED"
		assert error["recoverable"] is False
		assert check_command in error["recovery_action"]
		assert "不要自动重试" in error["recovery_action"]
		assert error["details"]["replay_safe"] is False
		assert error["details"]["outcome_unknown"] is True
		run = error["details"]["run"]
		assert run["status"] == "failed"
		assert run["current_step"] == "recruiter_reply"
		assert run["steps"][0]["status"] == "failed"
		assert run["steps"][0]["error"]["code"] == "ACTION_UNCONFIRMED"

	status = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "--json", "wizard", "--status", "run-killed"])
	assert status.exit_code == 0
	assert json.loads(status.stdout)["data"]["status"] == "failed"
	assert sends.read_text(encoding="utf-8").splitlines() == ["42"]


@pytest.mark.parametrize(("role", "action", "method", "inputs", "check_args"), _WRITE_ACTIONS)
def test_write_interrupted_after_dispatch_is_never_replayed(tmp_path, role, action, method, inputs, check_args):
	"""真实适配器连接默认动作与 runner：每个写动作都带着自己的核对命令被拦下。"""
	data_dir = tmp_path / "wizard profile"
	client = MagicMock()
	client.friend_list.return_value = _FRIEND_LIST
	getattr(client, method).side_effect = _ExecutorGone()
	context = _context(data_dir, role, _adapter(role, client))
	plan = _plan(role, inputs, action)
	events = []

	with WorkflowStore(data_dir) as store:
		runner = WorkflowRunner(store, DEFAULT_ACTIONS)
		with pytest.raises(_ExecutorGone):
			runner.run(plan, context, run_id="run-gone")
		orphan = store.get("run-gone")
		assert orphan["status"] == "running"
		assert orphan["steps"][0]["status"] == "running"

		getattr(client, method).side_effect = None
		getattr(client, method).return_value = {"code": 0, "zpData": {}}
		resumed = runner.run(
			plan, context, run_id="run-gone", max_retries=2, on_event=lambda *args: events.append(args),
		)
		again = runner.run(plan, context, run_id="run-gone", max_retries=2)

	getattr(client, method).assert_called_once()
	assert events == []
	check_command = _check_command(data_dir, check_args)
	for run in (resumed, again):
		assert run["status"] == "failed"
		assert run["current_step"] == action
		[step] = run["steps"]
		assert step["status"] == "failed"
		assert step["finished_at"] is not None
		for error in (run["error"], step["error"]):
			assert error["code"] == "ACTION_UNCONFIRMED"
			assert error["recoverable"] is False
			assert error["replay_safe"] is False
			assert "中断" in error["message"]
			assert check_command in error["recovery_action"]
			assert "不要自动重试" in error["recovery_action"]
			if role == "candidate":
				assert "hr chatmsg" not in error["recovery_action"]
	assert again == resumed


def test_interruption_before_the_write_request_is_still_resumable(tmp_path):
	"""同一步骤里的前置读取还没结束：写请求没有发出，恢复照常执行。"""
	client = MagicMock()
	client.friend_list.side_effect = _ExecutorGone()
	client.exchange_contact.return_value = {"code": 0, "zpData": {}}
	context = _context(tmp_path, "candidate", _adapter("candidate", client))
	plan = _plan("candidate", {"security_id": "sid_current"}, "candidate_exchange")

	with WorkflowStore(tmp_path) as store:
		runner = WorkflowRunner(store, DEFAULT_ACTIONS)
		with pytest.raises(_ExecutorGone):
			runner.run(plan, context, run_id="run-early")
		[step] = store.get("run-early")["steps"]
		assert step["status"] == "running"
		assert step["error"] is None
		client.exchange_contact.assert_not_called()

		client.friend_list.side_effect = None
		client.friend_list.return_value = _FRIEND_LIST
		resumed = runner.run(plan, context, run_id="run-early")

	assert resumed["status"] == "completed"
	client.exchange_contact.assert_called_once()


def test_interrupted_read_step_is_replayed(tmp_path):
	client = MagicMock()
	client.friend_list.side_effect = _ExecutorGone()
	context = _context(tmp_path, "candidate", _adapter("candidate", client))
	plan = _plan("candidate", {}, "candidate_chat")

	with WorkflowStore(tmp_path) as store:
		runner = WorkflowRunner(store, DEFAULT_ACTIONS)
		with pytest.raises(_ExecutorGone):
			runner.run(plan, context, run_id="run-read")
		[step] = store.get("run-read")["steps"]
		assert step["status"] == "running"
		assert step["error"] is None

		client.friend_list.side_effect = None
		client.friend_list.return_value = _FRIEND_LIST
		resumed = runner.run(plan, context, run_id="run-read")

	assert resumed["status"] == "completed"
	assert client.friend_list.call_count == 2


def test_unconfirmed_record_precedes_the_request_and_is_replaced_by_the_result(tmp_path):
	in_flight = []

	def observe():
		# 另开一个连接：看到的是已经提交到磁盘的状态，而不是执行器内存里的。
		with WorkflowStore(tmp_path) as observer:
			in_flight.append(observer.get("run-ordered"))

	recruiter = _OfflineRecruiter([_SENT], on_send=observe)
	context = _context(tmp_path, "recruiter", recruiter)
	with WorkflowStore(tmp_path) as store:
		run = WorkflowRunner(store, DEFAULT_ACTIONS).run(build_plan(WizardInput.from_mapping(_REPLY_INPUT)), context, run_id="run-ordered")

	[observed] = in_flight
	assert observed["status"] == "running"
	assert observed["error"] is None
	[step] = observed["steps"]
	assert step["status"] == "running"
	assert step["error"]["code"] == "ACTION_UNCONFIRMED"
	assert step["error"]["replay_safe"] is False
	assert "中断" not in step["error"]["message"]
	assert _check_command(tmp_path, ["hr", "chatmsg", "42"]) in step["error"]["recovery_action"]

	assert run["status"] == "completed"
	assert run["error"] is None
	assert run["steps"][0]["error"] is None
	assert run["steps"][0]["result"]["data"]["result"] == {"sent": True}
	assert len(recruiter.sends) == 1


def test_reported_replayable_failure_replaces_the_unconfirmed_record(tmp_path):
	"""平台明确拒绝（限流）是已知结果：不是「结果未知」，恢复可以再发。"""
	recruiter = _OfflineRecruiter([_RATE_LIMITED, _SENT])
	context = _context(tmp_path, "recruiter", recruiter)
	plan = build_plan(WizardInput.from_mapping(_REPLY_INPUT))

	with WorkflowStore(tmp_path) as store:
		runner = WorkflowRunner(store, DEFAULT_ACTIONS)
		first = runner.run(plan, context, run_id="run-limited")
		assert first["status"] == "failed"
		assert first["steps"][0]["error"]["code"] == "RATE_LIMITED"
		assert "replay_safe" not in first["steps"][0]["error"]
		second = runner.run(plan, context, run_id="run-limited")

	assert second["status"] == "completed"
	assert len(recruiter.sends) == 2


def test_interruption_between_auto_retry_attempts_is_still_resumable(tmp_path):
	"""第一次尝试被平台明确拒绝后自动重试；第二次尝试还没发出请求执行器就消失了。

	上一次尝试预先落盘的记录必须随它的已知结果一起作废，否则这次什么都没发出的
	中断会被当成结果未知，run 再也恢复不了。
	"""
	recruiter = _OfflineRecruiter([_RATE_LIMITED, _SENT])
	constructions = []

	def factory(*args):
		constructions.append(args)
		if len(constructions) == 2:
			raise _ExecutorGone()
		return recruiter

	context = ActionContext(tmp_path, "zhipin", "recruiter", Logger("error"), recruiter_factory=factory)
	plan = build_plan(WizardInput.from_mapping(_REPLY_INPUT))

	with WorkflowStore(tmp_path) as store:
		runner = WorkflowRunner(store, DEFAULT_ACTIONS)
		with pytest.raises(_ExecutorGone):
			runner.run(plan, context, run_id="run-retry", max_retries=1)
		assert len(recruiter.sends) == 1
		[step] = store.get("run-retry")["steps"]
		assert step["status"] == "running"
		assert step["error"] is None
		resumed = runner.run(plan, context, run_id="run-retry", max_retries=1)

	assert resumed["status"] == "completed"
	assert len(recruiter.sends) == 2


def test_stopping_an_orphaned_run_keeps_the_unconfirmed_record(tmp_path):
	"""悬空的 run 被 --stop 之后是终态：不重放，步骤上的记录仍在，状态查询看得到。"""
	recruiter = _OfflineRecruiter([_SENT, _SENT], on_send=_raise_executor_gone)
	context = _context(tmp_path, "recruiter", recruiter)
	plan = build_plan(WizardInput.from_mapping(_REPLY_INPUT))

	with WorkflowStore(tmp_path) as store:
		runner = WorkflowRunner(store, DEFAULT_ACTIONS)
		with pytest.raises(_ExecutorGone):
			runner.run(plan, context, run_id="run-stopped")
		assert store.request_stop("run-stopped")
		resumed = runner.run(plan, context, run_id="run-stopped")

	assert resumed["status"] == "stopped"
	assert resumed["steps"][0]["error"]["code"] == "ACTION_UNCONFIRMED"
	assert resumed["steps"][0]["error"]["replay_safe"] is False
	assert len(recruiter.sends) == 1


def test_steps_completed_before_the_interrupted_write_are_kept(tmp_path):
	reads = []

	def read(context, inputs, prior):
		reads.append(inputs)
		return StepResult({"offline": True})

	recruiter = _OfflineRecruiter([_SENT, _SENT], on_send=_raise_executor_gone)
	context = _context(tmp_path, "recruiter", recruiter)
	plan = _plan("recruiter", {"friend_id": 42, "message": "offline fixture"}, "read", "recruiter_reply")
	actions = {"read": read, "recruiter_reply": DEFAULT_ACTIONS["recruiter_reply"]}

	with WorkflowStore(tmp_path) as store:
		runner = WorkflowRunner(store, actions)
		with pytest.raises(_ExecutorGone):
			runner.run(plan, context, run_id="run-two-steps")
		resumed = runner.run(plan, context, run_id="run-two-steps")

	assert resumed["status"] == "failed"
	assert resumed["current_step"] == "recruiter_reply"
	assert resumed["error"]["code"] == "ACTION_UNCONFIRMED"
	assert [step["status"] for step in resumed["steps"]] == ["completed", "failed"]
	assert resumed["steps"][0]["result"]["data"] == {"offline": True}
	assert len(reads) == 1
	assert len(recruiter.sends) == 1


def test_write_is_not_sent_when_the_record_cannot_be_saved(tmp_path, monkeypatch):
	"""落不了盘就不发：否则这次写操作在执行器消失后无迹可查。"""
	recruiter = _OfflineRecruiter([_SENT])
	context = _context(tmp_path, "recruiter", recruiter)

	def locked(self, run_id, step_name, error):
		raise sqlite3.OperationalError("database is locked")

	monkeypatch.setattr(WorkflowStore, "set_step_error", locked)
	with WorkflowStore(tmp_path) as store:
		run = WorkflowRunner(store, DEFAULT_ACTIONS).run(
			build_plan(WizardInput.from_mapping(_REPLY_INPUT)), context, run_id="run-unrecorded",
		)

	assert recruiter.sends == []
	assert run["status"] == "failed"
	# 什么都没发出：这次失败可以重放。
	assert "replay_safe" not in run["steps"][0]["error"]


def test_write_is_not_sent_when_the_step_row_is_missing(tmp_path):
	"""记录没有落到任何一行上同样算没记下来。"""
	recruiter = _OfflineRecruiter([_SENT])
	context = _context(tmp_path, "recruiter", recruiter)

	def unbound(action_context, inputs, prior):
		control = action_context.workflow_control
		stray = type(control)(control.run_id, "no-such-step", control.store)
		return DEFAULT_ACTIONS["recruiter_reply"](action_context.with_workflow_control(stray), inputs, prior)

	plan = _plan("recruiter", {"friend_id": 42, "message": "offline fixture"}, "write")
	with WorkflowStore(tmp_path) as store:
		run = WorkflowRunner(store, {"write": unbound}).run(plan, context, run_id="run-unbound")

	assert recruiter.sends == []
	assert run["status"] == "failed"


def test_in_flight_record_only_touches_the_step_error(tmp_path):
	"""写前记录不能动状态、结果（含内部任务绑定）与时间戳。"""
	with WorkflowStore(tmp_path) as store:
		store.create("run-record", _plan("candidate", {}, "candidate_chat"))
		assert store.update_step("run-record", "candidate_chat", status="running", result={"inner_run_id": "crawl-1"})
		before = store.get("run-record")["steps"][0]
		assert before["result"] == {"inner_run_id": "crawl-1"}

		assert store.set_step_error("run-record", "candidate_chat", {"code": "ACTION_UNCONFIRMED", "replay_safe": False})
		recorded = store.get("run-record")["steps"][0]
		assert recorded == {**before, "error": {"code": "ACTION_UNCONFIRMED", "replay_safe": False}}

		assert store.set_step_error("run-record", "candidate_chat", None)
		assert store.get("run-record")["steps"][0] == before
		# 不存在的步骤返回 False，调用方据此拒绝发请求。
		assert store.set_step_error("run-record", "no-such-step", {"code": "ACTION_UNCONFIRMED"}) is False


def test_write_actions_work_without_a_workflow_control(tmp_path):
	"""动作被直接调用（不经 runner）时没有步骤可记，照常执行。"""
	recruiter = _OfflineRecruiter([_SENT])
	context = _context(tmp_path, "recruiter", recruiter)
	result = DEFAULT_ACTIONS["recruiter_reply"](context, {"friend_id": 42, "message": "offline fixture"}, {})
	assert result.data["result"] == {"sent": True}
	assert len(recruiter.sends) == 1


def _write_unwrap_callers():
	"""actions.py 里所有以写语义解包响应的调用点，按所在函数归类。"""
	tree = ast.parse(Path(wizard_actions.__file__).read_text(encoding="utf-8"))
	callers = []
	for function in ast.walk(tree):
		if not isinstance(function, ast.FunctionDef):
			continue
		for node in ast.walk(function):
			if (
				isinstance(node, ast.Call)
				and isinstance(node.func, ast.Name)
				and node.func.id == "_unwrap"
				and any(keyword.arg == "write" for keyword in node.keywords)
			):
				callers.append(function.name)
	return callers


def test_write_semantics_are_only_reachable_through_the_recording_helper():
	"""结构门禁：写语义的解包只有一个入口，而这个入口先落盘再发请求。

	新的写动作若照抄 ``_unwrap(platform, platform.xxx(...), write=True)``，请求会在
	没有任何记录的情况下发出，执行器消失后恢复就会重放它。
	"""
	assert _write_unwrap_callers() == ["_write_call"]
