"""Workflow persistence backed by the existing CacheStore database."""

from __future__ import annotations

from pathlib import Path
from types import TracebackType
from typing import Any

from boss_agent_cli.cache.store import CacheStore
from boss_agent_cli.wizard.models import WorkflowPlan


class WorkflowStore:
	def __init__(self, data_dir: Path) -> None:
		self._cache = CacheStore(data_dir / "cache" / "boss_agent.db")

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
			enrich_runs_with_live_crawl,
			sort_runs_for_status_picker,
		)

		runs = self._cache.list_workflow_runs(limit=limit)
		for run in runs:
			run["steps"] = self._cache.list_workflow_steps(str(run["run_id"]))
		# List menu: counts + sample titles only — no full 75-row dump.
		enriched = enrich_runs_with_live_crawl(runs, self._cache, job_limit=LIST_ENRICH_JOB_LIMIT)
		return sort_runs_for_status_picker(enriched)

	@property
	def cache(self) -> CacheStore:
		"""Expose CacheStore for live crawl enrich helpers outside the store."""
		return self._cache

	def update_run(self, run_id: str, **kwargs: Any) -> bool:
		return self._cache.update_workflow_run(run_id, **kwargs)

	def update_step(self, run_id: str, step_name: str, **kwargs: Any) -> bool:
		return self._cache.update_workflow_step(run_id, step_name, **kwargs)

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
