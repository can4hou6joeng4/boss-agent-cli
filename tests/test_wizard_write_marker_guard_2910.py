"""Issue #2910：写标记应覆盖所有异常分类，而不只是停止 / 聊天页分支。"""

from pathlib import Path
from typing import Any

import pytest

from boss_agent_cli.auth.manager import AuthRequired
from boss_agent_cli.output import Logger
from boss_agent_cli.wizard.actions import ActionContext, DEFAULT_ACTIONS
from boss_agent_cli.wizard.models import WorkflowPlan
from boss_agent_cli.wizard.runner import WorkflowActionError, WorkflowRunner
from boss_agent_cli.wizard.store import WorkflowStore

FRIEND_ID = 123
_AUTH_RESPONSE = {"code": -1, "parsed_code": "AUTH_EXPIRED", "message": "auth"}
_SUCCESS = {"code": 0, "zpData": {"ok": True}}


class _Platform:
	def __init__(self, first: Any) -> None:
		self.first = first
		self.calls = 0

	def _call(self) -> dict[str, Any]:
		self.calls += 1
		if self.first is not None:
			value, self.first = self.first, None
			if isinstance(value, BaseException):
				raise value
			return value
		return _SUCCESS

	def send_message_by_friend(self, friend_id: int, message: str) -> dict[str, Any]:
		return self._call()

	def chat_history(self, friend_id: int, count: int = 20, max_msg_id: int | None = None) -> dict[str, Any]:
		return self._call()

	def is_success(self, response: dict[str, Any]) -> bool:
		return response.get("code") == 0

	def parse_error(self, response: dict[str, Any]) -> tuple[str, str]:
		return response.get("parsed_code", "UNKNOWN"), response.get("message", "")

	def unwrap_data(self, response: dict[str, Any]) -> Any:
		return response.get("zpData") or {}

	def __enter__(self) -> "_Platform":
		return self

	def __exit__(self, *args: object) -> bool:
		return False


def _plan(goal: str) -> WorkflowPlan:
	return WorkflowPlan(
		role="recruiter",
		platform="zhipin",
		goal="offline",
		inputs={"friend_id": FRIEND_ID, "message": "hi"},
		requested_steps=(goal,),
		mode="headless",
	)


def _context(tmp_path: Path, platform: _Platform) -> ActionContext:
	return ActionContext(
		data_dir=tmp_path,
		platform="zhipin",
		role="recruiter",
		logger=Logger("error"),
		recruiter_factory=lambda *args, **kwargs: platform,
	)


@pytest.mark.parametrize("make_failure, expected_code", [
	(lambda: dict(_AUTH_RESPONSE), "AUTH_EXPIRED"),
	(lambda: AuthRequired("auth"), "AUTH_REQUIRED"),
	(lambda: WorkflowActionError("AUTH_EXPIRED", "auth", replay_safe=True), "AUTH_EXPIRED"),
], ids=["auth-response", "auth-exception", "typed-action-error"])
def test_write_marker_blocks_replay_for_auth_failures(
	tmp_path: Path, make_failure, expected_code: str
) -> None:
	"""写响应认证失败与 typed 认证异常都不能把写标记改成可重放。"""
	platform = _Platform(make_failure())
	context = _context(tmp_path, platform)

	with WorkflowStore(tmp_path) as store:
		runner = WorkflowRunner(store, DEFAULT_ACTIONS)
		first = runner.run(_plan("recruiter_reply"), context, run_id="run-auth", max_retries=0)
		assert first["status"] == "failed"
		assert first["error"]["code"] == expected_code
		assert first["error"]["replay_safe"] is False
		assert "写请求在途" in first["error"]["message"]
		assert "hr chatmsg 123" in first["error"]["recovery_action"]
		second = runner.run(_plan("recruiter_reply"), context, run_id="run-auth", max_retries=0)

	assert second["status"] == "failed"
	assert second["error"]["replay_safe"] is False
	assert platform.calls == 1


def test_read_only_auth_failure_stays_replayable(tmp_path: Path) -> None:
	"""没有写标记的只读步骤不受新闸门影响，认证后仍可恢复重跑。"""
	platform = _Platform(AuthRequired("auth"))
	context = _context(tmp_path, platform)

	with WorkflowStore(tmp_path) as store:
		runner = WorkflowRunner(store, DEFAULT_ACTIONS)
		first = runner.run(_plan("recruiter_chat_history"), context, run_id="run-read", max_retries=0)
		assert first["status"] == "failed"
		assert first["error"]["code"] == "AUTH_REQUIRED"
		assert first["error"].get("replay_safe") is not False
		second = runner.run(_plan("recruiter_chat_history"), context, run_id="run-read", max_retries=0)

	assert second["status"] == "completed"
	assert platform.calls == 2


def test_rate_limited_write_stays_replayable(tmp_path: Path) -> None:
	"""平台明确限流拒绝不是结果未知，恢复仍可重发（与既有契约一致）。"""
	platform = _Platform({"code": -1, "parsed_code": "RATE_LIMITED", "message": "slow down"})
	context = _context(tmp_path, platform)

	with WorkflowStore(tmp_path) as store:
		runner = WorkflowRunner(store, DEFAULT_ACTIONS)
		first = runner.run(_plan("recruiter_reply"), context, run_id="run-limited", max_retries=0)
		assert first["status"] == "failed"
		assert first["error"]["code"] == "RATE_LIMITED"
		assert first["error"].get("replay_safe") is not False
		second = runner.run(_plan("recruiter_reply"), context, run_id="run-limited", max_retries=0)

	assert second["status"] == "completed"
	assert platform.calls == 2
