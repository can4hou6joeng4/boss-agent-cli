"""Deterministic workflow runner shared by TTY and headless entrypoints."""

from __future__ import annotations

import secrets
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from boss_agent_cli.auth.manager import AuthRequired, TokenRefreshFailed
from boss_agent_cli.wizard.models import StepResult, WorkflowPlan, WorkflowStatus
from boss_agent_cli.wizard.store import WorkflowStore

Action = Callable[[Any, Mapping[str, Any], Mapping[str, Any]], StepResult]
WorkflowEvent = Callable[[str, str, Mapping[str, Any] | None], None]


class WorkflowActionError(Exception):
	def __init__(
		self,
		code: str,
		message: str,
		*,
		recoverable: bool = False,
		recovery_action: str | None = None,
	) -> None:
		super().__init__(message)
		self.code = code
		self.recoverable = recoverable
		self.recovery_action = recovery_action

	def to_dict(self) -> dict[str, Any]:
		return {
			"code": self.code,
			"message": str(self),
			"recoverable": self.recoverable,
			"recovery_action": self.recovery_action,
		}


@dataclass(frozen=True)
class WorkflowControl:
	"""Cooperative stop/deadline control exposed to long-running actions."""

	run_id: str
	step_name: str
	store: WorkflowStore
	deadline: float | None = None

	def poll(self) -> str | None:
		run = self.store.get(self.run_id)
		if run is not None and run.get("status") == WorkflowStatus.STOPPED.value:
			return "stop"
		if self.deadline is not None and time.monotonic() >= self.deadline:
			return "timeout"
		return None

	def bind_inner_run(self, inner_run_id: str) -> None:
		self.store.update_step(
			self.run_id,
			self.step_name,
			status=WorkflowStatus.RUNNING.value,
			result={"inner_run_id": inner_run_id},
		)

	def raise_if_requested(self) -> None:
		signal = self.poll()
		if signal == "stop":
			raise WorkflowActionError(
				"WORKFLOW_STOPPED",
				"workflow stop requested",
				recoverable=True,
				recovery_action="创建新 workflow，或恢复其内部可恢复任务",
			)
		if signal == "timeout":
			raise WorkflowActionError(
				"WORKFLOW_TIMEOUT",
				"workflow exceeded its timeout",
				recoverable=True,
				recovery_action=f"boss wizard --resume {self.run_id}",
			)


class WorkflowRunner:
	def __init__(self, store: WorkflowStore, actions: Mapping[str, Action]) -> None:
		self._store = store
		self._actions = dict(actions)

	def run(
		self,
		plan: WorkflowPlan,
		context: Any,
		*,
		run_id: str | None = None,
		on_event: WorkflowEvent | None = None,
		timeout_seconds: float | None = None,
		max_retries: int = 0,
	) -> dict[str, Any]:
		if max_retries < 0:
			raise ValueError("max_retries must be >= 0")
		started_at = time.monotonic()
		deadline = started_at + timeout_seconds if timeout_seconds is not None else None
		run_id = run_id or f"wrn_{secrets.token_urlsafe(12)}"
		existing = self._store.get(run_id)
		if existing is None:
			self._store.create(run_id, plan)
		else:
			self._assert_plan_matches(existing, plan)
			if existing["status"] == WorkflowStatus.STOPPED.value:
				return existing
		prior_results: dict[str, Any] = {}
		# waiting_input 步骤结果仍传给 action（如 crawl run_id），但不会跳过重跑。
		waiting_priors: dict[str, Any] = {}
		stored_run = self._store.get(run_id)
		if stored_run is None:
			raise RuntimeError(f"workflow run {run_id!r} was not persisted")
		for step in stored_run["steps"]:
			if not step["result"]:
				continue
			if step["status"] == WorkflowStatus.COMPLETED.value:
				prior_results[step["step_name"]] = step["result"]
			elif step["status"] == WorkflowStatus.WAITING_INPUT.value:
				waiting_priors[step["step_name"]] = step["result"]

		self._store.update_run(
			run_id,
			status=WorkflowStatus.RUNNING.value,
			current_step=None,
		)
		for step_name in plan.requested_steps:
			if step_name in prior_results:
				continue
			current = self._store.get(run_id) or {}
			if current.get("status") == WorkflowStatus.STOPPED.value:
				return current
			if timeout_seconds is not None and time.monotonic() - started_at >= timeout_seconds:
				return self._fail_controlled(
					run_id,
					step_name,
					code="WORKFLOW_TIMEOUT",
					message=f"workflow exceeded {timeout_seconds:g} seconds",
					recovery_action=f"boss wizard --resume {run_id}",
				)
			if on_event:
				on_event("step_started", step_name, None)
			self._store.update_run(
				run_id,
				status=WorkflowStatus.RUNNING.value,
				current_step=step_name,
			)
			self._store.update_step(run_id, step_name, status=WorkflowStatus.RUNNING.value)
			control = WorkflowControl(run_id, step_name, self._store, deadline)
			with_control = getattr(context, "with_workflow_control", None)
			action_context = with_control(control) if callable(with_control) else context
			try:
				control.raise_if_requested()
				action = self._actions.get(step_name)
				if action is None:
					raise WorkflowActionError(
						"NOT_SUPPORTED",
						f"workflow step {step_name!r} 未实现",
						recoverable=True,
						recovery_action="选择 catalog 中当前平台支持的 workflow goal",
					)
				attempt = 0
				action_prior = {**waiting_priors, **prior_results}
				while True:
					try:
						result = action(action_context, plan.inputs, action_prior)
						control.raise_if_requested()
						break
					except WorkflowActionError as exc:
						if not exc.recoverable or attempt >= max_retries:
							raise
						attempt += 1
						control.raise_if_requested()
						if on_event:
							on_event("step_retrying", step_name, {"attempt": attempt, "code": exc.code})
			except AuthRequired as exc:
				error = WorkflowActionError("AUTH_REQUIRED", str(exc), recoverable=True, recovery_action="boss login")
			except TokenRefreshFailed as exc:
				error = WorkflowActionError(
					"TOKEN_REFRESH_FAILED", str(exc), recoverable=True, recovery_action="boss login"
				)
			except NotImplementedError as exc:
				error = WorkflowActionError(
					"NOT_SUPPORTED",
					str(exc),
					recoverable=True,
					recovery_action="切换平台或 workflow goal 后重试",
				)
			except WorkflowActionError as exc:
				error = exc
			except Exception as exc:
				error = WorkflowActionError(
					"NETWORK_ERROR", str(exc), recoverable=True, recovery_action="重试当前 run_id"
				)
			else:
				result_data = result.to_dict()
				prior_results[step_name] = result_data
				self._store.update_step(run_id, step_name, status=result.status.value, result=result_data)
				self._store.update_run(
					run_id,
					status=result.status.value
					if result.status == WorkflowStatus.WAITING_INPUT
					else WorkflowStatus.RUNNING.value,
					current_step=step_name,
					last_result=result_data,
				)
				if on_event:
					on_event("step_finished", step_name, result_data)
				if result.status == WorkflowStatus.WAITING_INPUT:
					return self._store.get(run_id) or {}
				continue

			error_data = error.to_dict()
			failed_status = (
				WorkflowStatus.STOPPED.value if error.code == "WORKFLOW_STOPPED" else WorkflowStatus.FAILED.value
			)
			self._store.update_step(run_id, step_name, status=failed_status, error=error_data)
			self._store.update_run(
				run_id,
				status=failed_status,
				current_step=step_name,
				error=error_data,
			)
			if on_event:
				on_event("step_failed", step_name, error_data)
			return self._store.get(run_id) or {}

		self._store.update_run(
			run_id,
			status=WorkflowStatus.COMPLETED.value,
			current_step=None,
			last_result=prior_results.get(plan.requested_steps[-1]) if plan.requested_steps else {},
		)
		return self._store.get(run_id) or {}

	@staticmethod
	def _assert_plan_matches(existing: Mapping[str, Any], plan: WorkflowPlan) -> None:
		params = existing.get("params") or {}
		persisted = {
			"role": existing.get("role"),
			"platform": existing.get("platform"),
			"goal": existing.get("goal"),
			"mode": existing.get("mode"),
			"inputs": params.get("inputs"),
			"requested_steps": params.get("requested_steps"),
		}
		requested = {
			"role": plan.role,
			"platform": plan.platform,
			"goal": plan.goal,
			"mode": plan.mode,
			"inputs": dict(plan.inputs),
			"requested_steps": list(plan.requested_steps),
		}
		if persisted != requested:
			raise WorkflowActionError(
				"WORKFLOW_PLAN_MISMATCH",
				"run_id 已绑定到不同的 workflow plan",
				recoverable=False,
				recovery_action="使用原 plan 恢复，或不传 run_id 创建新任务",
			)

	def _fail_controlled(
		self,
		run_id: str,
		step_name: str,
		*,
		code: str,
		message: str,
		recovery_action: str,
	) -> dict[str, Any]:
		error = {
			"code": code,
			"message": message,
			"recoverable": True,
			"recovery_action": recovery_action,
		}
		self._store.update_step(run_id, step_name, status=WorkflowStatus.FAILED.value, error=error)
		self._store.update_run(
			run_id,
			status=WorkflowStatus.FAILED.value,
			current_step=step_name,
			error=error,
		)
		return self._store.get(run_id) or {}
