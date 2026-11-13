"""Deterministic workflow runner shared by TTY and headless entrypoints."""

from __future__ import annotations

import secrets
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from boss_agent_cli.api.browser_client import BrowserNavigationError
from boss_agent_cli.api.browser_source import BrowserSourceUnavailable, BrowserSourceUnsupported
from boss_agent_cli.api.client import PlatformRiskError
from boss_agent_cli.api.request_budget import RequestBudgetExceeded, request_control
from boss_agent_cli.auth.manager import AuthRequired, TokenRefreshFailed
from boss_agent_cli.display import (
	INTERNAL_ERROR_TYPES,
	boss_command,
	error_contract_for_code,
	risk_error_contract,
	risk_error_message,
	shell_argument,
)
from boss_agent_cli.output import is_local_io_error
from boss_agent_cli.wizard.models import StepResult, WorkflowInputError, WorkflowPlan, WorkflowStatus
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
		details: dict[str, Any] | None = None,
		replay_safe: bool = True,
	) -> None:
		super().__init__(message)
		self.code = code
		self.recoverable = recoverable
		self.recovery_action = recovery_action
		self.details = details
		# replay_safe=False 表示动作可能已经生效：--resume 不得重跑该步骤。
		self.replay_safe = replay_safe

	def to_dict(self) -> dict[str, Any]:
		result: dict[str, Any] = {
			"code": self.code,
			"message": str(self),
			"recoverable": self.recoverable,
			"recovery_action": self.recovery_action,
		}
		if self.details:
			result["details"] = self.details
		if not self.replay_safe:
			result["replay_safe"] = False
		return result


@dataclass(frozen=True)
class WorkflowControl:
	"""Cooperative stop/deadline control exposed to long-running actions."""

	run_id: str
	step_name: str
	store: WorkflowStore
	deadline: float | None = None
	# 超时恢复命令必须回到本次运行的数据目录；None 时保持通用示例。
	resume_action: str | None = None

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
				"已按 stop 请求停止 workflow",
				recoverable=True,
				recovery_action="创建新 workflow，或恢复其内部可恢复任务",
			)
		if signal == "timeout":
			raise WorkflowActionError(
				"WORKFLOW_TIMEOUT",
				"workflow 超过设定时限",
				recoverable=True,
				recovery_action=self.resume_action
				or f"boss wizard --resume {shell_argument(self.run_id)}",
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
		data_dir = getattr(context, "data_dir", None)
		login_action = boss_command("login", platform_name=plan.platform, data_dir=data_dir)

		def resume_action_for(target: str) -> str:
			return boss_command(
				f"wizard --resume {shell_argument(target)}", platform_name=plan.platform, data_dir=data_dir
			)
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
		blocked_steps: dict[str, dict[str, Any]] = {}
		for step in stored_run["steps"]:
			error = step.get("error") or {}
			if step["status"] == WorkflowStatus.FAILED.value and error.get("replay_safe") is False:
				# 动作可能已经生效：--resume 不得再执行一次。
				blocked_steps[step["step_name"]] = error
				continue
			if not step["result"]:
				continue
			if step["status"] == WorkflowStatus.COMPLETED.value:
				prior_results[step["step_name"]] = step["result"]
			elif step["status"] in {
				WorkflowStatus.WAITING_INPUT.value,
				WorkflowStatus.FAILED.value,
				WorkflowStatus.RUNNING.value,
			}:
				# 失败/中断的步骤仍会重跑，但它可能已通过 bind_inner_run 绑定了内部可恢复
				# 任务（如 crawl run_id）：把 result 交给 action，让 crawl_start 续跑而不是
				# 新建一份采集（Issue #882）。
				waiting_priors[step["step_name"]] = step["result"]
		pending_steps = [name for name in plan.requested_steps if name not in prior_results]
		if pending_steps and pending_steps[0] in blocked_steps:
			# 保持原失败状态与 recovery_action，让操作者先核对官方页面。
			return stored_run

		self._store.update_run(
			run_id,
			status=WorkflowStatus.RUNNING.value,
			current_step=None,
		)
		for step_name in plan.requested_steps:
			if step_name in prior_results:
				continue
			if step_name in blocked_steps:
				# 正常路径已在循环前返回；持久化状态异常时也不重放，恢复原失败状态。
				persisted = self._store.get(run_id) or {}
				self._store.update_run(
					run_id,
					status=WorkflowStatus.FAILED.value,
					current_step=step_name,
					last_result=persisted.get("last_result"),
					error=blocked_steps[step_name],
				)
				return self._store.get(run_id) or {}
			current = self._store.get(run_id) or {}
			if current.get("status") == WorkflowStatus.STOPPED.value:
				return current
			if timeout_seconds is not None and time.monotonic() - started_at >= timeout_seconds:
				return self._fail_controlled(
					run_id,
					step_name,
					code="WORKFLOW_TIMEOUT",
					message=f"workflow 超过 {timeout_seconds:g} 秒未完成",
					recovery_action=resume_action_for(run_id),
				)
			if on_event:
				on_event("step_started", step_name, None)
			self._store.update_run(
				run_id,
				status=WorkflowStatus.RUNNING.value,
				current_step=step_name,
			)
			self._store.update_step(
				run_id,
				step_name,
				status=WorkflowStatus.RUNNING.value,
				# 重跑前保留此前的 result（可能含内部 crawl 绑定），否则中断后再恢复就找不到原任务。
				result=waiting_priors.get(step_name),
			)
			control = WorkflowControl(run_id, step_name, self._store, deadline, resume_action=resume_action_for(run_id))
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
						with request_control(check=control.raise_if_requested, deadline=control.deadline):
							result = action(action_context, plan.inputs, action_prior)
						break
					except WorkflowActionError as exc:
						if not exc.recoverable or not exc.replay_safe or attempt >= max_retries:
							raise
						attempt += 1
						control.raise_if_requested()
						# 动作可能在失败前已保存内部任务绑定；自动重试也要读取最新断点，
						# 否则 crawl_start 会另建任务并丢开原任务的进度与预算（Issue #972）。
						persisted = self._store.get(run_id) or {}
						checkpoint = next(
							(step.get("result") for step in persisted.get("steps", []) if step["step_name"] == step_name),
							None,
						)
						if checkpoint is None:
							action_prior.pop(step_name, None)
						else:
							action_prior[step_name] = checkpoint
						if on_event:
							on_event("step_retrying", step_name, {"attempt": attempt, "code": exc.code})
			except AuthRequired as exc:
				error = WorkflowActionError("AUTH_REQUIRED", str(exc), recoverable=True, recovery_action=login_action)
			except TokenRefreshFailed as exc:
				error = WorkflowActionError(
					"TOKEN_REFRESH_FAILED", str(exc), recoverable=True, recovery_action=login_action
				)
			except NotImplementedError as exc:
				error = WorkflowActionError(
					"NOT_SUPPORTED",
					str(exc),
					recoverable=True,
					recovery_action="切换平台或 workflow goal 后重试",
				)
			except WorkflowActionError as exc:
				if (
					exc.code in {"AUTH_REQUIRED", "AUTH_EXPIRED", "TOKEN_REFRESH_FAILED"}
					and exc.recoverable
					and exc.replay_safe
					and exc.recovery_action == error_contract_for_code(exc.code)[1]
				):
					# action 的通用认证指引在写入 checkpoint 前补齐本次运行目录。
					exc.recovery_action = login_action
				error = exc
			except WorkflowInputError as exc:
				# inputs 写错（类型不对）不是网络问题：落到下面的通用兜底会变成
				# NETWORK_ERROR +「重试当前 run_id」，而重试同一条输入必然复现。
				recoverable, recovery = error_contract_for_code("INVALID_PARAM")
				error = WorkflowActionError(
					"INVALID_PARAM",
					str(exc),
					recoverable=recoverable,
					recovery_action=recovery,
				)
			except RequestBudgetExceeded as exc:
				recoverable, recovery = error_contract_for_code(exc.code)
				error = WorkflowActionError(exc.code, str(exc), recoverable=recoverable, recovery_action=recovery, details=exc.details)
			except KeyboardInterrupt:
				error = WorkflowActionError(
					"WORKFLOW_STOPPED",
					"workflow 被中断；开始新的运行前请先核对未完成的操作",
					recoverable=True,
					recovery_action="检查 checkpoint 与平台实际结果，再显式创建或恢复内部任务",
				)
			except PlatformRiskError as exc:
				# 风控：终止语义不能被下面的 NETWORK_ERROR 兜底反转成「重试当前 run_id」。
				# checkpoint 照常落盘（下方 update_step/update_run），恢复只能由用户在
				# 处理完账号/安全页后显式发起（Issue #419）。
				recovery_action, _ = risk_error_contract(exc.code)
				error = WorkflowActionError(
					exc.code,
					risk_error_message(exc.code),
					recoverable=False,
					recovery_action=recovery_action,
				)
			except BrowserNavigationError as exc:
				# 首页导航已经在会话内重试过。这里不再把同一步骤立刻重跑一遍。
				recoverable, recovery = error_contract_for_code(exc.code)
				error = WorkflowActionError(
					exc.code,
					str(exc),
					recoverable=recoverable,
					recovery_action=recovery,
					details=exc.details(),
				)
			except BrowserSourceUnavailable as exc:
				error = WorkflowActionError(
					exc.code, str(exc), recoverable=True, recovery_action=exc.policy.recovery_action
				)
			except BrowserSourceUnsupported as exc:
				fallback_recovery = "切换平台或 workflow goal 后重试"
				recoverable, contract_recovery = error_contract_for_code(
					"NOT_SUPPORTED", fallback_recoverable=True, fallback_recovery_action=fallback_recovery
				)
				error = WorkflowActionError(
					"NOT_SUPPORTED",
					str(exc),
					recoverable=recoverable,
					recovery_action=contract_recovery or fallback_recovery,
				)
			except INTERNAL_ERROR_TYPES as exc:
				internal_recoverable, internal_recovery = error_contract_for_code("INTERNAL_ERROR")
				error = WorkflowActionError(
					"INTERNAL_ERROR",
					f"{type(exc).__name__}: {exc}",
					recoverable=internal_recoverable,
					recovery_action=internal_recovery,
				)
			except OSError as exc:
				# 本地文件读写失败（导出路径不可写、磁盘满）重试同一 run_id 也不会成功；
				# 套接字层的 ConnectionError / TimeoutError 仍是网络问题，保持可重试语义。
				if is_local_io_error(exc):
					io_recoverable, io_recovery = error_contract_for_code("LOCAL_IO_ERROR")
					error = WorkflowActionError(
						"LOCAL_IO_ERROR",
						f"本地文件读写失败: {exc}",
						recoverable=io_recoverable,
						recovery_action=io_recovery,
					)
				else:
					error = WorkflowActionError(
						"NETWORK_ERROR", str(exc), recoverable=True, recovery_action="重试当前 run_id"
					)
			except Exception as exc:
				error = WorkflowActionError(
					"NETWORK_ERROR", str(exc), recoverable=True, recovery_action="重试当前 run_id"
				)
			else:
				result_data = result.to_dict()
				prior_results[step_name] = result_data
				# A returned result confirms the action's outcome. Persist it before
				# checking controls, so a late deadline cannot replay that effect.
				self._store.update_step(run_id, step_name, status=result.status.value, result=result_data)
				signal = control.poll()
				if signal == "timeout":
					return self._fail_controlled(
						run_id,
						step_name,
						code="WORKFLOW_TIMEOUT",
						message=f"workflow 超过 {timeout_seconds:g} 秒未完成",
						recovery_action=resume_action_for(run_id),
						preserve_result=True,
					)
				self._store.update_run(
					run_id,
					status=WorkflowStatus.STOPPED.value
					if signal == "stop"
					else result.status.value
					if result.status == WorkflowStatus.WAITING_INPUT
					else WorkflowStatus.RUNNING.value,
					current_step=step_name,
					last_result=result_data,
				)
				if on_event:
					on_event("step_finished", step_name, result_data)
				if signal == "stop":
					return self._store.get(run_id) or {}
				if result.status == WorkflowStatus.WAITING_INPUT:
					return self._store.get(run_id) or {}
				continue

			error_data = error.to_dict()
			failed_status = (
				WorkflowStatus.STOPPED.value if error.code == "WORKFLOW_STOPPED" else WorkflowStatus.FAILED.value
			)
			persisted = self._store.get(run_id) or {}
			step_result = next(
				(step.get("result") for step in persisted.get("steps", []) if step["step_name"] == step_name),
				None,
			)
			self._store.update_step(run_id, step_name, status=failed_status, result=step_result, error=error_data)
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
		preserve_result: bool = False,
	) -> dict[str, Any]:
		error = {
			"code": code,
			"message": message,
			"recoverable": True,
			"recovery_action": recovery_action,
		}
		stored_run = self._store.get(run_id) or {}
		step_result = next(
			(step.get("result") for step in stored_run.get("steps", []) if step["step_name"] == step_name),
			None,
		)
		if not preserve_result:
			# 超时/失败同样保留此前写入的结果（如 inner_run_id），恢复时才能续跑内部任务（Issue #882）。
			self._store.update_step(
				run_id, step_name, status=WorkflowStatus.FAILED.value, result=step_result, error=error
			)
		self._store.update_run(
			run_id,
			status=WorkflowStatus.FAILED.value,
			current_step=step_name,
			last_result=step_result,
			error=error,
		)
		return self._store.get(run_id) or {}
