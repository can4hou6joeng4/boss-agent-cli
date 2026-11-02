"""搜索结果索引缓存，支持 boss show N 快速导航。
缓存文件: ~/.boss-agent/cache/index_cache.json
"""
import json
import time
from pathlib import Path
from typing import Any

from boss_agent_cli.output import Logger
from boss_agent_cli.services.json_files import write_json_atomic

_CACHE_FILE = "index_cache.json"


def _cache_path(data_dir: Path) -> Path:
	return data_dir / "cache" / _CACHE_FILE


def save_index(data_dir: Path, jobs: list[dict[str, Any]], source: str = "search") -> None:
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
		"count": len(entries),
		"saved_at": time.time(),
		"jobs": entries,
	}

	path = _cache_path(data_dir)
	write_json_atomic(path, cache_data)


def try_save_index(data_dir: Path, jobs: list[dict[str, Any]], *, source: str = "search", logger: Logger | None = None) -> bool:
	"""Best-effort 保存索引缓存，失败时记录 warning，不影响主命令成功返回。"""
	try:
		save_index(data_dir, jobs, source=source)
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


def get_index_info(data_dir: Path) -> dict[str, Any]:
	"""获取缓存元信息（来源、数量、保存时间）。"""
	cache_data = _load_cache(data_dir)
	if cache_data is None:
		return {"exists": False, "source": "", "count": 0, "saved_at": 0}

	return {
		"exists": True,
		"source": cache_data.get("source", ""),
		"count": cache_data.get("count", 0),
		"saved_at": cache_data.get("saved_at", 0),
	}
