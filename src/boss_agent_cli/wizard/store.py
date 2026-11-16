"""Workflow persistence backed by the existing CacheStore database."""

from __future__ import annotations

from contextlib import AbstractContextManager
from pathlib import Path
from types import TracebackType
from typing import Any

from boss_agent_cli.cache.store import CacheRecordInvalid, CacheStore
from boss_agent_cli.services.file_lock import exclusive_file_lock, run_lock_path
from boss_agent_cli.wizard.models import WorkflowPlan, WorkflowStatus

_VALID_WORKFLOW_STATUSES = frozenset(status.value for status in WorkflowStatus)


def _validate_replay_markers(run: dict[str, Any]) -> dict[str, Any]:
	"""``error.replay_safe`` 只能是布尔值或缺省。

	恢复逻辑按 ``is False`` 判断「动作可能已生效、不得重放」。标记漂移成 ``0`` /
	``"false"`` / ``null`` 时这条判断不成立，结果未知的写步骤会被再执行一次；
	与其猜测原值，不如在读取边界按记录损坏拒绝，且不改写断点。
	"""
	run_id = str(run.get("run_id") or "")
	records = [("workflow_run", run_id, run.get("error"))]
	records.extend(
		("workflow_step", f"{run_id}/{step.get('step_name')}", step.get("error")) for step in run.get("steps") or []
	)
	for kind, record_id, error in records:
		if isinstance(error, dict) and "replay_safe" in error and not isinstance(error["replay_safe"], bool):
			raise CacheRecordInvalid(
				kind,
				record_id,
				"error.replay_safe",
				"必须是布尔值；该步骤的写操作可能已经生效，重建任务前先核对平台实际状态",
			)
	return run


def _validate_statuses(run: dict[str, Any]) -> dict[str, Any]:
	"""run 与每个 step 的 ``status`` 必须是 ``WorkflowStatus`` 的枚举值。

	恢复去重按 ``status == "completed"`` 判断，结算中断写请求按
	``status == "running"`` 判断。属性漂移成 ``"Completed"`` / ``NULL`` / 数字
	后两条判断都不成立：已完成的写步骤会被重跑，``replay_safe: false`` 的在途
	步骤也不会结算成 blocked。状态没有可猜测的原值，读取边界按记录损坏拒绝，
	不改写断点。
	"""
	run_id = str(run.get("run_id") or "")
	records = [("workflow_run", run_id, run.get("status"))]
	records.extend(
		("workflow_step", f"{run_id}/{step.get('step_name')}", step.get("status")) for step in run.get("steps") or []
	)
	for kind, record_id, status in records:
		if isinstance(status, str) and status in _VALID_WORKFLOW_STATUSES:
			continue
		raise CacheRecordInvalid(
			kind,
			record_id,
			"status",
			f"必须是 {' / '.join(sorted(_VALID_WORKFLOW_STATUSES))} 之一，实际是 {status!r}；"
			"状态无法识别时不能判断写步骤是否已完成或在途，禁止恢复或重放",
		)
	return run


class WorkflowStore:
	def __init__(self, data_dir: Path) -> None:
		self._cache = CacheStore(data_dir / "cache" / "boss_agent.db")
		self._lock_dir = data_dir / "cache" / "workflow-locks"
		self._corrupt_run_ids: list[str] = []

	def execution_lock(self, run_id: str) -> AbstractContextManager[bool]:
		"""同一 run 的执行权：跨进程独占、不等待，进程退出时由操作系统释放。

		只约束「执行步骤」。状态查询与 stop 请求不取这把锁，执行期间照常可用；
		不同 run 各用各的锁文件，互不阻塞。文件名取 run_id 的摘要，调用方传入
		的任意字符串都不会变成路径的一部分。
		"""
		return exclusive_file_lock(run_lock_path(self._lock_dir, run_id))

	def create(self, run_id: str, plan: WorkflowPlan) -> None:
		self._cache.create_workflow_run(
			run_id,
			role=plan.role,
			platform=plan.platform,
			goal=plan.goal,
			mode=plan.mode,
			params={"inputs": dict(plan.inputs), "requested_steps": list(plan.requested_steps)},
			steps=plan.requested_steps,
		)

	def get_checkpoint(self, run_id: str, *, validate_replay_markers: bool = True) -> dict[str, Any] | None:
		"""只读 workflow 自己的断点（run + steps），不叠加内部 crawl 的实时结果。

		停止这类控制操作只依赖断点本身：绑定的 crawl 结果读不出来时，
		也必须能落下停止标记（Issue #1128）。``validate_replay_markers=False``
		只供停止后的展示使用：停止已经落盘，不因重放标记或状态漂移把它报成失败。
		"""
		run = self._cache.get_workflow_run(run_id)
		if run is not None:
			run["steps"] = self._cache.list_workflow_steps(run_id)
			if validate_replay_markers:
				_validate_replay_markers(run)
				_validate_statuses(run)
		return run

	def get(self, run_id: str) -> dict[str, Any] | None:
		run = self.get_checkpoint(run_id)
		if run is not None:
			from boss_agent_cli.wizard.live_crawl import BROWSE_JOB_LIMIT, enrich_run_with_live_crawl

			# Full browseable job page for status / follow-up (still paginated in TUI).
			run = enrich_run_with_live_crawl(run, self._cache, job_limit=BROWSE_JOB_LIMIT)
		return run

	def get_for_result(self, run_id: str) -> dict[str, Any] | None:
		"""执行结果读取：优先带实时采集叠加，叠加数据损坏时退回断点本身。

		执行结束后返回给命令层的是展示用的完整任务；内部 crawl 记录损坏只影响
		jobs / live_status 这些叠加字段，不能把已经执行完的任务报成
		`CACHE_RECORD_INVALID`。断点自身损坏仍按原状抛出（与 #1128 同族）。
		"""
		try:
			return self.get(run_id)
		except CacheRecordInvalid:
			return self.get_checkpoint(run_id)

	def list_recent(self, *, limit: int = 20) -> list[dict[str, Any]]:
		from boss_agent_cli.wizard.live_crawl import (
			LIST_ENRICH_JOB_LIMIT,
			enrich_run_with_live_crawl,
			sort_runs_for_status_picker,
		)

		runs, corrupt = self._cache.list_workflow_runs_with_corrupt(limit=limit)
		enriched: list[dict[str, Any]] = []
		for run in runs:
			run_id = str(run["run_id"])
			try:
				run["steps"] = self._cache.list_workflow_steps(run_id)
				_validate_replay_markers(run)
				_validate_statuses(run)
			except CacheRecordInvalid:
				corrupt.append(run_id)
				continue
			try:
				enriched.append(enrich_run_with_live_crawl(run, self._cache, job_limit=LIST_ENRICH_JOB_LIMIT))
			except CacheRecordInvalid:
				# 叠加的实时采集读不出来只影响展示字段（jobs / live_status），
				# 断点与步骤完好就仍可恢复；不能把任务丢进 corrupt 名单（Issue #1181）。
				enriched.append(run)
		self._corrupt_run_ids = corrupt
		return sort_runs_for_status_picker(enriched)

	@property
	def corrupt_run_ids(self) -> list[str]:
		"""上一次 list_recent 跳过的损坏 run_id（断点读不出，只能重建）。"""
		return list(self._corrupt_run_ids)

	@property
	def cache(self) -> CacheStore:
		"""Expose CacheStore for live crawl enrich helpers outside the store."""
		return self._cache

	def update_run(self, run_id: str, **kwargs: Any) -> bool:
		# Keep stop requests atomic with respect to worker result updates. A
		# confirmed step may still be committed, but cannot restart this run.
		return self._cache.update_workflow_run(run_id, preserve_stopped=True, **kwargs)

	def update_step(self, run_id: str, step_name: str, **kwargs: Any) -> bool:
		return self._cache.update_workflow_step(run_id, step_name, **kwargs)

	def set_step_error(self, run_id: str, step_name: str, error: dict[str, Any] | None) -> bool:
		"""只改步骤的 error：状态、结果（含内部任务绑定）与时间戳保持原样。"""
		return self._cache.set_workflow_step_error(run_id, step_name, error)

	def request_stop(self, run_id: str) -> bool:
		# 先落盘停止标记，再读断点传给内部 crawl：断点或采集结果读不出来
		# 都不能挡住停止本身（Issue #1128）。
		if not self._cache.request_workflow_stop(run_id):
			return False
		try:
			steps = self._cache.list_workflow_steps(run_id)
		except CacheRecordInvalid:
			# 步骤记录损坏时无法确定绑定的内部任务；workflow 已停止，
			# 内部 crawl 需由操作者用 crawl stop 单独处理。
			return True
		for step in steps:
			result = step.get("result") or {}
			inner_run_id = result.get("inner_run_id")
			if inner_run_id is None and isinstance(result.get("data"), dict):
				inner_run_id = result["data"].get("run_id")
			if inner_run_id:
				self._cache.request_crawl_stop(str(inner_run_id))
		return True

	def close(self) -> None:
		self._cache.close()

	def __enter__(self) -> "WorkflowStore":
		return self

	def __exit__(
		self,
		exc_type: type[BaseException] | None,
		exc_val: BaseException | None,
		exc_tb: TracebackType | None,
	) -> None:
		self.close()
