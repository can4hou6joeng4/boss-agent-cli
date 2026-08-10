"""Merge live crawl_runs / crawl_jobs into workflow run payloads for TUI."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any, Mapping

from boss_agent_cli.cache.store import CacheStore

_CRAWL_GOALS = frozenset({"crawl_start", "crawl_resume", "crawl_status", "crawl_stop"})

# Summary panel: few sample lines only (full list is on-demand in the menu).
SAMPLE_TITLE_LIMIT = 5
# Task picker list: counts + samples, no full job array.
LIST_ENRICH_JOB_LIMIT = 0
# Single-run browse: load enough rows for paginated TUI selection.
BROWSE_JOB_LIMIT = 200
# Local description clip when user opens one crawl row.
LOCAL_DESC_LIMIT = 480


def is_crawl_goal(goal: Any) -> bool:
	return str(goal or "") in _CRAWL_GOALS


def inner_crawl_run_id(run: Mapping[str, Any]) -> str | None:
	"""Extract the crawler run_id nested in a workflow last_result / params."""
	last = run.get("last_result")
	if isinstance(last, Mapping):
		data = last.get("data")
		if isinstance(data, Mapping) and data.get("run_id"):
			return str(data["run_id"])
		if last.get("run_id"):
			return str(last["run_id"])
	params = run.get("params")
	if isinstance(params, Mapping):
		inputs = params.get("inputs")
		if isinstance(inputs, Mapping):
			for key in ("run_id", "crawl_run_id"):
				if inputs.get(key):
					return str(inputs[key])
	return None


def _output_paths(output_dir: str | None) -> dict[str, str]:
	if not output_dir:
		return {}
	base = Path(output_dir)
	paths: dict[str, str] = {}
	for kind in ("json", "csv", "xlsx"):
		path = base / f"jobs.{kind}"
		if path.exists():
			paths[kind] = str(path)
	return paths


def _job_row(item: Mapping[str, Any], *, include_description: bool = True) -> dict[str, Any]:
	payload = item.get("payload") if isinstance(item.get("payload"), Mapping) else {}
	if not isinstance(payload, Mapping):
		payload = {}
	row: dict[str, Any] = {
		"title": str(payload.get("title") or ""),
		"company": str(payload.get("company") or ""),
		"city": str(payload.get("city") or ""),
		"salary": str(payload.get("salary") or ""),
		"experience": str(payload.get("experience") or ""),
		"education": str(payload.get("education") or ""),
		"security_id": str(payload.get("security_id") or ""),
		"job_id": str(payload.get("job_id") or ""),
		"boss_name": str(payload.get("boss_name") or ""),
		"boss_title": str(payload.get("boss_title") or ""),
		"labels": payload.get("labels") or payload.get("benefits") or [],
		"selector": item.get("selector"),
		"crawl_page": item.get("page_no"),
		"detail_done": bool(item.get("detail_done")),
		"source": "crawl",
	}
	if include_description:
		desc = str(payload.get("post_description") or payload.get("description") or "")
		if len(desc) > LOCAL_DESC_LIMIT:
			desc = desc[:LOCAL_DESC_LIMIT] + "…"
		row["description"] = desc
	return row


def jobs_for_wizard(
	cache: CacheStore,
	crawl_run_id: str,
	*,
	limit: int = BROWSE_JOB_LIMIT,
	offset: int = 0,
	include_description: bool = True,
) -> tuple[list[dict[str, Any]], int]:
	"""Return (page of jobs with ids for follow-up, total count).

	limit=0 returns no rows (count-only / sample path still uses a tiny slice).
	"""
	items = cache.list_crawl_jobs(crawl_run_id)
	total = len(items)
	if limit <= 0:
		return [], total
	start = max(0, offset)
	end = start + limit
	jobs = [
		_job_row(item, include_description=include_description)
		for item in items[start:end]
		if isinstance(item, Mapping)
	]
	return jobs, total


def sample_titles_for_wizard(cache: CacheStore, crawl_run_id: str, *, limit: int = SAMPLE_TITLE_LIMIT) -> list[str]:
	jobs, _ = jobs_for_wizard(cache, crawl_run_id, limit=max(1, limit), include_description=False)
	return [f"{job.get('title') or '未命名'} · {job.get('company') or '-'}" for job in jobs[:limit]]


def enrich_run_with_live_crawl(
	run: Mapping[str, Any],
	cache: CacheStore,
	*,
	job_limit: int = BROWSE_JOB_LIMIT,
) -> dict[str, Any]:
	"""Copy a workflow run and overlay live crawl status + job list when applicable.

	Stale waiting_input rows that share an already-completed crawl run still surface
	jobs / output paths so the TUI does not look empty after a successful resume.

	job_limit controls how many full rows are attached for on-demand browsing.
	Use 0 for list/status pickers (counts + sample titles only).
	"""
	enriched: dict[str, Any] = dict(run)
	goal = str(run.get("goal") or "")
	if not is_crawl_goal(goal):
		return enriched

	crawl_id = inner_crawl_run_id(run)
	if not crawl_id:
		return enriched

	crawl = cache.get_crawl_run(crawl_id)
	if crawl is None:
		return enriched

	jobs, total = jobs_for_wizard(cache, crawl_id, limit=job_limit)
	samples = sample_titles_for_wizard(cache, crawl_id, limit=SAMPLE_TITLE_LIMIT)
	paths = _output_paths(crawl.get("output_dir"))
	last = dict(run.get("last_result") or {}) if isinstance(run.get("last_result"), Mapping) else {}
	data = dict(last.get("data") or {}) if isinstance(last.get("data"), Mapping) else {}

	data.update(
		{
			"run_id": crawl_id,
			"status": crawl.get("status") or data.get("status"),
			"query": (crawl.get("params") or {}).get("query") or data.get("query") or "",
			"city_code": (crawl.get("params") or {}).get("city_code") or data.get("city_code") or "",
			"next_page": crawl.get("next_page", data.get("next_page")),
			"pages_completed": max(0, int(crawl.get("next_page") or 1) - 1),
			"list_finished": crawl.get("list_finished", data.get("list_finished")),
			"jobs_seen": total,
			"jobs": jobs,
			"sample_titles": samples,
			"error": crawl.get("error") or data.get("error") or "",
			"output_dir": crawl.get("output_dir") or data.get("output_dir"),
			"output_paths": paths or data.get("output_paths") or {},
			"live_crawl_status": crawl.get("status"),
		}
	)
	if paths:
		last["artifacts"] = list(paths.values())
	last["data"] = data
	enriched["last_result"] = last
	enriched["live_crawl_status"] = crawl.get("status")
	enriched["live_jobs_seen"] = total
	# Surface effective status for TUI: completed crawl beats stale waiting_input.
	if str(crawl.get("status")) == "completed" and total > 0:
		enriched["display_status"] = "completed"
		enriched["effective_completed"] = True
	else:
		enriched["display_status"] = str(run.get("status") or "")
		enriched["effective_completed"] = False
	return enriched


def enrich_runs_with_live_crawl(
	runs: Sequence[Mapping[str, Any]],
	cache: CacheStore,
	*,
	job_limit: int = LIST_ENRICH_JOB_LIMIT,
) -> list[dict[str, Any]]:
	return [enrich_run_with_live_crawl(run, cache, job_limit=job_limit) for run in runs]


def sort_runs_for_status_picker(runs: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
	"""Prefer completed crawls with jobs; de-prioritize stale waiting shells."""

	def rank(run: Mapping[str, Any]) -> tuple[int, float, str]:
		status = str(run.get("status") or "")
		live = str(run.get("live_crawl_status") or "")
		jobs = int(run.get("live_jobs_seen") or 0)
		updated = float(run.get("updated_at") or 0)
		if (status == "completed" or run.get("effective_completed")) and jobs > 0:
			tier = 0
		elif status == "completed":
			tier = 1
		elif status == "waiting_input" and live == "completed" and jobs > 0:
			# Stale wait after a later successful resume — still show near top with results.
			tier = 2
		elif status in {"waiting_input", "running", "pending"}:
			tier = 3
		elif status == "failed":
			tier = 4
		else:
			tier = 5
		return (tier, -updated, str(run.get("run_id") or ""))

	return sorted((dict(run) for run in runs), key=rank)
