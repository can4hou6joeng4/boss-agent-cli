"""向导招聘者写动作在编码失败前拒绝输入（Issue #3017）。

`recruiter_resume` / `recruiter_reply` 此前把孤立代理项直接交给写请求：客户端
在发出第一个字节前抛 ``RequestTextEncodingError``，但写标记已落盘，runner 报成
``NETWORK_ERROR`` 结果未知并禁止 ``--resume`` 重放。这里验证入口按 CLI 同口径
返回 ``INVALID_PARAM``、不留写标记，并可在修正输入后恢复。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from boss_agent_cli.output import Logger
from boss_agent_cli.wizard.actions import DEFAULT_ACTIONS, ActionContext
from boss_agent_cli.wizard.models import WorkflowPlan
from boss_agent_cli.wizard.runner import WorkflowActionError, WorkflowRunner
from boss_agent_cli.wizard.store import WorkflowStore

LONE_SURROGATE = "\udcff"


class _FakeRecruiterPlatform:
	def __init__(self) -> None:
		self.calls: list[tuple[object, ...]] = []

	def __enter__(self) -> "_FakeRecruiterPlatform":
		return self

	def __exit__(self, *args: object) -> None:
		return None

	def send_message_by_friend(self, friend_id: int, message: str) -> dict[str, object]:
		self.calls.append(("reply", friend_id, message))
		return {"code": 0, "data": {"sent": True}}

	def view_geek(self, geek_id: str, job_id: str, security_id: str | None) -> dict[str, object]:
		self.calls.append(("view_geek", geek_id, job_id, security_id))
		return {"code": 0, "data": {"geek": {"id": geek_id}}}

	def is_success(self, response: dict[str, object]) -> bool:
		return response["code"] == 0

	def unwrap_data(self, response: dict[str, object]) -> object:
		return response["data"]


def _forbidden_factory(*args: object, **kwargs: object) -> object:
	raise AssertionError("非法输入不应构造平台实例")


def _context(tmp_path: Path, *, recruiter: object | None = None) -> ActionContext:
	return ActionContext(
		data_dir=tmp_path,
		platform="zhipin",
		role="recruiter",
		logger=Logger("error"),
		candidate_factory=_forbidden_factory,
		recruiter_factory=(lambda *args, **kwargs: recruiter) if recruiter is not None else _forbidden_factory,
	)


@pytest.mark.parametrize(
	"inputs",
	[
		{"geek_id": LONE_SURROGATE, "job_id": "job-1"},
		{"geek_id": "geek-1", "job_id": LONE_SURROGATE},
		{"geek_id": "geek-1", "job_id": "job-1", "security_id": LONE_SURROGATE},
	],
	ids=["geek_id", "job_id", "security_id"],
)
def test_recruiter_resume_rejects_unencodable_ids_before_platform(tmp_path: Path, inputs: dict[str, object]) -> None:
	with pytest.raises(WorkflowActionError) as exc_info:
		DEFAULT_ACTIONS["recruiter_resume"](_context(tmp_path), inputs, {})

	error = exc_info.value
	assert error.code == "INVALID_PARAM"
	assert error.replay_safe is True
	assert "未发出" in str(error)
	assert "UTF-8" in str(error)


def test_recruiter_reply_rejects_unencodable_message_before_platform(tmp_path: Path) -> None:
	with pytest.raises(WorkflowActionError) as exc_info:
		DEFAULT_ACTIONS["recruiter_reply"](_context(tmp_path), {"friend_id": 42, "message": f"你好{LONE_SURROGATE}"}, {})

	error = exc_info.value
	assert error.code == "INVALID_PARAM"
	assert error.replay_safe is True
	assert "message" in str(error)
	assert "未发出" in str(error)


def test_recruiter_reply_still_sends_encodable_message(tmp_path: Path) -> None:
	platform = _FakeRecruiterPlatform()
	result = DEFAULT_ACTIONS["recruiter_reply"](
		_context(tmp_path, recruiter=platform), {"friend_id": 42, "message": " 你好 "}, {}
	)

	assert result.data["result"]["sent"] is True
	assert platform.calls == [("reply", 42, " 你好 ")]


def _reply_plan(message: str) -> WorkflowPlan:
	return WorkflowPlan(
		role="recruiter",
		platform="zhipin",
		goal="offline",
		inputs={"friend_id": 42, "message": message},
		requested_steps=("recruiter_reply",),
		mode="headless",
	)


def _run_reply(tmp_path: Path, message: str) -> dict[str, object]:
	with WorkflowStore(tmp_path) as store:
		return WorkflowRunner(store, DEFAULT_ACTIONS).run(
			_reply_plan(message), _context(tmp_path, recruiter=_FakeRecruiterPlatform()), run_id="run-encoding"
		)


def test_runner_reports_invalid_param_without_write_marker(tmp_path: Path) -> None:
	run = _run_reply(tmp_path, f"你好{LONE_SURROGATE}")

	assert run["status"] == "failed"
	[step] = run["steps"]
	assert step["status"] == "failed"
	error = step["error"]
	assert error["code"] == "INVALID_PARAM"
	assert "未发出" in error["message"]
	# 没有落盘「结果未知」标记：既不能报成 outcome_unknown，也不能禁止恢复。
	assert error.get("replay_safe") is not False
	assert error.get("outcome_unknown") is not True


def test_corrected_input_runs_in_a_new_workflow(tmp_path: Path) -> None:
	"""同一 run 的输入已绑定，修正后按恢复指引新建 workflow（Issue #3017）。"""
	_run_reply(tmp_path, f"你好{LONE_SURROGATE}")

	platform = _FakeRecruiterPlatform()
	context = _context(tmp_path, recruiter=platform)
	with WorkflowStore(tmp_path) as store:
		run = WorkflowRunner(store, DEFAULT_ACTIONS).run(_reply_plan("你好"), context, run_id="run-encoding-fixed")

	assert run["status"] == "completed"
	assert platform.calls == [("reply", 42, "你好")]
