"""Issue #2904：聊天页异常的类型分支不能绕过已落盘的写标记。"""

from pathlib import Path
from typing import Any

import pytest

from boss_agent_cli.api.browser_client import ChatTabCdpUnreachable, RecruiterChatTabRequired
from boss_agent_cli.api.recruiter_client import _mark_request_not_sent
from boss_agent_cli.output import Logger
from boss_agent_cli.wizard.actions import ActionContext, DEFAULT_ACTIONS
from boss_agent_cli.wizard.models import WorkflowPlan
from boss_agent_cli.wizard.runner import WorkflowRunner
from boss_agent_cli.wizard.store import WorkflowStore

FRIEND_ID = 123


class _ExchangePlatform:
	def __init__(self, failure: BaseException) -> None:
		self.failure = failure
		self.calls = 0

	def exchange_request_by_friend(self, friend_id: int, exchange_type: int) -> dict[str, Any]:
		self.calls += 1
		if self.failure is not None:
			failure, self.failure = self.failure, None
			raise failure
		return {"code": 0, "zpData": {"requested": True}}

	def is_success(self, response: dict[str, Any]) -> bool:
		return response.get("code") == 0

	def parse_error(self, response: dict[str, Any]) -> tuple[str, str]:
		return "UNKNOWN", ""

	def unwrap_data(self, response: dict[str, Any]) -> dict[str, Any]:
		return response.get("zpData") or {}

	def __enter__(self) -> "_ExchangePlatform":
		return self

	def __exit__(self, *args: object) -> bool:
		return False


def _plan() -> WorkflowPlan:
	return WorkflowPlan(
		role="recruiter",
		platform="zhipin",
		goal="offline",
		inputs={"friend_id": FRIEND_ID},
		requested_steps=("recruiter_request_resume",),
		mode="headless",
	)


def _context(tmp_path: Path, platform: _ExchangePlatform) -> ActionContext:
	return ActionContext(
		data_dir=tmp_path,
		platform="zhipin",
		role="recruiter",
		logger=Logger("error"),
		recruiter_factory=lambda *args, **kwargs: platform,
	)


@pytest.mark.parametrize("make_error, expected_code", [
	(lambda: ChatTabCdpUnreachable("cdp down"), "CDP_UNAVAILABLE"),
	(lambda: RecruiterChatTabRequired(), "RECRUITER_CHAT_TAB_REQUIRED"),
])
def test_unmarked_chat_tab_error_keeps_write_marker(tmp_path: Path, make_error, expected_code: str) -> None:
	"""只有异常类型、没有显式未发出证据时，恢复不得重放写请求。"""
	platform = _ExchangePlatform(make_error())
	context = _context(tmp_path, platform)

	with WorkflowStore(tmp_path) as store:
		runner = WorkflowRunner(store, DEFAULT_ACTIONS)
		first = runner.run(_plan(), context, run_id="run-marker", max_retries=0)
		assert first["status"] == "failed"
		assert first["error"]["code"] == expected_code
		assert first["error"]["replay_safe"] is False
		assert "写请求在途" in first["error"]["message"]
		assert "hr chatmsg 123" in first["error"]["recovery_action"]
		second = runner.run(_plan(), context, run_id="run-marker", max_retries=0)

	assert second["status"] == "failed"
	assert second["error"]["replay_safe"] is False
	assert platform.calls == 1


@pytest.mark.parametrize("make_error, expected_code", [
	(lambda: ChatTabCdpUnreachable("cdp down"), "CDP_UNAVAILABLE"),
	(lambda: RecruiterChatTabRequired(), "RECRUITER_CHAT_TAB_REQUIRED"),
])
def test_marked_chat_tab_error_stays_replayable(tmp_path: Path, make_error, expected_code: str) -> None:
	"""客户端在写入边界标注 request_sent=False 时，仍按原错误码允许恢复重跑。"""
	exc = make_error()
	_mark_request_not_sent(exc)
	platform = _ExchangePlatform(exc)
	context = _context(tmp_path, platform)

	with WorkflowStore(tmp_path) as store:
		runner = WorkflowRunner(store, DEFAULT_ACTIONS)
		first = runner.run(_plan(), context, run_id="run-unsent", max_retries=0)
		assert first["status"] == "failed"
		assert first["error"]["code"] == expected_code
		assert first["error"].get("replay_safe") is not False
		second = runner.run(_plan(), context, run_id="run-unsent", max_retries=0)

	assert second["status"] == "completed"
	assert platform.calls == 2
