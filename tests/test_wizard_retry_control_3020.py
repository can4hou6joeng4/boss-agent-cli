"""明确拒绝之后的重试前停止不能继承失效的写标记（Issue #3020）。"""

from types import SimpleNamespace

import pytest

from boss_agent_cli.output import Logger
from boss_agent_cli.wizard.actions import ActionContext, DEFAULT_ACTIONS
from boss_agent_cli.wizard.models import WorkflowPlan
from boss_agent_cli.wizard.runner import WorkflowRunner
from boss_agent_cli.wizard.store import WorkflowStore


class _Recruiter:
	def __init__(self, first_code: str, before_first_return) -> None:
		self.calls = 0
		self.first_code = first_code
		self.before_first_return = before_first_return

	def __enter__(self):
		return self

	def __exit__(self, *args):
		pass

	def send_message_by_friend(self, friend_id, message):
		self.calls += 1
		if self.calls == 1:
			self.before_first_return()
			return {"code": 9, "error": self.first_code, "message": "offline rejection"}
		return {"code": 0, "data": {"sent": True}}

	def is_success(self, response):
		return response["code"] == 0

	def parse_error(self, response):
		return response["error"], response["message"]

	def unwrap_data(self, response):
		return response["data"]


def _plan():
	return WorkflowPlan(
		role="recruiter", platform="zhipin", goal="offline",
		inputs={"friend_id": 42, "message": "测试问卷"},
		requested_steps=("recruiter_reply",), mode="headless",
	)


@pytest.mark.parametrize("signal", ["timeout", "stop"])
def test_control_before_retry_does_not_inherit_rejected_write_marker(tmp_path, monkeypatch, signal):
	clock = [1000.0]
	monkeypatch.setattr("boss_agent_cli.wizard.runner.time", SimpleNamespace(monotonic=lambda: clock[0]))
	with WorkflowStore(tmp_path) as store:
		def trigger_control():
			if signal == "timeout":
				clock[0] = 1002.0
			else:
				assert store.request_stop("offline-retry")

		recruiter = _Recruiter("RATE_LIMITED", trigger_control)
		context = ActionContext(
			tmp_path, "zhipin", "recruiter", Logger("error"), recruiter_factory=lambda *args: recruiter,
		)
		runner = WorkflowRunner(store, DEFAULT_ACTIONS)
		first = runner.run(_plan(), context, run_id="offline-retry", timeout_seconds=1.0, max_retries=1)

		expected_code = "WORKFLOW_TIMEOUT" if signal == "timeout" else "WORKFLOW_STOPPED"
		assert first["error"]["code"] == expected_code
		assert first["error"].get("replay_safe") is not False
		assert first["steps"][0]["error"].get("replay_safe") is not False
		assert recruiter.calls == 1

		second = runner.run(_plan(), context, run_id="offline-retry")

	if signal == "timeout":
		assert second["status"] == "completed"
		assert recruiter.calls == 2
	else:
		# 显式 stop 仍是终态，只修正写结果的含义，不反转停止语义。
		assert second["status"] == "stopped"
		assert recruiter.calls == 1


def test_auth_failure_after_write_still_blocks_replay_before_control(tmp_path, monkeypatch):
	clock = [1000.0]
	monkeypatch.setattr("boss_agent_cli.wizard.runner.time", SimpleNamespace(monotonic=lambda: clock[0]))

	def expire():
		clock[0] = 1002.0

	recruiter = _Recruiter("AUTH_REQUIRED", expire)
	context = ActionContext(
		tmp_path, "zhipin", "recruiter", Logger("error"), recruiter_factory=lambda *args: recruiter,
	)
	with WorkflowStore(tmp_path) as store:
		runner = WorkflowRunner(store, DEFAULT_ACTIONS)
		first = runner.run(_plan(), context, run_id="offline-auth", timeout_seconds=1.0, max_retries=1)
		second = runner.run(_plan(), context, run_id="offline-auth")

	assert first["error"]["code"] == "AUTH_REQUIRED"
	assert first["error"]["replay_safe"] is False
	assert second["status"] == "failed"
	assert recruiter.calls == 1
