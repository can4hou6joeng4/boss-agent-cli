"""Local queries and shortlist imports for persisted crawl runs."""

from __future__ import annotations

from typing import Any

from boss_agent_cli.cache.store import CacheStore

_PUBLIC_JOB_FIELDS = {
	"query", "page", "rank", "title", "salary", "city", "district", "business_district",
	"company", "company_scale", "industry", "education", "experience", "labels", "benefits",
	"post_description", "address", "detail_status",
}


def _public_job(payload: dict[str, Any]) -> dict[str, Any]:
	return {key: value for key, value in payload.items() if key in _PUBLIC_JOB_FIELDS}


def crawl_status(cache: CacheStore, run_id: str) -> dict[str, Any]:
	"""Return one run's checkpoint and detail progress without opening Chrome."""
	run = cache.get_crawl_run(run_id)
	if run is None:
		raise KeyError(run_id)
	jobs = cache.list_crawl_jobs(run_id)
	details_completed = sum(1 for item in jobs if item["detail_done"])
	return {
		"run_id": run_id,
		"status": run["status"],
		"query": run["params"].get("query", ""),
		"city_code": run["params"].get("city_code", ""),
		"next_page": run["next_page"],
		"pages_completed": max(0, int(run["next_page"]) - 1),
		"list_finished": run["list_finished"],
		"jobs_seen": len(jobs),
		"details_completed": details_completed,
		"details_pending": len(jobs) - details_completed,
		"budget": {
			"requests_attempted": run["requests_attempted"],
			"detail_requests_attempted": run["detail_requests_attempted"],
			"elapsed_seconds": run["elapsed_seconds"],
			"limits": run["params"].get("limits", {}),
		},
		"output_dir": run["output_dir"],
		"error": run["error"],
		"hooks": run["hook_results"],
		"checkpoint": {
			"next_page": run["next_page"],
			"resume_command": f"boss crawl resume {run_id}",
		},
	}


def crawl_results(
	cache: CacheStore,
	run_id: str,
	*,
	page: int | None = None,
	detail_status: str | None = None,
) -> dict[str, Any]:
	"""Return locally stored jobs, with optional page/detail filtering."""
	if cache.get_crawl_run(run_id) is None:
		raise KeyError(run_id)
	items = cache.list_crawl_jobs(run_id)
	if page is not None:
		items = [item for item in items if item["page_no"] == page]
	if detail_status == "completed":
		items = [item for item in items if item["detail_done"]]
	elif detail_status == "pending":
		items = [item for item in items if not item["detail_done"]]
	return {
		"run_id": run_id,
		"page": page,
		"detail_status": detail_status,
		"count": len(items),
		"jobs": [
			{
				**_public_job(item["payload"]),
				"selector": item["selector"],
				"crawl_page": item["page_no"],
				"detail_done": item["detail_done"],
			}
			for item in items
		],
	}


def import_crawl_shortlist(
	cache: CacheStore,
	run_id: str,
	*,
	selectors: tuple[str, ...] = (),
	include_all: bool = False,
	tags: tuple[str, ...] = (),
	note: str = "",
) -> dict[str, Any]:
	"""Import selected crawl rows into the existing shortlist without overwrites."""
	if include_all == bool(selectors):
		raise ValueError("必须且只能使用 --all 或至少一个 --selector 选择要导入的职位")
	if len(selectors) != len(set(selectors)):
		raise ValueError("selector 不能重复")
	if cache.get_crawl_run(run_id) is None:
		raise KeyError(run_id)
	crawl_items = cache.list_crawl_jobs(run_id)
	requested_selectors = set(selectors)
	selected = [
		item
		for item in crawl_items
		if include_all or str(item["selector"]) in requested_selectors
	]
	found_selectors = {str(item["selector"]) for item in selected}
	missing_selectors = sorted(requested_selectors - found_selectors)
	if missing_selectors:
		raise ValueError(f"run {run_id} 中不存在 selector: {', '.join(missing_selectors)}")

	existing_keys = {
		(str(item.get("security_id", "")), str(item.get("job_id", "")))
		for item in cache.list_shortlist()
	}
	imported: list[dict[str, str]] = []
	existing_count = 0
	skipped_count = 0
	for selected_item in selected:
		item = selected_item["payload"]
		security_id = str(item.get("security_id", ""))
		job_id = str(item.get("job_id", ""))
		if not security_id or not job_id:
			skipped_count += 1
			continue
		if (security_id, job_id) in existing_keys:
			existing_count += 1
			continue
		cache.add_shortlist({
			"security_id": security_id,
			"job_id": job_id,
			"title": str(item.get("title", "")),
			"company": str(item.get("company", "")),
			"city": str(item.get("city", "")),
			"salary": str(item.get("salary", "")),
			"source": f"crawl:{run_id}",
			"tags": list(tags),
			"note": note,
		})
		imported.append({"selector": str(selected_item["selector"]), "title": str(item.get("title", ""))})
	return {
		"run_id": run_id,
		"selected_count": len(selected),
		"imported_count": len(imported),
		"existing_count": existing_count,
		"skipped_count": skipped_count,
		"source": f"crawl:{run_id}",
		"imported": imported,
	}
