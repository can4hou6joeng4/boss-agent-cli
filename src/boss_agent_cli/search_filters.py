"""Reusable search pipeline — list-page prefiltering + welfare detail fallback.

Centralizes filtering logic shared by search, batch-greet, and export commands.
"""
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any

from boss_agent_cli.api.models import JobItem

# ── Ordinal lookups for threshold comparisons ───────────────────────

_EXPERIENCE_ORDER: dict[str, int] = {
	"应届": 0, "1年以内": 1, "1-3年": 2, "3-5年": 3, "5-10年": 4, "10年以上": 5,
}

_EDUCATION_ORDER: dict[str, int] = {
	"初中及以下": 0, "中专/中技": 1, "高中": 2, "大专": 3, "本科": 4, "硕士": 5, "博士": 6,
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

_MAX_FILTER_PAGES = 5
_WELFARE_WORKERS = 3

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


@dataclass
class SearchPipelineStats:
	pages_scanned: int = 0
	jobs_seen: int = 0
	jobs_prefiltered: int = 0
	detail_checks: int = 0
	jobs_matched: int = 0


@dataclass
class SearchPipelineResult:
	items: list[dict[str, Any]] = field(default_factory=list)
	has_more: bool = False
	total: int | None = None
	last_page: int = 0
	stats: SearchPipelineStats = field(default_factory=SearchPipelineStats)


# ── List-page prefilter ─────────────────────────────────────────────

def prefilter_job(raw_item: dict[str, Any], criteria: SearchFilterCriteria) -> tuple[bool, list[str]]:
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

	return (len(reasons) == 0, reasons)


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


def _fetch_and_check(client: Any, welfare_conditions: list[tuple[str, list[str]]], raw_item: dict[str, Any]) -> dict[str, Any] | None:
	"""Single job: fetch detail + welfare match. 不访问 cache（线程安全）。"""
	welfare_list = raw_item.get("welfareList", [])
	try:
		card_raw = client.job_card(
			raw_item.get("securityId", ""),
			raw_item.get("lid", ""),
		)
		desc = card_raw.get("zpData", {}).get("jobCard", {}).get("postDescription", "")
	except (OSError, KeyError, TypeError):
		desc = ""

	match_results = match_all_welfare(welfare_conditions, welfare_list, desc)
	if match_results:
		item = JobItem.from_api(raw_item)
		d = item.to_dict()
		d["welfare_match"] = "✅ " + ", ".join(match_results)
		return d
	return None


def _check_details_parallel(
	client: Any,
	cache: Any,
	logger: Any,
	welfare_conditions: list[tuple[str, list[str]]],
	items: list[dict[str, Any]],
	matched: list[dict[str, Any]],
) -> None:
	"""Parallel detail check, append matched to list. cache 操作在主线程完成。"""
	with ThreadPoolExecutor(max_workers=_WELFARE_WORKERS) as pool:
		futures = {
			pool.submit(_fetch_and_check, client, welfare_conditions, raw_item): raw_item
			for raw_item in items
		}
		for future in as_completed(futures):
			raw_item = futures[future]
			company = raw_item.get("brandName", "")
			title = raw_item.get("jobName", "")
			try:
				result = future.result()
				if result:
					# is_greeted 在主线程中安全访问 cache
					sid = result.get("security_id", "")
					if sid:
						result["greeted"] = cache.is_greeted(sid)
					matched.append(result)
					logger.info(f"  ✅ {company} - {title}（详情匹配）")
				else:
					logger.info(f"  ❌ {company} - {title}")
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
) -> SearchPipelineResult:
	"""Run the full search pipeline: API search → list prefilter → welfare detail fallback."""
	stats = SearchPipelineStats()
	matched: list[dict[str, Any]] = []
	current_page = start_page
	last_page_scanned = 0
	has_more = False

	for _ in range(max_pages):
		if limit and len(matched) >= limit:
			break

		logger.info(f"正在搜索第 {current_page} 页...")
		raw = client.search_jobs(
			criteria.query,
			city=criteria.city, salary=criteria.salary,
			experience=criteria.experience, education=criteria.education,
			industry=criteria.industry, scale=criteria.scale,
			stage=criteria.stage, job_type=criteria.job_type,
			page=current_page,
		)
		zp_data = raw.get("zpData", {})
		job_list = zp_data.get("jobList", [])
		last_page_scanned = current_page
		stats.pages_scanned += 1
		stats.jobs_seen += len(job_list)

		if not job_list:
			break

		# Phase 1: list-page prefilter
		survivors = []
		for raw_item in job_list:
			ok, reasons = prefilter_job(raw_item, criteria)
			if not ok:
				stats.jobs_prefiltered += 1
				logger.info(f"  预筛排除: {raw_item.get('jobName', '')} ({', '.join(reasons)})")
				continue
			survivors.append(raw_item)

		# Phase 2: welfare filtering or direct collection
		if welfare_conditions:
			need_detail = []
			for raw_item in survivors:
				welfare_list = raw_item.get("welfareList", [])
				match_results = match_all_welfare(welfare_conditions, welfare_list, "")
				if match_results:
					item = JobItem.from_api(raw_item)
					item.greeted = cache.is_greeted(item.security_id)
					if skip_greeted and item.greeted:
						continue
					d = item.to_dict()
					d["welfare_match"] = "✅ " + ", ".join(match_results)
					matched.append(d)
					stats.jobs_matched += 1
					logger.info(f"  ✅ {item.company} - {item.title}（标签匹配）")
				else:
					need_detail.append(raw_item)

			if need_detail:
				logger.info(f"  标签未命中 {len(need_detail)} 个，并行查详情...")
				before = len(matched)
				_check_details_parallel(client, cache, logger, welfare_conditions, need_detail, matched)
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
				matched.append(item.to_dict())
				stats.jobs_matched += 1

		has_more = zp_data.get("hasMore", False)
		if not has_more:
			break
		if limit and len(matched) >= limit:
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
	)
