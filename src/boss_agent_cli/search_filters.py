"""Reusable search pipeline — list-page prefiltering + welfare / HR-active detail fallback.

Centralizes filtering logic shared by search, batch-greet, and export commands.
"""

import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

from boss_agent_cli.api import endpoints
from boss_agent_cli.api.client import PlatformRiskError
from boss_agent_cli.api.models import BOSS_INTERNSHIP_RESPONSE_JOB_TYPE, JobItem, is_boss_online

# ── Ordinal lookups for threshold comparisons ───────────────────────

_EXPERIENCE_ORDER: dict[str, int] = {
	"应届": 0,
	"1年以内": 1,
	"1-3年": 2,
	"3-5年": 3,
	"5-10年": 4,
	"10年以上": 5,
}

_EDUCATION_ORDER: dict[str, int] = {
	"初中及以下": 0,
	"中专/中技": 1,
	"高中": 2,
	"大专": 3,
	"本科": 4,
	"硕士": 5,
	"博士": 6,
}

# ── Welfare keywords ────────────────────────────────────────────────

WELFARE_KEYWORDS: dict[str, list[str]] = {
	"双休": ["双休", "周末双休", "五天工作制", "5天工作制"],
	"五险一金": ["五险一金"],
	"五险": ["五险一金", "五险"],
	"年终奖": ["年终奖"],
	"带薪年假": ["带薪年假"],
	"餐补": ["餐补", "包吃", "免费午餐"],
	"住房补贴": ["住房补贴", "住房补助"],
	"定期体检": ["定期体检"],
	"股票期权": ["股票期权"],
	"加班补助": ["加班补助"],
}

# ── HR active level ─────────────────────────────────────────────────

# 平台 activeTimeDesc 文案 → 活跃度等级（越大越活跃）。列表页有时直接给这个字段，
# 没有时从 job_card 的 activeTimeDesc 取。
_ACTIVE_DESC_RANK: dict[str, int] = {
	"在线": 7,
	"刚刚活跃": 7,
	"今日活跃": 6,
	"3日内活跃": 5,
	"本周活跃": 4,
	"2周内活跃": 3,
	"本月活跃": 2,
	"近半年活跃": 1,
	"半年前活跃": 0,
}

# --active 取值 → 最低等级。
ACTIVE_LEVELS: dict[str, int] = {
	"online": 7,
	"today": 6,
	"3d": 5,
	"week": 4,
	"2w": 3,
	"month": 2,
	"half-year": 1,
}

_ACTIVE_LEVEL_ALIASES: dict[str, str] = {
	"在线": "online",
	"刚刚活跃": "online",
	"now": "online",
	"1d": "today",
	"1day": "today",
	"今日": "today",
	"今天": "today",
	"今日活跃": "today",
	"3day": "3d",
	"3days": "3d",
	"3日": "3d",
	"3天": "3d",
	"三天": "3d",
	"3日内": "3d",
	"3日内活跃": "3d",
	"1w": "week",
	"7d": "week",
	"本周": "week",
	"一周": "week",
	"本周活跃": "week",
	"2week": "2w",
	"2weeks": "2w",
	"14d": "2w",
	"2周": "2w",
	"两周": "2w",
	"2周内": "2w",
	"2周内活跃": "2w",
	"1m": "month",
	"30d": "month",
	"本月": "month",
	"一个月": "month",
	"本月活跃": "month",
	"halfyear": "half-year",
	"half_year": "half-year",
	"6m": "half-year",
	"半年": "half-year",
	"近半年": "half-year",
	"近半年活跃": "half-year",
}


def resolve_active_level(value: str | None) -> str | None:
	"""把 --active 的取值（含别名）规范成 ACTIVE_LEVELS 的键；空值返回 None。"""
	if value is None:
		return None
	text = re.sub(r"\s+", "", str(value)).lower()
	if not text:
		return None
	if text in ACTIVE_LEVELS:
		return text
	level = _ACTIVE_LEVEL_ALIASES.get(text)
	if level is None:
		choices = ", ".join(ACTIVE_LEVELS)
		raise ValueError(f"未知活跃度: {value}，可选 {choices}（也认 3day、3日 等写法）")
	return level


def active_desc_rank(desc: Any) -> int | None:
	"""activeTimeDesc 文案对应的等级；不认识的文案返回 None。"""
	if not isinstance(desc, str):
		return None
	return _ACTIVE_DESC_RANK.get(re.sub(r"\s+", "", desc))


def list_active_decision(raw_item: dict[str, Any], level: str) -> tuple[str, str]:
	"""只看列表字段判断活跃度：返回 (pass|reject|unknown|detail, 文案)。

	- bossOnline 为在线（True / 1 / "1"）：满足任何等级，不查详情
	- 列表带 activeTimeDesc 且认识：直接按等级判断
	- 列表带 activeTimeDesc 但不认识：unknown（详情里也是同一个字段，不再多查）
	- level=online 且列表既不在线也没有文案：直接排除——在线状态只看列表，永不查详情
	- 其余情况需要查详情
	"""
	if is_boss_online(raw_item.get("bossOnline")):
		return ("pass", "在线")
	desc = raw_item.get("activeTimeDesc")
	if isinstance(desc, str) and desc.strip():
		rank = active_desc_rank(desc)
		if rank is None:
			return ("unknown", desc.strip())
		return ("pass" if rank >= ACTIVE_LEVELS[level] else "reject", desc.strip())
	if level == "online":
		return ("reject", "离线")
	return ("detail", "")


_MAX_FILTER_PAGES = 5
_WELFARE_WORKERS = 3
# --active（不带 --welfare）的翻页硬上限：online 只看列表，和普通搜索一样只翻 1 页；
# 其余档位先看 1 页，结果不够且仍有职位要查详情时才继续，最多 3 页。
_ACTIVE_MAX_PAGES = 3
# 没有显式 limit 时，--active 的目标条数（凑够就不再翻页）。
_ACTIVE_DEFAULT_TARGET = 10


def detail_filter_max_pages(welfare: Any, active: str | None) -> int:
	"""search / watch / wizard 共用的默认翻页上限。

	--welfare 维持原来的 5 页；只有 --active 时 online 为 1 页，其余档位 3 页。
	"""
	if welfare:
		return _MAX_FILTER_PAGES
	if active == "online":
		return 1
	if active:
		return _ACTIVE_MAX_PAGES
	return 1


def detail_channel_for(obj: Any) -> str:
	"""按 click ctx.obj 选详情通道：配了 CDP 或指定了浏览器来源时只走浏览器 job_card。"""
	if not isinstance(obj, dict):
		return "auto"
	if obj.get("cdp_url") or (obj.get("browser_source") or "auto") != "auto":
		return "browser"
	return "auto"


def client_is_browser_only(client: Any) -> bool:
	"""平台 / client 是否处于只走浏览器通道的 CDP 模式（测试替身一律视为否）。"""
	check = getattr(client, "is_browser_only", None)
	if not callable(check):
		return False
	try:
		return check() is True
	except Exception:
		return False


_BOSS_SEARCH_HOSTS = {"www.zhipin.com", "zhipin.com"}
_BOSS_SEARCH_PATHS = {"/web/geek/job", "/web/geek/jobs"}
_URL_PARAM_ALIASES = {
	"query": "query",
	"city": "city",
	"salary": "salary",
	"experience": "experience",
	"degree": "degree",
	"education": "degree",
	"industry": "industry",
	"scale": "scale",
	"stage": "stage",
	"jobType": "jobType",
	"job_type": "jobType",
}
_URL_SEARCH_PARAM_KEYS = {
	"city",
	"salary",
	"experience",
	"degree",
	"industry",
	"scale",
	"stage",
	"jobType",
}


class SearchUrlParseError(ValueError):
	"""Raised when a user-supplied BOSS search URL cannot be safely used."""


@dataclass(frozen=True)
class ParsedSearchUrl:
	query: str
	params: dict[str, str]
	page: int | None = None


def _first_query_value(parsed: dict[str, list[str]], key: str) -> str:
	values = parsed.get(key, [])
	for value in values:
		candidate = value.strip()
		if candidate:
			return candidate
	return ""


def parse_boss_search_url(search_url: str) -> ParsedSearchUrl:
	"""Parse a user-copied BOSS search URL into whitelisted API search params."""
	parts = urlparse(search_url)
	if parts.scheme not in {"http", "https"} or parts.netloc not in _BOSS_SEARCH_HOSTS:
		raise SearchUrlParseError("仅支持 zhipin.com 的职位搜索 URL")
	if parts.path.rstrip("/") not in _BOSS_SEARCH_PATHS:
		raise SearchUrlParseError("仅支持 BOSS 直聘求职者职位搜索页 URL")

	query_values = parse_qs(parts.query, keep_blank_values=False)
	params: dict[str, str] = {}
	for source_key, target_key in _URL_PARAM_ALIASES.items():
		value = _first_query_value(query_values, source_key)
		if value:
			params[target_key] = value

	query = params.pop("query", "")
	page = None
	if raw_page := _first_query_value(query_values, "page"):
		try:
			page = max(1, int(raw_page))
		except ValueError as exc:
			raise SearchUrlParseError("URL 中的 page 参数不是有效数字") from exc

	if not query and not any(key in params for key in _URL_SEARCH_PARAM_KEYS):
		raise SearchUrlParseError("URL 中没有可用的搜索参数")
	return ParsedSearchUrl(query=query, params=params, page=page)


def _split_multi_value(value: str) -> list[str]:
	return [part.strip() for part in value.split(",") if part.strip()]


def resolve_lookup_codes(value: str | None, lookup: dict[str, str], label: str) -> str | None:
	"""Resolve comma-separated display labels or raw numeric codes into API codes."""
	if not value:
		return None
	codes: list[str] = []
	for part in _split_multi_value(value):
		if part.isdigit():
			codes.append(part)
			continue
		code = lookup.get(part)
		if code is None:
			raise ValueError(f"未知{label}: {part}")
		codes.append(code)
	return ",".join(codes) if codes else None


def build_search_params(
	query: str,
	city: str | None,
	salary: str | None,
	experience: str | None,
	education: str | None,
	industry: str | None,
	scale: str | None,
	stage: str | None,
	job_type: str | None,
	welfare: str | None,
) -> dict[str, str | None]:
	"""构造 search/watch/preset 共用的 10 键搜索参数字典。"""
	return {
		"query": query,
		"city": city,
		"salary": salary,
		"experience": experience,
		"education": education,
		"industry": industry,
		"scale": scale,
		"stage": stage,
		"job_type": job_type,
		"welfare": welfare,
	}


def resolve_search_code_params(
	*,
	salary: str | None = None,
	experience: str | None = None,
	education: str | None = None,
	industry: str | None = None,
	scale: str | None = None,
	stage: str | None = None,
	job_type: str | None = None,
) -> dict[str, str]:
	"""Resolve user-facing search filters into BOSS API parameter codes."""
	params: dict[str, str] = {}
	if code := resolve_lookup_codes(salary, endpoints.SALARY_CODES, "薪资范围"):
		params["salary"] = code
	if code := resolve_lookup_codes(experience, endpoints.EXPERIENCE_CODES, "经验要求"):
		params["experience"] = code
	if code := resolve_lookup_codes(education, endpoints.EDUCATION_CODES, "学历要求"):
		params["degree"] = code
	if code := resolve_lookup_codes(industry, endpoints.INDUSTRY_CODES, "行业类型"):
		params["industry"] = code
	if code := resolve_lookup_codes(scale, endpoints.SCALE_CODES, "公司规模"):
		params["scale"] = code
	if code := resolve_lookup_codes(stage, endpoints.STAGE_CODES, "融资阶段"):
		params["stage"] = code
	if code := resolve_lookup_codes(job_type, endpoints.JOB_TYPE_CODES, "职位类型"):
		params["jobType"] = code
	return params


# ── Salary parsing ──────────────────────────────────────────────────

_SALARY_RE = re.compile(r"(\d+)(?:\s*[-~]\s*(\d+))?\s*K", re.IGNORECASE)
_SALARY_BELOW_RE = re.compile(r"(\d+)\s*K以下", re.IGNORECASE)


def parse_salary_range(value: str) -> tuple[int, int] | None:
	"""Parse salary string like '20-50K' into (low, high) in K. Returns None if unparseable."""
	if not value or value == "面议":
		return None
	m = _SALARY_BELOW_RE.search(value)
	if m:
		return (0, int(m.group(1)))
	m = _SALARY_RE.search(value)
	if m:
		low = int(m.group(1))
		high = int(m.group(2)) if m.group(2) else low
		return (low, high)
	return None


# ── Threshold comparisons ───────────────────────────────────────────


def meets_experience_threshold(candidate: str, required: str | None) -> bool:
	"""Check if candidate experience meets or exceeds required threshold."""
	if required is None:
		return True
	c = _EXPERIENCE_ORDER.get(candidate)
	r = _EXPERIENCE_ORDER.get(required)
	if c is None:
		return True  # unknown experience passes
	if r is None:
		return True
	return c >= r


def meets_education_threshold(candidate: str, required: str | None) -> bool:
	"""Check if candidate education meets or exceeds required threshold."""
	if required is None:
		return True
	c = _EDUCATION_ORDER.get(candidate)
	r = _EDUCATION_ORDER.get(required)
	if c is None:
		return True  # unknown education passes
	if r is None:
		return True
	return c >= r


# ── Data structures ─────────────────────────────────────────────────


@dataclass(frozen=True)
class SearchFilterCriteria:
	query: str
	city: str | None = None
	salary: str | None = None
	experience: str | None = None
	education: str | None = None
	industry: str | None = None
	scale: str | None = None
	stage: str | None = None
	job_type: str | None = None
	raw_params: dict[str, str] = field(default_factory=dict)


@dataclass
class SearchPipelineStats:
	pages_scanned: int = 0
	jobs_seen: int = 0
	jobs_prefiltered: int = 0
	detail_checks: int = 0
	# 实际发出的 job_card 请求数（福利描述缓存命中不算）。
	detail_requests: int = 0
	jobs_matched: int = 0
	active_rejected: int = 0
	active_unknown: int = 0


@dataclass
class SearchPipelineResult:
	items: list[dict[str, Any]] = field(default_factory=list)
	has_more: bool = False
	total: int | None = None
	last_page: int = 0
	stats: SearchPipelineStats = field(default_factory=SearchPipelineStats)
	# 活跃度文案不认识或拿不到而被排除的职位（文案 → 次数），供命令层写进 hints。
	active_unknown_descs: dict[str, int] = field(default_factory=dict)


class SearchPipelinePlatformError(Exception):
	def __init__(self, code: str, message: str, details: dict[str, Any] | None = None):
		self.code = code
		self.message = message
		self.details = details
		super().__init__(message)


# ── List-page prefilter ─────────────────────────────────────────────


def prefilter_job(
	raw_item: dict[str, Any],
	criteria: SearchFilterCriteria,
	*,
	platform_name: str | None = None,
) -> tuple[bool, list[str]]:
	"""Fast prefilter using list-page fields only. Returns (pass, rejection_reasons)."""
	reasons: list[str] = []

	# City filter
	if criteria.city:
		item_city = raw_item.get("cityName", "")
		if item_city and criteria.city not in item_city:
			reasons.append(f"城市不匹配: {item_city} != {criteria.city}")

	# Salary filter — reject only if candidate max is below required min
	if criteria.salary:
		req_range = parse_salary_range(criteria.salary)
		item_range = parse_salary_range(raw_item.get("salaryDesc", ""))
		if req_range and item_range:
			if item_range[1] < req_range[0]:
				reasons.append(f"薪资不足: {raw_item.get('salaryDesc', '')} < {criteria.salary}")

	# Experience filter
	if criteria.experience:
		item_exp = raw_item.get("jobExperience", "")
		if not meets_experience_threshold(item_exp, criteria.experience):
			reasons.append(f"经验不足: {item_exp} < {criteria.experience}")

	# Education filter
	if criteria.education:
		item_edu = raw_item.get("jobDegree", "")
		if not meets_education_threshold(item_edu, criteria.education):
			reasons.append(f"学历不足: {item_edu} < {criteria.education}")

	_, job_type_reasons = prefilter_platform_job_type(raw_item, criteria, platform_name=platform_name)
	reasons.extend(job_type_reasons)

	return (len(reasons) == 0, reasons)


def prefilter_platform_job_type(
	raw_item: dict[str, Any],
	criteria: SearchFilterCriteria,
	*,
	platform_name: str | None = None,
) -> tuple[bool, list[str]]:
	"""Fail closed when a platform exposes a verified employment-type enum."""
	reasons: list[str] = []
	# BOSS request jobType=1902 returns a mixed campus/intern pool. For a
	# strict internship-only search, trust the response enum rather than the
	# title or request filter alone.
	if platform_name == "zhipin" and _requests_internship_only(criteria):
		raw_job_type = raw_item.get("jobType")
		if str(raw_job_type) != str(BOSS_INTERNSHIP_RESPONSE_JOB_TYPE):
			reasons.append(f"岗位类型不匹配: jobType={raw_job_type!r} != 实习(4)")

	return (len(reasons) == 0, reasons)


def _requests_internship_only(criteria: SearchFilterCriteria) -> bool:
	requested = set(_split_multi_value(criteria.job_type or ""))
	if requested in ({"实习"}, {"1902"}):
		return True
	raw_job_type = criteria.raw_params.get("jobType", "")
	return not requested and set(_split_multi_value(raw_job_type)) == {"1902"}


# ── Welfare matching ────────────────────────────────────────────────


def resolve_welfare_keywords(label: str) -> list[str]:
	"""Resolve a welfare label to matching keywords."""
	return WELFARE_KEYWORDS.get(label, [label])


def _check_welfare_in_text(keywords: list[str], text: str) -> bool:
	return any(kw in text for kw in keywords)


def match_all_welfare(
	conditions: list[tuple[str, list[str]]],
	welfare_list: list[str],
	description: str,
) -> list[str]:
	"""Check all welfare conditions (AND). Returns match descriptions or empty list."""
	text = " ".join(welfare_list)
	full_text = text + " " + description
	results = []
	for label, keywords in conditions:
		if _check_welfare_in_text(keywords, text):
			results.append(f"{label}(标签)")
		elif description and _check_welfare_in_text(keywords, full_text):
			results.append(f"{label}(描述)")
		else:
			return []
	return results


def compute_match_score(item: dict[str, Any], welfare_results: list[str], criteria: SearchFilterCriteria) -> int:
	"""Compute a local 0-100 match score from already-fetched item fields."""
	score = 0

	for result in welfare_results:
		if result.endswith("(标签)"):
			score += 12
		elif result.endswith("(描述)"):
			score += 8

	if criteria.city and criteria.city in str(item.get("city", "")):
		score += 12

	if criteria.salary:
		item_range = parse_salary_range(str(item.get("salary", "")))
		criteria_range = parse_salary_range(criteria.salary)
		if item_range and criteria_range and item_range[1] >= criteria_range[0]:
			score += 12

	if criteria.experience and meets_experience_threshold(str(item.get("experience", "")), criteria.experience):
		score += 10

	if criteria.education and meets_education_threshold(str(item.get("education", "")), criteria.education):
		score += 10

	if criteria.query:
		query = criteria.query.lower()
		skills = item.get("skills", [])
		if not isinstance(skills, list):
			skills = []
		searchable = f"{item.get('title', '')} {' '.join(str(skill) for skill in skills)}".lower()
		if query and query in searchable:
			score += 10

	if item.get("welfare"):
		score += 4

	return min(score, 100)


def _unwrap_platform_data(client: Any, response: dict[str, Any]) -> dict[str, Any]:
	"""Read a platform envelope while tolerating legacy test doubles."""
	unwrap = getattr(client, "unwrap_data", None)
	if callable(unwrap):
		data = unwrap(response)
		if isinstance(data, dict):
			return data
	for key in ("zpData", "data"):
		data = response.get(key)
		if isinstance(data, dict):
			return data
	return {}


@dataclass
class _DetailOutcome:
	item: dict[str, Any] | None = None
	fresh_desc: str | None = None
	active_state: str = ""
	active_desc: str = ""
	requested: bool = False


def _job_card_fetcher(client: Any, detail_channel: str) -> Callable[[str, str], dict[str, Any]]:
	"""detail_channel=browser 时只走浏览器通道（CDP 模式），平台没有该方法再退回 job_card。"""
	fetch: Callable[[str, str], dict[str, Any]] = client.job_card
	if detail_channel == "browser":
		browser_fetch = getattr(client, "job_card_browser", None)
		if callable(browser_fetch):
			fetch = browser_fetch
	return fetch


def _fetch_and_check(
	client: Any,
	welfare_conditions: list[tuple[str, list[str]]],
	criteria: SearchFilterCriteria,
	raw_item: dict[str, Any],
	cached_desc: str | None = None,
	*,
	active_level: str | None = None,
	need_active: bool = False,
	list_welfare: list[str] | None = None,
	list_active_desc: str = "",
	before_detail_request: Callable[[], None] | None = None,
	detail_channel: str = "auto",
) -> _DetailOutcome:
	"""Single job: (复用缓存或取详情) + 福利 / 活跃度判断。不访问 cache（线程安全）。

	cached_desc 命中且不需要查活跃度时跳过 job_card 请求；活跃度会变，
	need_active=True 时总是取一次新详情（描述缓存只省福利那部分）。
	list_welfare 是列表标签已经命中的福利结果，此时只需要补活跃度。
	"""
	welfare_list = raw_item.get("welfareList", [])
	outcome = _DetailOutcome()
	desc = ""
	card_active = ""
	need_desc = bool(welfare_conditions) and not list_welfare
	if need_active or (need_desc and not isinstance(cached_desc, str)):
		if before_detail_request is not None:
			before_detail_request()
		outcome.requested = True
		try:
			card_raw = _job_card_fetcher(client, detail_channel)(
				raw_item.get("securityId", ""),
				raw_item.get("lid", ""),
			)
			if not client.is_success(card_raw):
				code, message = client.parse_error(card_raw)
				raise SearchPipelinePlatformError(code, message or "职位详情获取失败")
			card_data = _unwrap_platform_data(client, card_raw)
			card = card_data.get("jobCard", {}) or {}
			desc = card.get("postDescription", "") or ""
			raw_active = card.get("activeTimeDesc")
			card_active = raw_active.strip() if isinstance(raw_active, str) else ""
			outcome.fresh_desc = desc  # 仅新取到的描述需写回缓存（主线程处理）
		except NotImplementedError:
			raise SearchPipelinePlatformError(
				"NOT_SUPPORTED",
				"当前平台暂不支持活跃度详情筛选，请去掉 --active 后重试"
				if need_active else "当前平台暂不支持福利详情筛选，请去掉 --welfare 后重试",
			)
		except (OSError, KeyError, TypeError, AttributeError):
			desc = ""
		if isinstance(cached_desc, str) and not desc:
			desc = cached_desc
	elif isinstance(cached_desc, str):
		desc = cached_desc

	active_desc = list_active_desc
	if need_active and active_level is not None:
		active_desc = card_active
		rank = active_desc_rank(card_active)
		if rank is None:
			outcome.active_state = "unknown"
			outcome.active_desc = card_active
			return outcome
		outcome.active_desc = card_active
		if rank < ACTIVE_LEVELS[active_level]:
			outcome.active_state = "reject"
			return outcome
		outcome.active_state = "pass"

	if welfare_conditions:
		match_results = list_welfare or match_all_welfare(welfare_conditions, welfare_list, desc)
		if not match_results:
			return outcome
	else:
		match_results = []

	item = JobItem.from_api(raw_item)
	d = item.to_dict()
	if active_desc:
		d["boss_active_desc"] = active_desc
	if match_results:
		d["welfare_match"] = "✅ " + ", ".join(match_results)
	d["match_score"] = compute_match_score(d, match_results, criteria)
	outcome.item = d
	return outcome


def _fetch_and_check_guarded(
	stop: threading.Event,
	client: Any,
	welfare_conditions: list[tuple[str, list[str]]],
	criteria: SearchFilterCriteria,
	raw_item: dict[str, Any],
	cached_desc: str | None = None,
	**kwargs: Any,
) -> _DetailOutcome:
	"""线程池 worker 入口：平台错误 / 风控一旦出现，同池后续任务不再触网。

	停止标志由 **worker 自己** 在抛出前置位，而不是等主线程收到异常再取消队列——
	主线程的 ``cancel_futures`` 只能取消尚未出队的任务，worker 在主线程反应过来之前
	往往已经取出了下一项（Python 3.14 下几乎必然）。标志置位后被取出的任务直接返回
	空结果，保证「命中风控后不再继续下一项」不依赖线程调度时机（Issue #419）。
	"""
	if stop.is_set():
		return _DetailOutcome()
	try:
		return _fetch_and_check(client, welfare_conditions, criteria, raw_item, cached_desc, **kwargs)
	except (SearchPipelinePlatformError, PlatformRiskError):
		stop.set()
		raise


@dataclass
class _DetailTask:
	raw_item: dict[str, Any]
	need_active: bool = False
	list_welfare: list[str] | None = None
	list_active_desc: str = ""


def _record_detail_outcome(
	outcome: _DetailOutcome,
	raw_item: dict[str, Any],
	cache: Any,
	logger: Any,
	matched: list[dict[str, Any]],
	stats: SearchPipelineStats | None,
	unknown_descs: dict[str, int] | None,
) -> None:
	"""主线程处理单个详情结果：写回描述缓存、统计活跃度、收集匹配项。"""
	company = raw_item.get("brandName", "")
	title = raw_item.get("jobName", "")
	if outcome.requested and stats is not None:
		stats.detail_requests += 1
	# 写回缓存（主线程，sqlite 安全）：仅新取到的描述，键用稳定的 encryptJobId
	if outcome.fresh_desc:
		cache.put_job_desc(raw_item.get("encryptJobId", ""), outcome.fresh_desc)
	result = outcome.item
	if outcome.active_state == "reject":
		if stats is not None:
			stats.active_rejected += 1
		logger.info(f"  ❌ {company} - {title}（HR 活跃度不足: {outcome.active_desc}）")
	elif outcome.active_state == "unknown":
		if stats is not None:
			stats.active_unknown += 1
		if unknown_descs is not None:
			key = outcome.active_desc or "(空)"
			unknown_descs[key] = unknown_descs.get(key, 0) + 1
		logger.info(f"  ❌ {company} - {title}（HR 活跃度未知: {outcome.active_desc or '详情未返回'}）")
	elif result:
		# is_greeted 在主线程中安全访问 cache
		sid = result.get("security_id", "")
		if sid:
			result["greeted"] = cache.is_greeted(sid)
		matched.append(result)
		logger.info(f"  ✅ {company} - {title}（详情匹配）")
	else:
		logger.info(f"  ❌ {company} - {title}")


def _check_details_parallel(
	client: Any,
	cache: Any,
	logger: Any,
	welfare_conditions: list[tuple[str, list[str]]],
	criteria: SearchFilterCriteria,
	tasks: list[_DetailTask],
	matched: list[dict[str, Any]],
	*,
	active_level: str | None = None,
	stats: SearchPipelineStats | None = None,
	unknown_descs: dict[str, int] | None = None,
	sequential: bool = False,
	before_detail_request: Callable[[], None] | None = None,
	detail_channel: str = "auto",
) -> None:
	"""Detail check, append matched to list. cache 操作在主线程完成。

	主线程先查职位描述缓存命中者跳过取详情；未命中者入线程池取详，
	取回的新描述由主线程写回缓存——所有 cache I/O 留在主线程（sqlite 非线程安全）。
	需要查活跃度的任务总是重新取详情，描述缓存只对福利判断有用。

	sequential=True（--active 需要查详情时）：不开线程池，主线程逐个取详情，
	每次真正发请求前调用 before_detail_request 做间隔（CrawlBudget 也在主线程用 sqlite）。
	"""
	# 主线程预取缓存：命中的描述随提交一并传入 worker，避免 worker 触网。
	# 键用 encryptJobId（跨搜索稳定）；securityId 每次搜索都变，不能做键。
	cached_by_item = {
		id(task.raw_item): cache.get_job_desc(task.raw_item.get("encryptJobId", ""))
		if welfare_conditions and not task.list_welfare else None
		for task in tasks
	}
	cache_hits = sum(
		1 for task in tasks
		if isinstance(cached_by_item[id(task.raw_item)], str) and not task.need_active
	)
	if cache_hits:
		logger.info(f"  详情缓存命中 {cache_hits}/{len(tasks)}，跳过对应取详情请求")

	if sequential:
		for task in tasks:
			raw_item = task.raw_item
			company = raw_item.get("brandName", "")
			title = raw_item.get("jobName", "")
			try:
				outcome = _fetch_and_check(
					client, welfare_conditions, criteria, raw_item, cached_by_item[id(raw_item)],
					active_level=active_level,
					need_active=task.need_active,
					list_welfare=task.list_welfare,
					list_active_desc=task.list_active_desc,
					before_detail_request=before_detail_request,
					detail_channel=detail_channel,
				)
			except SearchPipelinePlatformError:
				if stats is not None:
					stats.detail_requests += 1
				logger.info(f"  ❌ {company} - {title}（详情接口失败）")
				raise
			except PlatformRiskError:
				if stats is not None:
					stats.detail_requests += 1
				logger.info(f"  ❌ {company} - {title}（平台风控，停止扫描）")
				raise
			except Exception:
				logger.info(f"  ❌ {company} - {title}（查询失败）")
				continue
			_record_detail_outcome(outcome, raw_item, cache, logger, matched, stats, unknown_descs)
		return

	stop = threading.Event()
	with ThreadPoolExecutor(max_workers=_WELFARE_WORKERS) as pool:
		futures = {
			pool.submit(
				_fetch_and_check_guarded,
				stop, client, welfare_conditions, criteria, task.raw_item, cached_by_item[id(task.raw_item)],
				active_level=active_level,
				need_active=task.need_active,
				list_welfare=task.list_welfare,
				list_active_desc=task.list_active_desc,
				detail_channel=detail_channel,
			): task.raw_item
			for task in tasks
		}
		for future in as_completed(futures):
			raw_item = futures[future]
			company = raw_item.get("brandName", "")
			title = raw_item.get("jobName", "")
			try:
				outcome = future.result()
				_record_detail_outcome(outcome, raw_item, cache, logger, matched, stats, unknown_descs)
			except SearchPipelinePlatformError:
				logger.info(f"  ❌ {company} - {title}（详情接口失败）")
				stop.set()
				pool.shutdown(wait=False, cancel_futures=True)
				raise
			except PlatformRiskError:
				# 风控异常形态（job_card 在 httpx 失败后降级到浏览器通道时抛出）。
				# 响应字典形态已在 _fetch_and_check 里经 parse_error 包成 SearchPipelinePlatformError，
				# 这一支专门堵「异常形态被 except Exception 吞掉、扫描继续跑完整页」的缺口（Issue #419）。
				logger.info(f"  ❌ {company} - {title}（平台风控，停止扫描）")
				stop.set()
				pool.shutdown(wait=False, cancel_futures=True)
				raise
			except Exception:
				logger.info(f"  ❌ {company} - {title}（查询失败）")


# ── Main pipeline ───────────────────────────────────────────────────


def run_search_pipeline(
	client: Any,
	cache: Any,
	logger: Any,
	*,
	criteria: SearchFilterCriteria,
	start_page: int = 1,
	max_pages: int = 1,
	limit: int | None = None,
	welfare_conditions: list[tuple[str, list[str]]] | None = None,
	skip_greeted: bool = False,
	before_list_request: Callable[[], None] | None = None,
	active: str | None = None,
	before_detail_request: Callable[[], None] | None = None,
	detail_channel: str = "auto",
) -> SearchPipelineResult:
	"""Run the full search pipeline: API search → list prefilter → welfare / active detail fallback.

	--active 的请求量控制（#451 回归修复）：
	- online 只看列表，永不查详情，只翻 1 页；
	- 其余档位不带 --welfare 时最多 _ACTIVE_MAX_PAGES 页，且只有「结果不足目标条数、
	  本页仍有职位需要查详情」时才翻下一页；
	- 需要查详情时串行取详情，每次请求前调用 before_detail_request 做间隔；
	  detail_channel=browser 时只走浏览器通道取 job_card。
	- CDP 模式（client.is_browser_only()，或 detail_channel=browser）下福利补详情也串行，
	  不开线程池，避免多个请求同时打到用户 Chrome。
	"""
	stats = SearchPipelineStats()
	matched: list[dict[str, Any]] = []
	unknown_descs: dict[str, int] = {}
	active_level = resolve_active_level(active)
	current_page = start_page
	last_page_scanned = 0
	has_more = False
	active_only = bool(active_level) and not welfare_conditions
	if active_only:
		max_pages = min(max_pages, detail_filter_max_pages(None, active_level))
	active_target = limit or _ACTIVE_DEFAULT_TARGET

	for _ in range(max_pages):
		if limit and len(matched) >= limit:
			break

		logger.info(f"正在搜索第 {current_page} 页...")
		search_filters: dict[str, Any] = {
			"city": criteria.city,
			"salary": criteria.salary,
			"experience": criteria.experience,
			"education": criteria.education,
			"industry": criteria.industry,
			"scale": criteria.scale,
			"stage": criteria.stage,
			"job_type": criteria.job_type,
			"page": current_page,
		}
		if criteria.raw_params:
			search_filters["raw_params"] = criteria.raw_params

		if before_list_request is not None:
			before_list_request()
		raw = client.search_jobs(
			criteria.query,
			**search_filters,
		)
		if not client.is_success(raw):
			code, message = client.parse_error(raw)
			details = None
			if isinstance(raw, dict):
				error = raw.get("error")
				if isinstance(error, dict) and isinstance(error.get("details"), dict):
					details = error["details"]
			raise SearchPipelinePlatformError(code, message or "搜索结果获取失败", details=details)
		platform_data = _unwrap_platform_data(client, raw)
		job_list = platform_data.get("jobList", [])
		last_page_scanned = current_page
		stats.pages_scanned += 1
		stats.jobs_seen += len(job_list)

		if not job_list:
			break

		# Phase 1: list-page prefilter
		survivors = []
		for raw_item in job_list:
			ok, reasons = prefilter_job(raw_item, criteria, platform_name=getattr(client, "name", None))
			if not ok:
				stats.jobs_prefiltered += 1
				logger.info(f"  预筛排除: {raw_item.get('jobName', '')} ({', '.join(reasons)})")
				continue
			survivors.append(raw_item)

		# Phase 2: welfare / active filtering or direct collection
		page_needed_detail = False
		if welfare_conditions or active_level:
			need_detail: list[_DetailTask] = []
			for raw_item in survivors:
				active_desc = ""
				need_active = False
				if active_level:
					decision, active_desc = list_active_decision(raw_item, active_level)
					if decision == "reject":
						stats.active_rejected += 1
						logger.info(f"  活跃度排除: {raw_item.get('jobName', '')} ({active_desc})")
						continue
					if decision == "unknown":
						stats.active_unknown += 1
						unknown_descs[active_desc] = unknown_descs.get(active_desc, 0) + 1
						logger.info(f"  活跃度未知，排除: {raw_item.get('jobName', '')} ({active_desc})")
						continue
					need_active = decision == "detail"
				match_results: list[str] = []
				if welfare_conditions:
					match_results = match_all_welfare(welfare_conditions, raw_item.get("welfareList", []), "")
				if need_active or (welfare_conditions and not match_results):
					need_detail.append(_DetailTask(
						raw_item,
						need_active=need_active,
						list_welfare=match_results or None,
						list_active_desc=active_desc,
					))
					continue
				item = JobItem.from_api(raw_item)
				item.greeted = cache.is_greeted(item.security_id)
				if skip_greeted and item.greeted:
					continue
				d = item.to_dict()
				if active_desc:
					d["boss_active_desc"] = active_desc
				if match_results:
					d["welfare_match"] = "✅ " + ", ".join(match_results)
				d["match_score"] = compute_match_score(d, match_results, criteria)
				matched.append(d)
				stats.jobs_matched += 1
				logger.info(f"  ✅ {item.company} - {item.title}（{'标签匹配' if match_results else '列表活跃度匹配'}）")

			if need_detail:
				page_needed_detail = True
				reason = "标签未命中" if not active_level else "列表信息不足"
				# CDP 模式（配置或本进程已接上 CDP Chrome）：详情只走浏览器 job_card，且不并发。
				if detail_channel != "browser" and client_is_browser_only(client):
					detail_channel = "browser"
				# 有 --active 或 CDP 模式时串行 + 间隔取详情（单独或叠加 --welfare 都一样保守）
				sequential = bool(active_level) or detail_channel == "browser"
				logger.info(f"  {reason} {len(need_detail)} 个，{'逐个' if sequential else '并行'}查详情...")
				before = len(matched)
				_check_details_parallel(
					client, cache, logger, welfare_conditions or [], criteria, need_detail, matched,
					active_level=active_level, stats=stats, unknown_descs=unknown_descs,
					sequential=sequential,
					before_detail_request=before_detail_request if sequential else None,
					detail_channel=detail_channel,
				)
				stats.detail_checks += len(need_detail)
				stats.jobs_matched += len(matched) - before

			# Post-filter skip_greeted for detail-matched items
			if skip_greeted:
				matched = [m for m in matched if not m.get("greeted", False)]
		else:
			for raw_item in survivors:
				item = JobItem.from_api(raw_item)
				item.greeted = cache.is_greeted(item.security_id)
				if skip_greeted and item.greeted:
					continue
				d = item.to_dict()
				d["match_score"] = compute_match_score(d, [], criteria)
				matched.append(d)
				stats.jobs_matched += 1

		has_more = platform_data.get("hasMore", False)
		if not has_more:
			break
		if limit and len(matched) >= limit:
			break
		if active_only and (len(matched) >= active_target or not page_needed_detail):
			# 已凑够，或本页全靠列表字段判定完——不再为活跃度多翻页
			break
		current_page += 1

	if limit:
		matched = matched[:limit]

	return SearchPipelineResult(
		items=matched,
		has_more=has_more,
		total=len(matched),
		last_page=last_page_scanned,
		stats=stats,
		active_unknown_descs=unknown_descs,
	)
