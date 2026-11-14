"""Workflow persistence backed by the existing CacheStore database."""

from __future__ import annotations

from contextlib import AbstractContextManager
from pathlib import Path
from types import TracebackType
from typing import Any

from boss_agent_cli.cache.store import CacheRecordInvalid, CacheStore
from boss_agent_cli.services.file_lock import exclusive_file_lock, run_lock_path
from boss_agent_cli.wizard.models import WorkflowPlan


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

	def get(self, run_id: str) -> dict[str, Any] | None:
		run = self._cache.get_workflow_run(run_id)
		if run is not None:
			run["steps"] = self._cache.list_workflow_steps(run_id)
			from boss_agent_cli.wizard.live_crawl import BROWSE_JOB_LIMIT, enrich_run_with_live_crawl

			# Full browseable job page for status / follow-up (still paginated in TUI).
			run = enrich_run_with_live_crawl(run, self._cache, job_limit=BROWSE_JOB_LIMIT)
		return run

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
				# 绑定的 crawl 记录写坏时同样读不出状态，与 run 本体损坏同等对待。
				enriched.append(enrich_run_with_live_crawl(run, self._cache, job_limit=LIST_ENRICH_JOB_LIMIT))
			except CacheRecordInvalid:
				corrupt.append(run_id)
		self._corrupt_run_ids = corrupt
		return sort_runs_for_status_picker(enriched)

	@property
	def corrupt_run_ids(self) -> list[str]:
		"""上一次 list_recent 跳过的损坏 run_id（读不出断点，只能重建）。"""
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
		run = self.get(run_id)
		if run is None or not self._cache.request_workflow_stop(run_id):
			return False
		for step in run["steps"]:
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
