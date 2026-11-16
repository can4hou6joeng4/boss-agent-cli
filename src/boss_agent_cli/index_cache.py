"""搜索结果索引缓存，支持 boss show N 快速导航。
缓存文件: ~/.boss-agent/cache/index_cache.json
"""
import json
import math
import time
from pathlib import Path
from typing import Any

from boss_agent_cli.output import Logger
from boss_agent_cli.services.json_files import write_json_atomic

_CACHE_FILE = "index_cache.json"


def _cache_path(data_dir: Path) -> Path:
	return data_dir / "cache" / _CACHE_FILE


def save_index(data_dir: Path, jobs: list[dict[str, Any]], source: str = "search", page: int | None = None) -> None:
	"""保存搜索/推荐结果到索引缓存。"""
	entries = []
	for job in jobs:
		entries.append({
			"security_id": job.get("security_id", ""),
			"job_id": job.get("job_id", ""),
			"lid": job.get("lid", ""),
			"title": job.get("title", ""),
			"company": job.get("company", ""),
			"salary": job.get("salary", ""),
			"city": job.get("city", ""),
			"experience": job.get("experience", ""),
			"education": job.get("education", ""),
			"skills": job.get("skills", []),
			"raw_job_type": job.get("raw_job_type"),
			"employment_type": job.get("employment_type", ""),
			"days_per_week": job.get("days_per_week", ""),
			"least_month": job.get("least_month", ""),
		})

	cache_data = {
		"source": source,
		"page": page,
		"count": len(entries),
		"saved_at": time.time(),
		"jobs": entries,
	}

	path = _cache_path(data_dir)
	write_json_atomic(path, cache_data)


def try_save_index(
	data_dir: Path, jobs: list[dict[str, Any]], *, source: str = "search",
	page: int | None = None, logger: Logger | None = None,
) -> bool:
	"""Best-effort 保存索引缓存，失败时记录 warning，不影响主命令成功返回。"""
	try:
		save_index(data_dir, jobs, source=source, page=page)
		return True
	except OSError as e:
		if logger:
			logger.warning(f"索引缓存写入失败，已跳过: {e}")
		return False


def _load_cache(data_dir: Path) -> dict[str, Any] | None:
	"""读取索引缓存；缺文件、解析失败或结构不对都返回 None。

	缓存是 `boss show <编号>` 的唯一数据源，结构损坏时按「没有缓存」处理，
	让用户重新 `boss search`，而不是把 AttributeError 兜成 INTERNAL_ERROR
	（那条指引会让人去升级版本 / 提 issue，而真正该做的是重跑搜索）。
	"""
	path = _cache_path(data_dir)
	if not path.exists():
		return None
	try:
		loaded = json.loads(path.read_text(encoding="utf-8"))
	except (json.JSONDecodeError, UnicodeDecodeError, OSError):
		return None
	if not isinstance(loaded, dict):
		return None
	jobs = loaded.get("jobs", [])
	if not isinstance(jobs, list) or not all(isinstance(job, dict) for job in jobs):
		return None
	return loaded


def get_job_by_index(data_dir: Path, index: int) -> dict[str, Any] | None:
	"""按 1-based 编号获取缓存的职位信息。"""
	cache_data = _load_cache(data_dir)
	if cache_data is None:
		return None

	jobs = cache_data.get("jobs", [])
	if not isinstance(jobs, list) or index < 1 or index > len(jobs):
		return None

	job = jobs[index - 1]
	return job if isinstance(job, dict) else None


def _finite_number(raw: Any) -> int | float | None:
	"""缓存元信息的数值容错读取：bool、非数值与非有限值一律视为缺失。

	`math.isfinite(10**400)` 与 `float("inf")` 都会让下游的 JSON 序列化 / 时间
	格式化出问题，`saved_at` 这类字段只接受能安全进入信封的有限数值。
	"""
	if isinstance(raw, bool) or not isinstance(raw, (int, float)):
		return None
	try:
		return raw if math.isfinite(float(raw)) else None
	except OverflowError:
		return None


def _cache_metadata(cache_data: dict[str, Any]) -> dict[str, Any]:
	"""把缓存元信息归一成信封声明的类型；`jobs` 可用时不因元信息漂移整体丢弃。"""
	jobs = cache_data.get("jobs")
	job_count = len(jobs) if isinstance(jobs, list) else 0
	source = cache_data.get("source")
	page = cache_data.get("page")
	count = cache_data.get("count")
	saved_at = _finite_number(cache_data.get("saved_at"))
	return {
		"source": source if isinstance(source, str) else "",
		# 旧版本写入的缓存没有 page 字段，未知页码本来就按 None 返回。
		"page": page if isinstance(page, int) and not isinstance(page, bool) and page >= 1 else None,
		"count": count if isinstance(count, int) and not isinstance(count, bool) and count >= 0 else job_count,
		"saved_at": saved_at if saved_at is not None else 0,
	}


def get_index_info(data_dir: Path) -> dict[str, Any]:
	"""获取缓存元信息（来源、页码、数量、保存时间）。"""
	cache_data = _load_cache(data_dir)
	if cache_data is None:
		return {"exists": False, "source": "", "page": None, "count": 0, "saved_at": 0}

	return {"exists": True, **_cache_metadata(cache_data)}


def get_index_snapshot(data_dir: Path) -> dict[str, Any] | None:
	"""返回当前索引快照的身份信息；没有可用缓存时返回 None。

	`boss show <编号>` 的编号只在这份快照内有效，`search` / `recommend` 成功
	写入后把它附在分页信息里，调用方才能对照 `show` 返回的 `index_snapshot`
	判断手里的编号是否已被新的搜索覆盖。`page` 让多页搜索的编号也能区分页码，
	旧版本写入的缓存没有该字段，这里按 None 返回。
	"""
	info = get_index_info(data_dir)
	if not info["exists"]:
		return None
	return {
		"source": info["source"],
		"page": info["page"],
		"count": info["count"],
		"saved_at": info["saved_at"],
	}
