"""Local queries and shortlist imports for persisted crawl runs."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from boss_agent_cli.api.models import safe_text
from boss_agent_cli.cache.store import CacheStore
from boss_agent_cli.crawler.service import (
	CrawlSettings,
	_hook_injections,
	crawl_executor_active,
	crawl_run_awaiting_worker,
)
from boss_agent_cli.display import boss_command, shell_argument
from boss_agent_cli.services.text_files import is_utf8_encodable

_PUBLIC_JOB_FIELDS = {
	"query", "page", "rank", "title", "salary", "city", "district", "business_district",
	"company", "company_scale", "industry", "education", "experience", "labels", "benefits",
	"post_description", "address", "detail_status",
}


def _public_job(payload: dict[str, Any]) -> dict[str, Any]:
	return {key: value for key, value in payload.items() if key in _PUBLIC_JOB_FIELDS}


def crawl_status(cache: CacheStore, run_id: str, *, data_dir: Path | None = None) -> dict[str, Any]:
	"""Return one run's checkpoint and detail progress without opening Chrome."""
	run = cache.get_crawl_run(run_id)
	if run is None:
		raise KeyError(run_id)
	return crawl_status_from_run(cache, run, data_dir=data_dir)


def crawl_status_from_run(
	cache: CacheStore, run: dict[str, Any], *, data_dir: Path | None = None
) -> dict[str, Any]:
	"""根据已读取的任务行组装状态和详情进度。"""
	run_id = str(run["run_id"])
	# 与 resume 共用同一套断点校验：params 里 query/city 类型漂移时，status 不能
	# 把错误类型原样放进 ok=true 信封、而 resume 对同一行报损坏（Issue #1192）。
	settings = CrawlSettings.from_dict(run["params"], run_id=run_id)
	jobs = cache.list_crawl_jobs(run_id)
	details_completed = sum(1 for item in jobs if item["detail_done"])
	checkpoint: dict[str, Any] = {
		"next_page": run["next_page"],
		"resume_command": boss_command(f"crawl resume {shell_argument(run_id)}", data_dir=data_dir),
	}
	if run["status"] == "risk_stopped":
		# 风险锁会先于浏览器构造阻断 resume（raise_if_locked）。这里只标记前置条件，
		# 不把解锁命令写进机器可执行的 resume_command：风险锁必须由用户确认官方
		# 页面恢复后显式解除。
		checkpoint["resume_requires_operator_unlock"] = True
	payload = {
		"run_id": run_id,
		"status": run["status"],
		"query": settings.query,
		"city_code": settings.city_code,
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
			# 输出有效预算而不是原始 params：limits 写成字符串时 resume 按默认值
			# 执行，status 回显原始坏值会让调用方以为限制真的生效（Issue #1192）。
			"limits": {
				"max_requests": settings.max_requests,
				"max_details": settings.max_details,
				"max_seconds": settings.max_seconds,
				"max_retries": settings.max_retries,
			},
		},
		"output_dir": run["output_dir"],
		"error": run["error"],
		# 与 resume 同一口径解码：success 必须是真布尔，类型漂移按记录损坏上报
		# （Issue #1484 / #1874）。
		"hooks": [
			{"name": item.name, "success": item.success, "sha256": item.sha256, "reason": item.reason}
			for item in _hook_injections(run.get("hook_results", []), run_id=run_id)
		],
		"checkpoint": checkpoint,
	}
	if data_dir is not None:
		payload = _with_executor_state(payload, run, data_dir=data_dir)
	return payload


def _with_executor_state(payload: dict[str, Any], run: dict[str, Any], *, data_dir: Path) -> dict[str, Any]:
	"""给状态数据补上执行器证据与待处理的停止请求，用组装状态用的同一行判定。

	``crawl status`` 与向导 / MCP 的 ``crawl_status`` 步骤共用这份数据：记录仍是
	``running`` / ``queued`` 不代表还有进程在推进，执行锁空闲时 Agent 继续轮询
	只会永远等不到结果（Issue #2465）。``data_dir`` 为空时不便探锁（纯本地读取
	调用），保持原数据形状。
	"""
	run_id = str(run["run_id"])
	status = run["status"]
	if status not in ("queued", "running"):
		payload["executor_active"] = False
		return payload
	queue_in_handoff = crawl_run_awaiting_worker(run)
	executor_active = queue_in_handoff or crawl_executor_active(data_dir, run_id)
	payload["executor_active"] = executor_active
	# 已受理但还没被执行器消费的停止请求：终态不回显，避免把已生效的历史标记
	# 再报一次；未结算状态必须让调用方看见，否则陈旧的 running / queued 会被
	# 引导去 resume --background，而 resume 会先清除该标记（Issue #2482）。
	payload["stop_requested"] = run["stop_requested"]
	if status == "running" and not executor_active:
		payload["running_stale"] = True
	if status == "queued" and not queue_in_handoff:
		payload["queue_overdue"] = True
	return payload


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
	"""Import selected crawl rows into the existing shortlist without overwrites.

	Dedupe by the stable ``job_id``: ``security_id`` rotates on every search
	response, so a composite key lets a re-crawled job in as a second row.
	"""
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

	# security_id 每次搜索请求都会轮换，不能参与跨 run 去重：同一职位重新采集后
	# 再次导入会带着新值，复合键永远对不上，候选池里就多出一条重复记录。只按稳定
	# 的 job_id 判断是否已存在；命中时保留已有标签、备注和首次加入时间（Issue #899）。
	known_job_ids = {safe_text(item.get("job_id") or "") for item in cache.list_shortlist()}
	imported: list[dict[str, str]] = []
	existing_count = 0
	skipped_count = 0
	for selected_item in selected:
		item = selected_item["payload"]
		# 显式 null 不能经 ``str(None)`` 变成真值 "None"：那样空值检查失效，不同
		# 职位会以同一个伪主键互相去重，候选池里落出一条 security_id/job_id 都是
		# "None" 的记录（Issue #2408）。与展示字段同口径按缺失处理。
		security_id = safe_text(item.get("security_id") or "")
		job_id = safe_text(item.get("job_id") or "")
		if not security_id or not job_id or not is_utf8_encodable(security_id) or not is_utf8_encodable(job_id):
			skipped_count += 1
			continue
		if job_id in known_job_ids:
			existing_count += 1
			continue
		cache.add_shortlist({
			"security_id": security_id,
			"job_id": job_id,
			"title": safe_text(item.get("title") or ""),
			"company": safe_text(item.get("company") or ""),
			"city": safe_text(item.get("city") or ""),
			"salary": safe_text(item.get("salary") or ""),
			"source": f"crawl:{run_id}",
			"tags": list(tags),
			"note": note,
		})
		imported.append({"selector": str(selected_item["selector"]), "title": safe_text(item.get("title") or "")})
		known_job_ids.add(job_id)
	return {
		"run_id": run_id,
		"selected_count": len(selected),
		"imported_count": len(imported),
		"existing_count": existing_count,
		"skipped_count": skipped_count,
		"source": f"crawl:{run_id}",
		"imported": imported,
	}
