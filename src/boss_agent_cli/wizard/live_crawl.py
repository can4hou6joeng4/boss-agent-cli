"""Merge live crawl_runs / crawl_jobs into workflow run payloads for TUI."""

from __future__ import annotations

from collections.abc import Sequence
import math
import stat
from pathlib import Path
from typing import Any, Mapping

from boss_agent_cli.api.models import safe_text
from boss_agent_cli.cache.store import CacheStore
from boss_agent_cli.services.json_files import path_mode

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


def bindable_run_id(value: Any) -> str | None:
	"""把断点里的内部 run_id 归一成可绑定给 sqlite 的文本（Issue #2495）。

	JSON 列用 ``ensure_ascii=True`` 落库，``\\ud800`` 这类孤立代理字符能通过
	读取校验，再作为 sqlite 参数绑定却抛 ``UnicodeEncodeError``（不是
	``sqlite3.DatabaseError``，不在 ``CacheRecordInvalid`` 的捕获范围内），
	把状态 / 列表 / 停止控制路径整体拖垮。这样的值不可能对应 ``crawl_runs``
	里的真实记录（写入时同样过不了绑定），按「没有绑定内部任务」处理；
	``safe_text`` 同时兜住超大整数 / 深嵌套的 ``str()`` 异常。

	只接受字符串与非 bool 数值标量：容器 / 布尔的 ``str()`` 是 Python repr
	（``"{'x': 1}"`` / ``"True"``），可绑定但必然查不到 ``crawl_runs``；旧实现
	还会让 ``inner_crawl_run_id`` 立即返回、截断合法的后续候选（Issue #3081）。
	"""
	if value is None or isinstance(value, bool):
		return None
	if not isinstance(value, (str, int, float)):
		return None
	if isinstance(value, float) and not math.isfinite(value):
		return None
	text = safe_text(value)
	if not text:
		return None
	try:
		text.encode("utf-8")
	except UnicodeEncodeError:
		return None
	return text


def inner_crawl_run_id(run: Mapping[str, Any]) -> str | None:
	"""Extract the crawler run_id nested in a workflow last_result / params.

	某个候选值类型漂移（``bindable_run_id`` 返回 None）时继续尝试后续候选：
	旧实现直接 ``return``，``last_result.data.run_id`` 是容器时合法的
	``params.inputs.run_id`` 被截断，状态面板的 jobs / output_paths 随之消失
	（Issue #3081）。
	"""
	last = run.get("last_result")
	if isinstance(last, Mapping):
		data = last.get("data")
		if isinstance(data, Mapping) and data.get("run_id"):
			bound = bindable_run_id(data["run_id"])
			if bound:
				return bound
		if last.get("run_id"):
			bound = bindable_run_id(last["run_id"])
			if bound:
				return bound
	params = run.get("params")
	if isinstance(params, Mapping):
		inputs = params.get("inputs")
		if isinstance(inputs, Mapping):
			for key in ("run_id", "crawl_run_id"):
				if inputs.get(key):
					bound = bindable_run_id(inputs[key])
					if bound:
						return bound
	return None


def _output_paths(output_dir: str | None) -> dict[str, str]:
	if not output_dir:
		return {}
	base = Path(output_dir)
	paths: dict[str, str] = {}
	for kind in ("json", "csv", "xlsx"):
		path = base / f"jobs.{kind}"
		try:
			mode = path_mode(path)
		except (OSError, ValueError):
			# 展示型探测：目录存在但不可读（权限 / 挂载异常 / 非法路径）时
			# 按「无法确认产物」处理。`Path.exists()` 在 3.11 / 3.12 会把
			# PermissionError 抛给状态面板、3.14 又静默返回 False（Issue #2371）。
			continue
		# 目录 / FIFO / 设备 / 悬空链接都不是可读取的产物文件：只按 lstat
		# 存在性报告会让状态面板展示一个打不开的路径（Issue #3081）。
		# 符号链接指向普通文件仍视为产物，与 services/json_files.path_mode
		# 的读路径口径一致。
		if mode is None or not stat.S_ISREG(mode):
			continue
		paths[kind] = str(path)
	return paths


def _payload_text(payload: Mapping[str, Any], *keys: str) -> str:
	"""采集载荷的展示标量：容器 / 布尔 / 非有限数 / 超大整数按缺失处理（Issue #2831）。

	载荷来自本地 SQLite 的 jobs payload，平台字段漂移时可能是对象 / 数组；
	裸 ``str()`` 会把 Python repr 打给真人，超大整数还会在转换上限处抛
	``ValueError`` 中断整条 TTY 渲染。``keys`` 按优先级回退，空串视为未提供。
	"""
	for key in keys:
		value = payload.get(key)
		if isinstance(value, str):
			if value:
				return value
			continue
		if isinstance(value, bool) or value is None:
			continue
		if isinstance(value, (int, float)):
			if isinstance(value, float) and not math.isfinite(value):
				continue
			text = safe_text(value)
			if text:
				return text
	return ""


def _job_row(item: Mapping[str, Any], *, include_description: bool = True) -> dict[str, Any]:
	payload = item.get("payload") if isinstance(item.get("payload"), Mapping) else {}
	if not isinstance(payload, Mapping):
		payload = {}
	row: dict[str, Any] = {
		"title": _payload_text(payload, "title"),
		"company": _payload_text(payload, "company"),
		"city": _payload_text(payload, "city"),
		"salary": _payload_text(payload, "salary"),
		"experience": _payload_text(payload, "experience"),
		"education": _payload_text(payload, "education"),
		"security_id": _payload_text(payload, "security_id"),
		"job_id": _payload_text(payload, "job_id"),
		"boss_name": _payload_text(payload, "boss_name"),
		"boss_title": _payload_text(payload, "boss_title"),
		"labels": payload.get("labels") or payload.get("benefits") or [],
		"selector": item.get("selector"),
		"crawl_page": item.get("page_no"),
		"detail_done": bool(item.get("detail_done")),
		"source": "crawl",
	}
	if include_description:
		desc = _payload_text(payload, "post_description", "description")
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


def _as_timestamp(value: Any) -> float:
	"""展示排序用的时间戳；类型漂移按 0 参与排序，不能让任务列表 traceback。"""
	try:
		return float(value or 0)
	except (TypeError, ValueError):
		return 0.0


def sort_runs_for_status_picker(runs: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
	"""Prefer completed crawls with jobs; de-prioritize stale waiting shells."""

	def rank(run: Mapping[str, Any]) -> tuple[int, float, str]:
		status = str(run.get("status") or "")
		live = str(run.get("live_crawl_status") or "")
		jobs = int(run.get("live_jobs_seen") or 0)
		updated = _as_timestamp(run.get("updated_at"))
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
