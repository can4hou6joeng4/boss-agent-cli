"""Issue #2913：自动重试不能清掉写请求在途标记后二次派发写操作。"""

from pathlib import Path
from typing import Any

from boss_agent_cli.output import Logger
from boss_agent_cli.wizard.actions import ActionContext, DEFAULT_ACTIONS, _write_call
from boss_agent_cli.wizard.models import StepResult, WorkflowPlan
from boss_agent_cli.wizard.runner import WorkflowActionError, WorkflowRunner
from boss_agent_cli.wizard.store import WorkflowStore

FRIEND_ID = 123
_AUTH_RESPONSE = {"code": -1, "parsed_code": "AUTH_EXPIRED", "message": "auth expired"}
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

	def write(self) -> dict[str, Any]:
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


def test_auth_write_failure_is_not_auto_retried(tmp_path: Path) -> None:
	"""写标记存在时，认证类可重放失败也不能在同一次运行里二次派发。"""
	platform = _Platform(dict(_AUTH_RESPONSE))
	context = _context(tmp_path, platform)

	with WorkflowStore(tmp_path) as store:
		runner = WorkflowRunner(store, DEFAULT_ACTIONS)
		first = runner.run(_plan("recruiter_reply"), context, run_id="run-retry-auth", max_retries=1)
		assert first["status"] == "failed"
		assert first["error"]["code"] == "AUTH_EXPIRED"
		assert first["error"]["replay_safe"] is False
		assert "写请求在途" in first["error"]["message"]
		second = runner.run(_plan("recruiter_reply"), context, run_id="run-retry-auth", max_retries=1)

	assert second["status"] == "failed"
	assert second["error"]["replay_safe"] is False
	assert platform.calls == 1


def test_provably_unsent_failure_still_auto_retries(tmp_path: Path) -> None:
	"""显式 request_sent=False 已撤销标记时，自动重试契约保持不变。"""
	platform = _Platform(None)
	context = _context(tmp_path, platform)
	attempts = 0

	def action(ctx: ActionContext, inputs: dict[str, Any], prior: dict[str, Any]) -> Any:
		def write() -> dict[str, Any]:
			nonlocal attempts
			attempts += 1
			if attempts == 1:
				exc = WorkflowActionError("AUTH_EXPIRED", "not sent", recoverable=True)
				exc.request_sent = False
				raise exc
			return platform.write()

		return StepResult(
			_write_call(
				ctx,
				platform,
				write,
				"写失败",
				provably_unsent=lambda exc: getattr(exc, "request_sent", None) is False,
			)
		)

	with WorkflowStore(tmp_path) as store:
		runner = WorkflowRunner(store, {"write_step": action})
		result = runner.run(_plan("write_step"), context, run_id="run-unsent", max_retries=1)

	assert result["status"] == "completed"
	assert platform.calls == 1


def test_read_only_replayable_failure_still_auto_retries(tmp_path: Path) -> None:
	"""没有写标记的步骤仍按原有可重放语义自动重试。"""
	calls = 0

	def action(ctx: ActionContext, inputs: dict[str, Any], prior: dict[str, Any]) -> dict[str, Any]:
		nonlocal calls
		calls += 1
		if calls == 1:
			raise WorkflowActionError("RATE_LIMITED", "slow down", recoverable=True)
		return StepResult({"ok": True})

	with WorkflowStore(tmp_path) as store:
		runner = WorkflowRunner(store, {"read_step": action})
		result = runner.run(_plan("read_step"), _context(tmp_path, _Platform(None)), run_id="run-read", max_retries=1)

	assert result["status"] == "completed"
	assert calls == 2
