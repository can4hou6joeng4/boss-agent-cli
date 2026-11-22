"""审计批次 132：向导动作对空白标识 / 空消息与 CLI 同口径拒绝（Issue #2516）。

`candidate_apply` / `candidate_greet` 早已用非空白校验（Issue #2320），但
`candidate_detail`、`recruiter_jobs_detail/online/offline` 与 `recruiter_reply`
仍用只查类型的 `_required_str`：纯空白值会作为真实参数发到平台，其中空消息
会真的发给候选人。CLI 对应命令都在入口报 ``INVALID_PARAM``。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from boss_agent_cli.output import Logger
from boss_agent_cli.wizard.actions import DEFAULT_ACTIONS, ActionContext
from boss_agent_cli.wizard.runner import WorkflowActionError


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

	def job_online(self, job_id: str) -> dict[str, object]:
		self.calls.append(("job_online", job_id))
		return {"code": 0, "data": {"online": True}}

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
		logger=Logger(),
		candidate_factory=_forbidden_factory,
		recruiter_factory=(lambda *args, **kwargs: recruiter) if recruiter is not None else _forbidden_factory,
	)


def test_candidate_detail_rejects_blank_security_id(tmp_path: Path) -> None:
	with pytest.raises(WorkflowActionError) as exc_info:
		DEFAULT_ACTIONS["candidate_detail"](_context(tmp_path), {"security_id": "   "}, {})
	assert exc_info.value.code == "INVALID_PARAM"
	assert "security_id" in str(exc_info.value)


@pytest.mark.parametrize("action", ["recruiter_jobs_detail", "recruiter_jobs_online", "recruiter_jobs_offline"])
def test_recruiter_jobs_reject_blank_job_id(tmp_path: Path, action: str) -> None:
	with pytest.raises(WorkflowActionError) as exc_info:
		DEFAULT_ACTIONS[action](_context(tmp_path), {"job_id": "   "}, {})
	assert exc_info.value.code == "INVALID_PARAM"
	assert "job_id" in str(exc_info.value)


def test_recruiter_reply_rejects_blank_message(tmp_path: Path) -> None:
	with pytest.raises(WorkflowActionError) as exc_info:
		DEFAULT_ACTIONS["recruiter_reply"](_context(tmp_path), {"friend_id": 42, "message": "   "}, {})
	assert exc_info.value.code == "INVALID_PARAM"
	assert "message" in str(exc_info.value)


def test_recruiter_reply_preserves_nonblank_message(tmp_path: Path) -> None:
	platform = _FakeRecruiterPlatform()

	result = DEFAULT_ACTIONS["recruiter_reply"](
		_context(tmp_path, recruiter=platform), {"friend_id": 42, "message": " 你好 "}, {}
	)

	assert result.data["result"]["sent"] is True
	assert platform.calls == [("reply", 42, " 你好 ")]


def test_jobs_online_preserves_padded_job_id(tmp_path: Path) -> None:
	platform = _FakeRecruiterPlatform()

	DEFAULT_ACTIONS["recruiter_jobs_online"](_context(tmp_path, recruiter=platform), {"job_id": " job-1 "}, {})

	assert platform.calls == [("job_online", " job-1 ")]
