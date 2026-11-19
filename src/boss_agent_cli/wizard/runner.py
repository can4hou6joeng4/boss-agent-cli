"""Deterministic workflow runner shared by TTY and headless entrypoints."""

from __future__ import annotations

import json
import secrets
import sqlite3
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from boss_agent_cli.api.browser_client import BrowserNavigationError, ChatTabCdpUnreachable
from boss_agent_cli.api.browser_source import BrowserSourceUnavailable, BrowserSourceUnsupported
from boss_agent_cli.api.client import PlatformRiskError
from boss_agent_cli.api.proxy import ProxyConfigurationError
from boss_agent_cli.api.request_budget import RequestBudgetExceeded, request_control
from boss_agent_cli.auth.manager import AuthRequired, TokenRefreshFailed
from boss_agent_cli.cache.store import (
	CacheBusyError,
	CacheDatabaseInvalid,
	CacheRecordInvalid,
	CacheWriteRejected,
	is_cache_busy_error,
)
from boss_agent_cli.crawler.controls import CrawlProfilePathInvalid
from boss_agent_cli.display import (
	INTERNAL_ERROR_TYPES,
	RISK_ERROR_CONTRACTS,
	boss_command,
	browser_policy_text,
	error_contract_for_code,
	platform_error_contract,
	platform_error_message,
	risk_error_contract,
	risk_error_message,
	shell_argument,
)
from boss_agent_cli.output import is_local_io_error
from boss_agent_cli.schema.error_codes import ERROR_CODES
from boss_agent_cli.search_filters import SearchPipelinePlatformError
from boss_agent_cli.services.browser_runtime import is_browser_kernel_missing, patchright_install_command
from boss_agent_cli.wizard.models import StepResult, WorkflowInputError, WorkflowPlan, WorkflowStatus
from boss_agent_cli.wizard.store import WorkflowStore

Action = Callable[[Any, Mapping[str, Any], Mapping[str, Any]], StepResult]
WorkflowEvent = Callable[[str, str, Mapping[str, Any] | None], None]


#: 命中即停止、且不允许重试的账号 / 环境风控码。
_RISK_CODES = frozenset({"ACCOUNT_RISK", "ENVIRONMENT_RISK"})

#: 带 ``replay_safe: false`` 时必须阻断重放的步骤状态。
_REPLAY_BLOCKING_STATUSES = frozenset({WorkflowStatus.FAILED.value, WorkflowStatus.STOPPED.value})


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
		auto_retry: bool = True,
	) -> None:
		super().__init__(message)
		self.code = code
		self.recoverable = recoverable
		if recovery_action is None:
			# 登记表里的 recovery_action 是 Agent 的下一步真源：动作层漏传时按
			# 错误码兜底补全，信封不再给出 null（Issue #1907）。显式传入优先。
			recovery_action = error_contract_for_code(code)[1]
		self.recovery_action = recovery_action
		self.details = details
		# replay_safe=False 表示动作可能已经生效：--resume 不得重跑该步骤。
		self.replay_safe = replay_safe
		# recoverable 表示修正后可重新执行；auto_retry=False 的确定性失败
		# （如本地配置损坏）不进入同一次运行的自动重试循环（Issue #1525）。
		self.auto_retry = auto_retry

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


def _browser_kernel_missing_error(
	exc: Exception, plan: WorkflowPlan, data_dir: Path | None
) -> WorkflowActionError:
	"""内核缺失的统一 workflow 错误：安装指引 + 本次运行的数据目录。

	Playwright 报 ``Executable doesn't exist`` / ``Playwright was just installed``
	是确定性失败——patchright 已升级但对应修订版的 Chromium 没装。runner 的自动
	重试与 Agent 的 ``--resume`` 都会复现同一结果，而且每次重试还会再起一个
	浏览器进程。登录链路与命令层（``display.handle_browser_kernel_missing``）
	已识别这条错误，runner 沿用同一份登记表契约与文案。

	``doctor`` 诊断的是 ``<data_dir>`` 里的登录态与内核缓存，必须带上本次运行
	的数据目录，否则自定义 --data-dir 下照抄提示会诊断另一份目录（同 Issue #978）。
	"""
	recoverable, _ = error_contract_for_code("BROWSER_KERNEL_MISSING")
	doctor = boss_command("doctor", platform_name=plan.platform, data_dir=data_dir)
	cause = (str(exc) or type(exc).__name__).splitlines()[0][:300]
	return WorkflowActionError(
		"BROWSER_KERNEL_MISSING",
		"patchright 浏览器内核缺失或与所需修订版不匹配",
		recoverable=recoverable,
		# 内核没装好时重试同一动作只会再起一次浏览器、得到同一结果；浏览器未启动
		# 也没有发出平台写请求，因此 replay_safe 保持 True（Issue #1543）。
		auto_retry=False,
		# 与命令层同一份可执行指引：裸 `patchright install chromium` 在
		# `uv tool install` 环境下不在 PATH 上（Issue #1060）。
		recovery_action=patchright_install_command(),
		details={"cause": cause, "doctor_command": doctor},
	)


#: 恢复时发现上一个执行器在写请求结果确认前消失，步骤与 run 落成失败时使用的说明。
_INTERRUPTED_WRITE_MESSAGE = "上一次执行在写请求结果确认前中断，无法确认操作是否已生效，禁止自动重试"

#: 中断时读不出已落盘写标记（记录损坏、字段类型漂移）的兜底记录（Issue #1478）。
#:
#: 无法证明这次写请求没有发出，就必须按「结果未知」拦下：宁可多拦一次，也不能
#: 因为类型漂移把 ``replay_safe`` 判成可重放。
_UNCONFIRMED_WRITE_FALLBACK: dict[str, Any] = {
	"code": "ACTION_UNCONFIRMED",
	"message": "写请求在途，结果尚未确认",
	"recoverable": False,
	"recovery_action": "先在官方页面核对该操作是否已生效，不要自动重试",
	"replay_safe": False,
}


def _search_pipeline_error(
	exc: SearchPipelinePlatformError,
	plan: WorkflowPlan,
	data_dir: Path | None,
	login_action: str,
) -> WorkflowActionError:
	"""搜索管线异常：保留平台错误码与登记表契约。

	搜索动作只读，但错误码决定 Agent 的下一步：登录过期要重新登录、限流要
	等待、风控要终止。此前这些异常都被 ``except Exception`` 兜成
	``NETWORK_ERROR`` +「重试当前 run_id」，Agent 会拿同一个过期登录态反复
	重试（Issue #1384）。风控码仍按 ``RISK_ERROR_CONTRACTS`` 保持终止语义，
	不透传平台响应原文。
	"""
	code = exc.code
	if code not in ERROR_CODES or code in {"NETWORK_ERROR", "UNKNOWN"}:
		# 未登记的传输类错误维持原兜底：可重试的网络问题。
		return WorkflowActionError("NETWORK_ERROR", str(exc), recoverable=True, recovery_action="重试当前 run_id")
	if code in RISK_ERROR_CONTRACTS:
		recoverable, recovery_action, _ = platform_error_contract(
			code,
			clean_command=boss_command("clean --risk-lock", platform_name=plan.platform, data_dir=data_dir),
			doctor_command=boss_command("doctor", platform_name=plan.platform, data_dir=data_dir),
		)
		return WorkflowActionError(
			code,
			risk_error_message(code),
			recoverable=recoverable,
			recovery_action=recovery_action,
		)
	message = platform_error_message(code, exc.message, "搜索结果获取失败")
	if code in {"AUTH_EXPIRED", "AUTH_REQUIRED", "TOKEN_REFRESH_FAILED"}:
		recoverable, _ = error_contract_for_code(code)
		return WorkflowActionError(
			code,
			message,
			recoverable=recoverable,
			recovery_action=login_action,
			details=exc.details,
		)
	recoverable, recovery_action, _ = platform_error_contract(code)
	return WorkflowActionError(
		code,
		message,
		recoverable=recoverable,
		recovery_action=recovery_action,
		details=exc.details,
	)


def has_blocked_step(run: Mapping[str, Any]) -> bool:
	"""是否有失败步骤声明 ``replay_safe: false``：动作可能已生效，恢复只会返回原失败。

	``--resume`` 与 TTY 的「恢复已有任务」列表都以此为准，避免同一份状态在两处
	得出不同结论（Issue #1021）。
	"""
	steps = run.get("steps")
	if not isinstance(steps, Sequence):
		return False
	for step in steps:
		if not isinstance(step, Mapping) or str(step.get("status")) not in _REPLAY_BLOCKING_STATUSES:
			continue
		error = step.get("error")
		if isinstance(error, Mapping) and error.get("replay_safe") is False:
			return True
	return False


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
		# 只看断点里的状态：叠加实时采集结果会让损坏的 crawl 记录挡住停止信号。
		run = self.store.get_checkpoint(self.run_id)
		if run is not None and run.get("status") == WorkflowStatus.STOPPED.value:
			return "stop"
		if self.deadline is not None and time.monotonic() >= self.deadline:
			return "timeout"
		return None

	def bind_inner_run(self, inner_run_id: str) -> None:
		# update_step 不带 error 会把它清空：已落盘的写前标记必须原样带回，否则
		# 中断后 --resume 会重放结果未知的写操作（Issue #1958）。
		try:
			steps = self.store.cache.list_workflow_steps(self.run_id)
		except CacheRecordInvalid:
			steps = []
		existing = next((step.get("error") for step in steps if step.get("step_name") == self.step_name), None)
		self.store.update_step(
			self.run_id,
			self.step_name,
			status=WorkflowStatus.RUNNING.value,
			result={"inner_run_id": inner_run_id},
			error=dict(existing) if isinstance(existing, Mapping) else None,
		)

	def begin_write(self, recovery_action: str) -> None:
		"""在写请求发出之前调用：先把「结果未知」落到本步骤上。

		执行器若在结果确认前消失（被 kill、终端关闭、断电），步骤会停在 running，
		它没有机会再记录任何东西；``--resume`` 凭这条预先落盘的记录拒绝重放
		（Issue #1019）。结果一旦确认——成功，或动作明确报告的失败——runner 落盘
		步骤时会用实际结果覆盖它。

		记录落不了盘就抛出：调用方不得在没有记录的情况下发出写请求。
		"""
		error = WorkflowActionError(
			"ACTION_UNCONFIRMED",
			"写请求在途，结果尚未确认",
			recoverable=False,
			recovery_action=recovery_action,
			replay_safe=False,
		)
		if not self.store.set_step_error(self.run_id, self.step_name, error.to_dict()):
			# 记录缺失是确定性的状态损坏：报成可重试的 NETWORK_ERROR 会让每次恢复
			# 都重跑前置步骤，而写请求永远发不出去（Issue #1965）。
			_, recovery = error_contract_for_code("CACHE_RECORD_INVALID")
			raise WorkflowActionError(
				"CACHE_RECORD_INVALID",
				f"workflow 步骤 {self.step_name!r}（run {self.run_id!r}）的记录不存在，写前标记无法落盘",
				# 重试同一条命令必然复现：恢复方式是重建任务，不是「重试」。
				recoverable=False,
				recovery_action=recovery,
				replay_safe=False,
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
		run_id = run_id or f"wrn_{secrets.token_urlsafe(12)}"
		# 执行权必须先于执行快照取得：两次调用都读到旧快照时，后到者会把先到者已保存
		# 的结果覆盖回 running，再把同一个写操作发一遍（Issue #1017）。
		with self._store.execution_lock(run_id) as acquired:
			if not acquired:
				status_action = boss_command(
					f"wizard --status {shell_argument(run_id)}",
					platform_name=plan.platform,
					data_dir=getattr(context, "data_dir", None),
				)
				raise WorkflowActionError(
					"WORKFLOW_BUSY",
					"该 workflow 正在由另一次调用执行，本次调用未执行任何步骤",
					recoverable=True,
					recovery_action=f"用 {status_action} 查看进度，等当前执行结束后再恢复；不要并发恢复同一个 run_id",
					details={"run_id": run_id},
				)
			return self._run_exclusive(
				plan,
				context,
				run_id=run_id,
				on_event=on_event,
				timeout_seconds=timeout_seconds,
				max_retries=max_retries,
			)

	def _run_exclusive(
		self,
		plan: WorkflowPlan,
		context: Any,
		*,
		run_id: str,
		on_event: WorkflowEvent | None,
		timeout_seconds: float | None,
		max_retries: int,
	) -> dict[str, Any]:
		"""读取执行快照并逐步执行；调用方必须已持有该 run 的执行权。"""
		started_at = time.monotonic()
		data_dir = getattr(context, "data_dir", None)
		login_action = boss_command("login", platform_name=plan.platform, data_dir=data_dir)

		def resume_action_for(target: str) -> str:
			return boss_command(
				f"wizard --resume {shell_argument(target)}", platform_name=plan.platform, data_dir=data_dir
			)
		deadline = started_at + timeout_seconds if timeout_seconds is not None else None
		# 控制读取只认断点本身：展示用的实时采集叠加损坏不能挡住恢复（Issue #1176）。
		existing = self._store.get_checkpoint(run_id)
		if existing is None:
			self._store.create(run_id, plan)
		else:
			self._assert_plan_matches(existing, plan)
			if existing["status"] == WorkflowStatus.STOPPED.value:
				return existing
		prior_results: dict[str, Any] = {}
		# waiting_input 步骤结果仍传给 action（如 crawl run_id），但不会跳过重跑。
		waiting_priors: dict[str, Any] = {}
		stored_run = self._store.get_checkpoint(run_id)
		if stored_run is None:
			raise RuntimeError(f"workflow run {run_id!r} was not persisted")
		stored_run = self._settle_interrupted_writes(run_id, stored_run)
		blocked_steps: dict[str, dict[str, Any]] = {}
		for step in stored_run["steps"]:
			error = step.get("error") or {}
			if step["status"] in _REPLAY_BLOCKING_STATUSES and error.get("replay_safe") is False:
				# 动作可能已经生效：--resume 不得再执行一次。步骤落成 stopped 而 run
				# 还没写完就中断时同样要阻断（Issue #1957）。
				blocked_steps[step["step_name"]] = error
				continue
			if step["status"] == WorkflowStatus.COMPLETED.value:
				# 已完成步骤的 result 漂移成空值也不能重跑：它可能是写操作（Issue #1959）。
				prior_results[step["step_name"]] = step["result"] or {}
				continue
			if not step["result"]:
				continue
			if step["status"] in {
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
			if stored_run["status"] not in {WorkflowStatus.FAILED.value, WorkflowStatus.STOPPED.value}:
				# 步骤失败已落盘、run 状态还没写完执行器就消失了：run 仍是 running，
				# 原样返回会把结果未知的写操作报成执行中（Issue #1129）。
				blocked_step = pending_steps[0]
				self._store.update_run(
					run_id,
					status=WorkflowStatus.FAILED.value,
					current_step=blocked_step,
					last_result=stored_run.get("last_result"),
					error=blocked_steps[blocked_step],
				)
				return self._store.get_for_result(run_id) or stored_run
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
				persisted = self._store.get_checkpoint(run_id) or {}
				self._store.update_run(
					run_id,
					status=WorkflowStatus.FAILED.value,
					current_step=step_name,
					last_result=persisted.get("last_result"),
					error=blocked_steps[step_name],
				)
				return self._store.get_for_result(run_id) or {}
			current = self._store.get_checkpoint(run_id) or {}
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
						if not exc.recoverable or not exc.auto_retry or not exc.replay_safe or attempt >= max_retries:
							raise
						attempt += 1
						control.raise_if_requested()
						# 动作明确报告了可以重放的失败：这次尝试在写请求前预先落盘的「结果未知」
						# 随之作废。留着它，下一次尝试还没发出请求就被终止也会被当成结果未知。
						self._store.set_step_error(run_id, step_name, None)
						# 动作可能在失败前已保存内部任务绑定；自动重试也要读取最新断点，
						# 否则 crawl_start 会另建任务并丢开原任务的进度与预算（Issue #972）。
						persisted = self._store.get_checkpoint(run_id) or {}
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
				if exc.code in {"WORKFLOW_STOPPED", "WORKFLOW_TIMEOUT"} and exc.replay_safe:
					# 写请求发出后的下一次控制检查抛出停止 / 超时：保留写前标记，
					# 否则 --resume 会重放结果未知的写操作（Issue #1956）。
					write_marker = self._persisted_write_marker(run_id, step_name)
					if write_marker is not None:
						error = self._unconfirmed_error(exc.code, write_marker)
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
				# 若写请求发出前已落盘「结果未知」标记，异常只改变错误原因，不能把
				# replay_safe 改回 True：否则 --resume 会重放可能已生效的写操作（Issue #1604）。
				write_marker = self._persisted_write_marker(run_id, step_name)
				if write_marker is not None:
					error = self._unconfirmed_error(exc.code, write_marker)
				else:
					recoverable, recovery = error_contract_for_code(exc.code)
					error = WorkflowActionError(exc.code, str(exc), recoverable=recoverable, recovery_action=recovery, details=exc.details)
			except KeyboardInterrupt:
				# 写请求已经发出时，``begin_write`` 已在步骤上落盘结果未知标记；用普通的
				# WORKFLOW_STOPPED 覆盖它会让 checkpoint 丢掉 replay_safe=false 与该动作的
				# 核对命令，``--resume`` 就会把结果未知的写操作再发一次（Issue #1478）。
				error = self._interrupt_error(run_id, step_name)
			except PlatformRiskError as exc:
				# 写请求在途时命中风控：保留预写的「结果未知」标记与核对命令，
				# 只让风控原因覆盖消息；否则 --resume 会重放结果未知的写操作（Issue #1604）。
				write_marker = self._persisted_write_marker(run_id, step_name)
				if write_marker is not None:
					error = self._unconfirmed_error(exc.code, write_marker)
				else:
					# 风控：终止语义不能被下面的 NETWORK_ERROR 兜底反转成「重试当前 run_id」。
					# checkpoint 照常落盘（下方 update_step/update_run），恢复只能由用户在
					# 处理完账号/安全页后显式发起（Issue #419）。解锁命令必须指向本次
					# workflow 的数据目录，否则 clean --risk-lock 会去默认目录空转（Issue #978）。
					recovery_action, _ = risk_error_contract(
						exc.code,
						clean_command=boss_command(
							"clean --risk-lock", platform_name=plan.platform, data_dir=data_dir
						),
						doctor_command=boss_command("doctor", platform_name=plan.platform, data_dir=data_dir),
					)
					error = WorkflowActionError(
						exc.code,
						risk_error_message(exc.code),
						recoverable=False,
						recovery_action=recovery_action,
					)
			except SearchPipelinePlatformError as exc:
				# 搜索管线把平台错误包成自己的异常：保留原始错误码与契约，
				# 不能兜成 NETWORK_ERROR（Issue #1384）。
				error = _search_pipeline_error(exc, plan, data_dir, login_action)
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
			except ChatTabCdpUnreachable as exc:
				# 脚本未发送，动作没有平台写入：按可重试的 CDP 不可用收口，
				# 不进入「结果未知」分支（Issue #1455）。
				recoverable, recovery = error_contract_for_code("CDP_UNAVAILABLE")
				error = WorkflowActionError(
					"CDP_UNAVAILABLE",
					f"CDP 调试端点不可达，聊天页动作尚未发送: {exc}",
					recoverable=recoverable,
					recovery_action=recovery,
				)
			except BrowserSourceUnavailable as exc:
				# 策略表的 doctor / status --live 读的是 <data_dir> 里的登录态与风险锁，
				# 这里补上本次运行的数据目录与平台（Issue #980）。
				error = WorkflowActionError(
					exc.code,
					str(exc),
					recoverable=True,
					recovery_action=browser_policy_text(
						exc.policy.recovery_action,
						lambda command: boss_command(command, platform_name=plan.platform, data_dir=data_dir),
					),
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
				# 响应无法解析（PlatformResponseInvalid）等内部错误可能在写请求
				# 发出之后才发生：与 NETWORK_ERROR 分支同口径，先读本步骤的写前
				# 标记，命中时保留原错误码并强制禁止重放（Issue #1881）。
				write_marker = self._persisted_write_marker(run_id, step_name)
				if write_marker is not None:
					error = self._unconfirmed_error("INTERNAL_ERROR", write_marker)
				else:
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
				# 但写请求发出后已落盘的结果未知标记不能被 NETWORK_ERROR 覆盖（Issue #1639）。
				write_marker = self._persisted_write_marker(run_id, step_name)
				if write_marker is not None:
					error = self._unconfirmed_error("NETWORK_ERROR", write_marker)
				elif is_browser_kernel_missing(str(exc)):
					# Playwright 的「内核缺失」也是 OSError 子类：重试只会再起一个浏览器。
					error = _browser_kernel_missing_error(exc, plan, data_dir)
				elif is_local_io_error(exc):
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
				# 写请求已发出后无论什么异常都不能把结果未知标记覆盖成「可重试」；
				# 分类照常记在错误码里，恢复时由标记强制禁止重放（Issue #1639）。
				write_marker = self._persisted_write_marker(run_id, step_name)
				if write_marker is not None:
					error = self._unconfirmed_error("NETWORK_ERROR", write_marker)
				# 代理环境变量非法是确定性配置错误：重试或恢复同一 run_id 只会
				# 复现，不能兜成 NETWORK_ERROR +「重试当前 run_id」（Issue #1423）。
				elif isinstance(exc, ProxyConfigurationError):
					invalid_recoverable, invalid_recovery = error_contract_for_code("INVALID_PARAM")
					error = WorkflowActionError(
						"INVALID_PARAM",
						str(exc),
						recoverable=invalid_recoverable,
						recovery_action=invalid_recovery,
						replay_safe=True,
					)
				# 约束冲突 / 绑定值非法是调用方输入问题，缓存库本身健康：
				# 不能兜成可重试的 NETWORK_ERROR（Issue #1886）。
				elif isinstance(exc, CacheWriteRejected):
					invalid_recoverable, invalid_recovery = error_contract_for_code("INVALID_PARAM")
					error = WorkflowActionError(
						"INVALID_PARAM",
						str(exc),
						recoverable=invalid_recoverable,
						recovery_action=invalid_recovery,
						replay_safe=True,
					)
				# 本地缓存锁冲突不是网络问题（Issue #1118）：等锁释放后重试同一
				# run_id 即可，不能兜成 NETWORK_ERROR。
				elif isinstance(exc, CacheBusyError) or is_cache_busy_error(exc):
					busy_recoverable, busy_recovery = error_contract_for_code("CACHE_BUSY")
					error = WorkflowActionError(
						"CACHE_BUSY",
						str(exc) if isinstance(exc, CacheBusyError) else f"本地缓存库正被其他进程写入（{exc}）",
						recoverable=busy_recoverable,
						recovery_action=busy_recovery,
					)
				# 缓存层不再把只读目录 / 磁盘满等本地 IO 故障包装成
				# CacheDatabaseInvalid（Issue #1467）；这些原始 sqlite 错误同样不能
				# 落成可重试的 NETWORK_ERROR，否则 Agent 会在同一份只读库上反复
				# 恢复 run_id。
				elif isinstance(exc, sqlite3.DatabaseError):
					io_recoverable, io_recovery = error_contract_for_code("LOCAL_IO_ERROR")
					error = WorkflowActionError(
						"LOCAL_IO_ERROR",
						f"本地数据库文件无法打开: {exc}",
						recoverable=io_recoverable,
						recovery_action=io_recovery,
						replay_safe=False,
					)
				# 内核缺失是确定性失败：同一条输入（含 --resume）永远复现，不能兜成
				# NETWORK_ERROR +「重试当前 run_id」（Issue #1007）。
				elif is_browser_kernel_missing(str(exc)):
					error = _browser_kernel_missing_error(exc, plan, data_dir)
				# 缓存库损坏（含取数阶段才暴露的坏页）是确定性本地失败：重试或恢复
				# 只会撞上同一个坏库，不能兜成 NETWORK_ERROR +「重试当前 run_id」
				# （Issue #1362）。与 CLI 路径的 CACHE_DATABASE_INVALID 同口径。
				elif isinstance(exc, CacheDatabaseInvalid):
					db_recoverable, db_recovery = error_contract_for_code("CACHE_DATABASE_INVALID")
					error = WorkflowActionError(
						"CACHE_DATABASE_INVALID",
						str(exc),
						recoverable=db_recoverable,
						recovery_action=db_recovery,
						replay_safe=False,
					)
				# 本地任务记录写坏同属确定性失败：重试同一条记录永远复现，不能兜成
				# NETWORK_ERROR +「重试当前 run_id」（Issue #1286）。
				elif isinstance(exc, CacheRecordInvalid):
					record_recoverable, record_recovery = error_contract_for_code("CACHE_RECORD_INVALID")
					error = WorkflowActionError(
						"CACHE_RECORD_INVALID",
						str(exc),
						recoverable=record_recoverable,
						recovery_action=record_recovery,
						replay_safe=False,
					)
				# 采集配置目录路径不可用是确定性本地失败：重试或恢复只会复现
				# （Issue #1290），与 CLI 路径的 LOCAL_IO_ERROR 同口径。
				elif isinstance(exc, CrawlProfilePathInvalid):
					io_recoverable, _ = error_contract_for_code("LOCAL_IO_ERROR")
					error = WorkflowActionError(
						"LOCAL_IO_ERROR",
						str(exc),
						recoverable=io_recoverable,
						recovery_action=(
							"检查数据目录下的 crawl/chrome-profile 是否被同名文件占用、"
							"目录权限与磁盘空间是否正常，修正后重新执行"
						),
						replay_safe=False,
					)
				else:
					error = WorkflowActionError(
						"NETWORK_ERROR", str(exc), recoverable=True, recovery_action="重试当前 run_id"
					)
			else:
				result_data = result.to_dict()
				prior_results[step_name] = result_data
				# 通用 update_step 会把步骤 error 清空；声明失败的 action 若已
				# begin_write，必须在覆盖前抓住写前标记（Issue #1882）。
				declared_marker = (
					self._persisted_write_marker(run_id, step_name)
					if result.status not in {WorkflowStatus.COMPLETED, WorkflowStatus.WAITING_INPUT}
					else None
				)
				# A returned result confirms the action's outcome. Persist it before
				# checking controls, so a late deadline cannot replay that effect.
				# 落盘失败不能以可重试错误逃逸：步骤已成功而结果没写下去，重试会重发
				# 一次平台请求（Issue #1963）。
				try:
					step_persisted = self._store.update_step(
						run_id, step_name, status=result.status.value, result=result_data
					)
				except Exception as exc:
					raise self._write_failed_error(
						{"code": "LOCAL_STATE_WRITE_FAILED", "message": f"步骤 {step_name} 的结果未能落盘"},
						exc,
					) from exc
				if not step_persisted:
					raise self._write_failed_error(
						{"code": "CACHE_RECORD_INVALID", "message": f"步骤 {step_name} 的记录已不存在"},
						CacheRecordInvalid("workflow_step", f"{run_id}/{step_name}", "step", "步骤记录不存在"),
					)
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
				try:
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
				except Exception as exc:
					raise self._write_failed_error(
						{"code": "LOCAL_STATE_WRITE_FAILED", "message": f"run {run_id} 的状态未能落盘"},
						exc,
					) from exc
				if on_event:
					on_event("step_finished", step_name, result_data)
				if signal == "stop":
					return self._store.get_for_result(run_id) or {}
				if result.status == WorkflowStatus.WAITING_INPUT:
					return self._store.get_for_result(run_id) or {}
				if result.status != WorkflowStatus.COMPLETED:
					# StepResult.status 是完整 WorkflowStatus，自定义 action 可以
					# 返回 FAILED / STOPPED。以前只把值抄进步骤状态，run 继续执行
					# 并最终落成 completed、error=None（Issue #1882）；这里按失败
					# 终止，不再把 action 已声明的失败当成成功继续。
					return self._finish_declared_status(run_id, step_name, result, result_data, declared_marker)
				continue

			error_data = error.to_dict()
			failed_status = (
				WorkflowStatus.STOPPED.value if error.code == "WORKFLOW_STOPPED" else WorkflowStatus.FAILED.value
			)
			try:
				persisted = self._store.get_checkpoint(run_id) or {}
			except CacheRecordInvalid:
				# 记录里已有类型漂移（如被中断的写标记 replay_safe 不是布尔值）：
				# 继续用已知的错误落盘覆盖它，不能因此让中断保护失效（Issue #1478）。
				persisted = {}
			step_result = next(
				(step.get("result") for step in persisted.get("steps", []) if step["step_name"] == step_name),
				None,
			)
			self._persist_failure(
				run_id, step_name, status=failed_status, result=step_result, error_data=error_data
			)
			if on_event:
				on_event("step_failed", step_name, error_data)
			return self._store.get_for_result(run_id) or {}

		try:
			self._store.update_run(
				run_id,
				status=WorkflowStatus.COMPLETED.value,
				current_step=None,
				last_result=prior_results.get(plan.requested_steps[-1]) if plan.requested_steps else {},
			)
		except Exception as exc:
			# 平台步骤都成功了，只是本地记账失败：按 LOCAL_STATE_WRITE_FAILED 上报，
			# 让操作者核对页面而不是重跑整个 workflow（Issue #1963）。
			raise self._write_failed_error({"code": WorkflowStatus.COMPLETED.value}, exc) from exc
		return self._store.get_for_result(run_id) or {}

	def _finish_declared_status(
		self,
		run_id: str,
		step_name: str,
		result: StepResult,
		result_data: dict[str, Any],
		marker: dict[str, Any] | None = None,
	) -> dict[str, Any]:
		"""把 action 显式返回的失败状态落成 run 级失败（Issue #1882）。

		``StepResult.status`` 允许完整 ``WorkflowStatus``，但 runner 只实现了
		COMPLETED / WAITING_INPUT 两种终态；FAILED、STOPPED 以及非终态的
		PENDING / RUNNING 都必须终止 workflow，不能继续执行后续步骤后报 completed。
		"""
		if result.status == WorkflowStatus.STOPPED:
			code = "WORKFLOW_STOPPED"
			message = f"步骤 {step_name!r} 返回 stopped，workflow 按停止处理"
			failed_status = WorkflowStatus.STOPPED.value
		else:
			code = "INTERNAL_ERROR"
			message = f"步骤 {step_name!r} 返回非终态 {result.status.value}，workflow 按失败终止"
			failed_status = WorkflowStatus.FAILED.value
		recoverable, recovery = error_contract_for_code(code)
		if marker is not None:
			# 写请求可能已发出：保留原码与核对信息，禁止重放。
			error = self._unconfirmed_error(code, marker)
		else:
			error = WorkflowActionError(
				code, message, recoverable=recoverable, recovery_action=recovery
			)
		error_data = error.to_dict()
		self._persist_failure(
			run_id,
			step_name,
			status=failed_status,
			result=result_data,
			error_data=error_data,
			last_result=result_data,
		)
		return self._store.get_for_result(run_id) or {}

	def _interrupt_error(self, run_id: str, step_name: str) -> WorkflowActionError:
		"""Ctrl-C 时保留已落盘的「写请求在途」标记（Issue #1478）。

		只有本步骤没有该标记时才落成普通的 ``WORKFLOW_STOPPED``。标记存在（或读不出来）
		时保留原错误码、核对命令与 ``replay_safe=false``，把步骤落成 failed，让
		``--resume`` 与 ``--status`` 都按「结果未知、禁止重放」处理。
		"""
		marker = self._persisted_write_marker(run_id, step_name)
		if marker is None:
			return WorkflowActionError(
				"WORKFLOW_STOPPED",
				"workflow 被中断；开始新的运行前请先核对未完成的操作",
				recoverable=True,
				recovery_action="检查 checkpoint 与平台实际结果，再显式创建或恢复内部任务",
			)
		message = marker.get("message")
		text = message if isinstance(message, str) and message else _UNCONFIRMED_WRITE_FALLBACK["message"]
		if "中断" not in text:
			# 合并中断文案，但不丢掉「结果尚未确认」这个核心事实。
			text = f"{text}（workflow 被中断）"
		code = marker.get("code")
		recoverable = marker.get("recoverable")
		recovery_action = marker.get("recovery_action")
		details = marker.get("details")
		return WorkflowActionError(
			code if isinstance(code, str) and code else _UNCONFIRMED_WRITE_FALLBACK["code"],
			text,
			recoverable=recoverable if isinstance(recoverable, bool) else False,
			recovery_action=recovery_action
			if isinstance(recovery_action, str) and recovery_action
			else _UNCONFIRMED_WRITE_FALLBACK["recovery_action"],
			details=dict(details) if isinstance(details, Mapping) else None,
			# 这条路径只处理「写请求可能已发出」：无论原记录怎么写，都不得重放。
			replay_safe=False,
		)

	def _unconfirmed_error(self, code: str, marker: dict[str, Any]) -> WorkflowActionError:
		"""把已落盘的在途写标记转成带原错误码的「结果未知」失败（Issue #1604）。"""
		message = marker.get("message")
		text = message if isinstance(message, str) and message else _UNCONFIRMED_WRITE_FALLBACK["message"]
		recovery = marker.get("recovery_action")
		return WorkflowActionError(
			code,
			text,
			recoverable=False,
			recovery_action=recovery if isinstance(recovery, str) and recovery else _UNCONFIRMED_WRITE_FALLBACK["recovery_action"],
			details=dict(marker["details"]) if isinstance(marker.get("details"), Mapping) else None,
			replay_safe=False,
		)

	def _persist_failure(
		self,
		run_id: str,
		step_name: str,
		*,
		status: str,
		result: dict[str, Any] | None,
		error_data: dict[str, Any],
		last_result: dict[str, Any] | None = None,
	) -> None:
		"""落盘失败步骤与 run 终态；本地写失败不能掩盖原始失败原因（Issue #1963）。

		缓存库被其它进程持写锁时 ``update_step`` 会抛 ``CacheBusyError``：直接
		冒泡会让风控 / 预算这类终止码变成「可重试的缓存忙」，Agent 会照着重试
		一个本该停下来核对页面的动作。
		"""
		try:
			persisted = self._store.update_step(
				run_id, step_name, status=status, result=result, error=error_data
			)
			if not persisted:
				raise CacheRecordInvalid(
					"workflow_step",
					f"{run_id}/{step_name}",
					"step",
					"步骤记录不存在，无法落盘执行结果",
				)
			self._store.update_run(
				run_id, status=status, current_step=step_name, last_result=last_result, error=error_data
			)
		except Exception as exc:
			raise self._write_failed_error(error_data, exc) from exc

	def _write_failed_error(self, error_data: dict[str, Any], exc: Exception) -> WorkflowActionError:
		"""把「原始失败 + 断点写失败」合成一条不可重试的终止错误。"""
		code = error_data.get("code")
		message = error_data.get("message")
		detail = f"本地断点写入失败：{exc}"
		already_closed = error_data.get("recoverable") is False and error_data.get("replay_safe") is False
		no_effect = error_data.get("replay_safe") is False
		if isinstance(code, str) and (code in _RISK_CODES or (already_closed and no_effect)):
			# 风控是硬边界：原码与停止指引优先，只把写失败附在消息里。
			recovery = error_data.get("recovery_action")
			return WorkflowActionError(
				code,
				f"原始失败：{message if isinstance(message, str) and message else '未知失败'}；本地断点写入失败：{detail}",
				recoverable=False,
				recovery_action=recovery if isinstance(recovery, str) and recovery else None,
				replay_safe=False,
			)
		recoverable, recovery = error_contract_for_code("LOCAL_STATE_WRITE_FAILED")
		return WorkflowActionError(
			"LOCAL_STATE_WRITE_FAILED",
			f"{detail}；原始失败：{code if isinstance(code, str) else '未知'}",
			recoverable=recoverable,
			recovery_action=recovery,
			details={"original_code": code, "original_message": message},
			replay_safe=False,
		)

	def _persisted_write_marker(self, run_id: str, step_name: str) -> dict[str, Any] | None:
		"""读取本步骤已落盘的写前标记；返回 None 表示没有写请求在途的记录。

		读取走原始步骤列表而不是 ``get_checkpoint``：后者会按记录损坏抛
		``CacheRecordInvalid``，而中断路径恰恰不能因此丢掉保护。error 不是对象、
		``replay_safe`` 不是布尔值时按「结果未知」处理（fail-closed）。
		"""
		try:
			steps = self._store.cache.list_workflow_steps(run_id)
		except CacheRecordInvalid:
			return dict(_UNCONFIRMED_WRITE_FALLBACK)
		error = next((step.get("error") for step in steps if step.get("step_name") == step_name), None)
		if error is None:
			return None
		if not isinstance(error, Mapping):
			return dict(_UNCONFIRMED_WRITE_FALLBACK)
		replay_safe = error.get("replay_safe")
		if replay_safe is not None and not isinstance(replay_safe, bool):
			# 类型漂移（如 "false"）：不能按「可重放」放行，但保留可抢救的核对信息。
			marker = dict(_UNCONFIRMED_WRITE_FALLBACK)
			for key in ("message", "recovery_action"):
				value = error.get(key)
				if isinstance(value, str) and value:
					marker[key] = value
			details = error.get("details")
			if isinstance(details, Mapping):
				marker["details"] = dict(details)
			return marker
		if error.get("code") == _UNCONFIRMED_WRITE_FALLBACK["code"] or replay_safe is False:
			return dict(error)
		return None

	def _settle_interrupted_writes(self, run_id: str, stored_run: dict[str, Any]) -> dict[str, Any]:
		"""把上一个执行器留下的在途写请求落成「结果未知」的失败。

		调用方持有执行权，所以仍是 running 的步骤不可能还在执行：上一个执行器没有
		记录结果就消失了。步骤若带着写请求发出前预先落盘的记录（``begin_write``），
		这次写操作可能已经生效，也可能没有——不重放，交给操作者核对（Issue #1019）。
		没有这条记录的 running 步骤（只读、内部任务续跑、尚未走到写请求）照常重跑。
		"""
		interrupted: list[tuple[dict[str, Any], dict[str, Any]]] = []
		for step in stored_run["steps"]:
			if step["status"] != WorkflowStatus.RUNNING.value:
				continue
			# _persisted_write_marker 可能把缺失 / 漂移的 replay_safe 规范化成
			# 后备标记；结算时也用规范化结果，不能把不完整的记录原样写回。
			marker = self._persisted_write_marker(run_id, step["step_name"])
			if marker is not None:
				interrupted.append((step, marker))
		if not interrupted:
			return stored_run
		for step, marker in interrupted:
			self._store.update_step(
				run_id,
				step["step_name"],
				status=WorkflowStatus.FAILED.value,
				result=step["result"],
				error={**marker, "message": _INTERRUPTED_WRITE_MESSAGE, "replay_safe": False},
			)
		# 步骤按顺序执行，正常情况下只有一个；run 指向最先该执行的那一步。
		first, first_marker = interrupted[0]
		self._store.update_run(
			run_id,
			status=WorkflowStatus.FAILED.value,
			current_step=first["step_name"],
			error={**first_marker, "message": _INTERRUPTED_WRITE_MESSAGE, "replay_safe": False},
		)
		return self._store.get_checkpoint(run_id) or stored_run

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
		if not WorkflowRunner._plans_equal(persisted, requested):
			raise WorkflowActionError(
				"WORKFLOW_PLAN_MISMATCH",
				"run_id 已绑定到不同的 workflow plan",
				recoverable=False,
				recovery_action="使用原 plan 恢复，或不传 run_id 创建新任务",
			)

	@staticmethod
	def _plans_equal(persisted: Any, requested: Any) -> bool:
		"""比较落库参数与本次 plan，NaN 按同值处理（Issue #1883）。

		``json.loads`` 接受 ``NaN``，而 ``nan != nan`` 恒真：直接 ``!=`` 会让同一个
		含 NaN 的 plan 恢复时永远命中 ``WORKFLOW_PLAN_MISMATCH``（recoverable=False），
		run 永久不可恢复。先按值比较，再退到规范化 JSON 文本比较——两侧对 NaN
		都序列化成 ``NaN``。
		"""
		if persisted == requested:
			return True
		try:
			return json.dumps(persisted, sort_keys=True) == json.dumps(requested, sort_keys=True)
		except (TypeError, ValueError, RecursionError):
			return False

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
		stored_run = self._store.get_checkpoint(run_id) or {}
		step_result = next(
			(step.get("result") for step in stored_run.get("steps", []) if step["step_name"] == step_name),
			None,
		)
		try:
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
		except Exception as exc:
			raise self._write_failed_error(error, exc) from exc
		return self._store.get_for_result(run_id) or {}
