"""搜索结果索引缓存，支持 boss show N 快速导航。
缓存文件: ~/.boss-agent/cache/index_cache.json
"""
import errno
import json
import math
import stat
import time
from pathlib import Path
from typing import Any

from boss_agent_cli.output import Logger
from boss_agent_cli.services.json_files import path_mode, write_json_atomic

_CACHE_FILE = "index_cache.json"
# 合理时间戳上限（2100-01-01），与 api/cdp_risk_lock 的区间判定同口径：
# 负值或超远未来的时间戳只会来自漂移，不能当作真实保存时间参与新鲜度判断。
_MAX_SAVED_AT = 4_102_444_800.0
# 搜索结果页码的合理上限：平台分页远达不到，超出即视为漂移。
_MAX_PAGE = 1_000_000


def _cache_path(data_dir: Path) -> Path:
	return data_dir / "cache" / _CACHE_FILE


def save_index(
	data_dir: Path, jobs: list[dict[str, Any]], source: str = "search", page: int | None = None
) -> dict[str, Any]:
	"""保存搜索/推荐结果到索引缓存，返回本次写入的快照身份。

	调用方要用这份身份填 ``pagination.index_snapshot``：保存后再读文件，并发
	搜索可能让本次结果挂上另一次搜索的 ``source`` / ``page``（Issue #2292）。
	"""
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
	# 与读回时的归一化完全同口径：信封装的是「读者会看到的」身份。
	snapshot = _cache_metadata(cache_data)

	path = _cache_path(data_dir)
	write_json_atomic(path, cache_data)
	return snapshot


def try_save_index(
	data_dir: Path, jobs: list[dict[str, Any]], *, source: str = "search",
	page: int | None = None, logger: Logger | None = None,
) -> dict[str, Any] | None:
	"""Best-effort 保存索引缓存，失败时记录 warning，不影响主命令成功返回。

	返回本次写入的快照身份（写失败为 ``None``）：信封里的 ``index_snapshot``
	必须来自这次写入，而不是保存后重新读到的可变文件（Issue #2292）。
	"""
	try:
		return save_index(data_dir, jobs, source=source, page=page)
	except OSError as e:
		if logger:
			logger.warning(f"索引缓存写入失败，已跳过: {e}")
		return None


def _load_cache(data_dir: Path) -> dict[str, Any] | None:
	"""读取索引缓存；缺文件、解析失败或结构不对都返回 None。

	缓存是 `boss show <编号>` 的唯一数据源，结构损坏时按「没有缓存」处理，
	让用户重新 `boss search`，而不是把 AttributeError 兜成 INTERNAL_ERROR
	（那条指引会让人去升级版本 / 提 issue，而真正该做的是重跑搜索）。
	"""
	path = _cache_path(data_dir)
	# 缓存文件是 `boss show` 的唯一数据源：FIFO / 设备直接 read_text() 会永久
	# 阻塞（Issue #1732）；`exists()` 又会把 3.14 下权限等元数据错误吞成
	# 「没有缓存」，误导用户重跑搜索。与 stats 缓存库（#1716）同口径按 lstat
	# 判定：只有 FileNotFoundError 才算没有缓存，非普通文件上抛 OSError。
	mode = path_mode(path)
	if mode is None:
		return None
	if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
		raise OSError(errno.EINVAL, "索引缓存不是普通文件", str(path))
	# 文件本身读不出来（权限、磁盘错误）同样是本地状态未知，交给命令层按
	# LOCAL_IO_ERROR 上报，而不是谎报「先 search 建立缓存」。
	raw = path.read_bytes()
	try:
		loaded = json.loads(raw.decode("utf-8"))
	except (ValueError, RecursionError):
		# ValueError 覆盖 JSONDecodeError / UnicodeDecodeError，以及 Python 3.11+
		# 对超长整数字面量的位数上限；深嵌套会抛 RecursionError。损坏缓存一律
		# 按「没有缓存」处理，不能冒泡成可重试的 NETWORK_ERROR（Issue #1224）。
		return None
	if not isinstance(loaded, dict):
		return None
	jobs = loaded.get("jobs", [])
	if not isinstance(jobs, list) or not all(isinstance(job, dict) for job in jobs):
		return None
	return loaded


#: 会进入 URL 编码 / SQLite 绑定的职位标识：孤立代理字符会让这些操作抛
#: UnicodeEncodeError，被兜成带磁盘指引的 LOCAL_IO_ERROR（Issue #2293）。
_IDENTIFIER_KEYS = ("security_id", "job_id", "lid")


def _safe_job(job: dict[str, Any]) -> dict[str, Any]:
	"""读取职位时净化标识；坏值按缺失（空串）处理，由命令层按参数错误拒绝。"""
	safe = dict(job)
	for key in _IDENTIFIER_KEYS:
		raw = safe.get(key)
		if isinstance(raw, str):
			safe[key] = _safe_text(raw)
	return safe


def _job_at(cache_data: dict[str, Any], index: int) -> dict[str, Any] | None:
	jobs = cache_data.get("jobs", [])
	if not isinstance(jobs, list) or index < 1 or index > len(jobs):
		return None
	job = jobs[index - 1]
	return _safe_job(job) if isinstance(job, dict) else None


def get_job_by_index(data_dir: Path, index: int) -> dict[str, Any] | None:
	"""按 1-based 编号获取缓存的职位信息。"""
	cache_data = _load_cache(data_dir)
	if cache_data is None:
		return None
	return _job_at(cache_data, index)


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


def _saved_at(raw: Any) -> int | float:
	"""保存时间只接受有限的 Unix 秒：负值与超远未来按「未知」(0) 处理。"""
	parsed = _finite_number(raw)
	if parsed is None or not 0 <= float(parsed) <= _MAX_SAVED_AT:
		return 0
	return parsed


def _safe_text(raw: Any) -> str:
	"""字符串元信息还必须能编码成 UTF-8。

	JSON 的 ``"\\ud800"`` 转义会解析出孤立代理字符，嵌入错误信封后
	``click.echo`` 写 stdout 时抛 ``UnicodeEncodeError``，本地损坏被兜成
	可重试的 ``NETWORK_ERROR``（Issue #1224）。
	"""
	if not isinstance(raw, str):
		return ""
	try:
		raw.encode("utf-8")
	except UnicodeEncodeError:
		return ""
	return raw


def _cache_metadata(cache_data: dict[str, Any]) -> dict[str, Any]:
	"""把缓存元信息归一成信封声明的类型；`jobs` 可用时不因元信息漂移整体丢弃。"""
	jobs = cache_data.get("jobs")
	job_count = len(jobs) if isinstance(jobs, list) else 0
	page = cache_data.get("page")
	return {
		"source": _safe_text(cache_data.get("source")),
		# 旧版本写入的缓存没有 page 字段，未知页码本来就按 None 返回。
		"page": page if isinstance(page, int) and not isinstance(page, bool) and 1 <= page <= _MAX_PAGE else None,
		# count 只由已校验的 jobs 决定：save_index 保证两者一致，缓存里的 count
		# 漂移时越界提示会自相矛盾（「共 5 条」却拒绝编号 5，Issue #1224）。
		"count": job_count,
		"saved_at": _saved_at(cache_data.get("saved_at")),
	}


_MISSING_INFO: dict[str, Any] = {"exists": False, "source": "", "page": None, "count": 0, "saved_at": 0}


def _info_from(cache_data: dict[str, Any]) -> dict[str, Any]:
	return {"exists": True, **_cache_metadata(cache_data)}


def get_index_info(data_dir: Path) -> dict[str, Any]:
	"""获取缓存元信息（来源、页码、数量、保存时间）。"""
	cache_data = _load_cache(data_dir)
	if cache_data is None:
		return dict(_MISSING_INFO)
	return _info_from(cache_data)


def get_index_entry(data_dir: Path, index: int) -> tuple[dict[str, Any], dict[str, Any] | None]:
	"""一次读取同时返回快照元信息与指定编号的职位。

	``show`` 若分两次读取，并发的搜索覆盖缓存后会出现「职位来自第二份快照、
	``index_snapshot`` 来自第一份」的成功信封；``index_snapshot`` 正是 Agent
	判断编号失效的依据，错配等于让它把过期编号当成有效（Issue #2291）。
	"""
	cache_data = _load_cache(data_dir)
	if cache_data is None:
		return dict(_MISSING_INFO), None
	return _info_from(cache_data), _job_at(cache_data, index)


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
