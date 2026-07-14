"""Sequential crawl orchestration, checkpointing and incremental export."""

from __future__ import annotations

import random
import time
import uuid
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable

from boss_agent_cli.cache.store import CacheStore
from boss_agent_cli.crawler.exporter import write_run_outputs
from boss_agent_cli.crawler.hooks import (
	UPSTREAM_COMMIT,
	UPSTREAM_REPOSITORY,
	UPSTREAM_SNAPSHOT_URL,
	HookInjection,
	HookRegistrationError,
)
from boss_agent_cli.crawler.transport import CrawlRiskError, CrawlTransport


@dataclass(frozen=True)
class CrawlSettings:
	query: str
	city_code: str
	pages: int
	with_detail: bool
	profile_path: Path
	chrome_path: str | None
	cdp_port: int
	hook_profile: str

	def as_dict(self) -> dict[str, Any]:
		return {
			"query": self.query, "city_code": self.city_code, "pages": self.pages, "with_detail": self.with_detail,
			"profile_path": str(self.profile_path), "chrome_path": self.chrome_path, "cdp_port": self.cdp_port,
			"hook_profile": self.hook_profile,
			"hook_source": {
				"repository": UPSTREAM_REPOSITORY,
				"commit": UPSTREAM_COMMIT,
				"snapshot_url": UPSTREAM_SNAPSHOT_URL,
			},
		}

	@classmethod
	def from_dict(cls, raw: dict[str, Any]) -> "CrawlSettings":
		return cls(
			query=str(raw["query"]), city_code=str(raw["city_code"]), pages=int(raw["pages"]),
			with_detail=bool(raw["with_detail"]), profile_path=Path(str(raw["profile_path"])),
			chrome_path=raw.get("chrome_path"), cdp_port=int(raw["cdp_port"]), hook_profile=str(raw["hook_profile"]),
		)


@dataclass(frozen=True)
class CrawlOutcome:
	run_id: str
	status: str
	next_page: int
	pages_completed: int
	jobs_seen: int
	detail_checks: int
	output_paths: dict[str, str]
	hooks: tuple[HookInjection, ...]
	error: str = ""

	def as_dict(self) -> dict[str, Any]:
		return {
			"run_id": self.run_id, "status": self.status, "next_page": self.next_page,
			"pages_completed": self.pages_completed, "jobs_seen": self.jobs_seen, "detail_checks": self.detail_checks,
			"checkpoint": {"next_page": self.next_page, "resume_command": f"boss crawl resume {self.run_id}"},
			"output_paths": self.output_paths,
			"hooks": [{"name": item.name, "success": item.success, "reason": item.reason} for item in self.hooks],
			"error": self.error,
		}


class CrawlBudget:
	"""Persistent, single-process rate budget shared by list and detail work."""

	def __init__(
		self,
		cache: CacheStore,
		*,
		sleeper: Callable[[float], None] = time.sleep,
		clock: Callable[[], float] = time.time,
		random_delay: Callable[[float, float], float] = random.uniform,
	) -> None:
		self._cache = cache
		self._sleeper = sleeper
		self._clock = clock
		self._random_delay = random_delay

	def wait(self, kind: str) -> None:
		low, high = (5.0, 10.0) if kind == "list" else (3.0, 6.0)
		last = self._cache.get_crawl_budget(f"zhipin:{kind}")
		interval = self._random_delay(low, high)
		now = self._clock()
		if last is not None:
			remaining = interval - (now - last)
			if remaining > 0:
				self._sleeper(remaining)
		self._cache.put_crawl_budget(f"zhipin:{kind}", self._clock())


class CrawlService:
	"""Run an explicit browser crawl and retain every recovery-relevant state."""

	def __init__(
		self,
		cache: CacheStore,
		*,
		data_dir: Path,
		transport_factory: Callable[[CrawlSettings], CrawlTransport],
		budget_factory: Callable[[CacheStore], CrawlBudget] = CrawlBudget,
	) -> None:
		self._cache = cache
		self._data_dir = data_dir
		self._transport_factory = transport_factory
		self._budget_factory = budget_factory

	def create_and_run(self, settings: CrawlSettings) -> CrawlOutcome:
		run_id = self.create(settings)
		output_dir = self._data_dir / "crawl" / "runs" / run_id
		return self._run(run_id, settings, next_page=1, output_dir=output_dir)

	def create(self, settings: CrawlSettings) -> str:
		"""Persist a new task so another process may run or resume it."""
		run_id = uuid.uuid4().hex[:12]
		output_dir = self._data_dir / "crawl" / "runs" / run_id
		self._cache.create_crawl_run(run_id, settings.as_dict(), str(output_dir), status="queued")
		return run_id

	def resume(self, run_id: str, *, pages: int | None = None, with_detail: bool = False) -> CrawlOutcome:
		run = self._cache.get_crawl_run(run_id)
		if run is None:
			raise KeyError(run_id)
		settings = CrawlSettings.from_dict(run["params"])
		if pages is not None:
			settings = replace(settings, pages=pages)
		if with_detail:
			settings = replace(settings, with_detail=True)
		pending_details = any(not item["detail_done"] for item in self._cache.list_crawl_jobs(run_id))
		needs_more_pages = (
			not bool(run["list_finished"])
			and pages is not None
			and (pages == 0 or pages >= int(run["next_page"]))
		)
		if run["status"] == "completed" and not (needs_more_pages or (with_detail and pending_details)):
			return self._completed_outcome(run)
		self._cache.update_crawl_run_params(run_id, settings.as_dict())
		return self._run(
			run_id,
			settings,
			next_page=int(run["next_page"]),
			output_dir=Path(str(run["output_dir"])),
			list_finished=bool(run["list_finished"]),
		)

	def _completed_outcome(self, run: dict[str, Any]) -> CrawlOutcome:
		output_dir = Path(str(run["output_dir"]))
		rows = self._cache.list_crawl_jobs(str(run["run_id"]))
		return CrawlOutcome(
			run_id=str(run["run_id"]), status="completed", next_page=int(run["next_page"]),
			pages_completed=max(0, int(run["next_page"]) - 1), jobs_seen=len(rows),
			detail_checks=0, output_paths=_output_paths(output_dir), hooks=_hook_injections(run.get("hook_results", [])),
			error=str(run.get("error", "")),
		)

	def _run(
		self,
		run_id: str,
		settings: CrawlSettings,
		*,
		next_page: int,
		output_dir: Path,
		list_finished: bool = False,
	) -> CrawlOutcome:
		transport = self._transport_factory(settings)
		budget = self._budget_factory(self._cache)
		hooks: tuple[HookInjection, ...] = ()
		detail_checks = 0
		status = "completed"
		error = ""
		current_page = next_page
		try:
			hooks = tuple(transport.open())
			self._cache.update_crawl_run(
				run_id,
				status="running",
				next_page=current_page,
				list_finished=list_finished,
				hook_results=_hook_metadata(hooks),
			)
			detail_checks += self._complete_pending_details(run_id, settings, transport, budget)
			while not list_finished and (settings.pages == 0 or current_page <= settings.pages):
				body = self._with_one_retry(lambda: self._fetch_list(transport, budget, settings, current_page))
				jobs_data = body.get("zpData", {}) if isinstance(body.get("zpData"), dict) else {}
				jobs = jobs_data.get("jobList", [])
				if not isinstance(jobs, list):
					raise RuntimeError(f"第 {current_page} 页 jobList 不是列表")
				for rank, raw in enumerate(jobs, 1):
					if not isinstance(raw, dict):
						continue
					job_key, row = _normalize_job(raw, settings.query, current_page, rank)
					existing = self._cache.get_crawl_job(run_id, job_key)
					if existing is not None:
						continue
					# 原始列表项必须先落库：详情的风险码或第二次超时不会丢失待补队列。
					self._cache.put_crawl_job(run_id, job_key, current_page, row, detail_done=False)

				current_page += 1
				list_finished = not bool(jobs_data.get("hasMore", True))
				self._cache.update_crawl_run(
					run_id,
					status="running",
					next_page=current_page,
					list_finished=list_finished,
					hook_results=_hook_metadata(hooks),
				)
				detail_checks += self._complete_pending_details(run_id, settings, transport, budget)
				paths = write_run_outputs(output_dir, [item["payload"] for item in self._cache.list_crawl_jobs(run_id)])
				if list_finished:
					break
		except HookRegistrationError as exc:
			hooks = exc.injections
			status = "stopped"
			error = str(exc)
		except CrawlRiskError as exc:
			status = "risk_stopped"
			error = str(exc)
		except Exception as exc:
			status = "stopped"
			error = str(exc)
		finally:
			transport.close()

		self._cache.update_crawl_run(
			run_id,
			status=status,
			next_page=current_page,
			error=error,
			list_finished=list_finished,
			hook_results=_hook_metadata(hooks),
		)
		rows = [item["payload"] for item in self._cache.list_crawl_jobs(run_id)]
		paths = write_run_outputs(output_dir, rows)
		return CrawlOutcome(
			run_id=run_id, status=status, next_page=current_page, pages_completed=max(0, current_page - 1),
			jobs_seen=len(rows), detail_checks=detail_checks,
			output_paths=paths, hooks=hooks, error=error,
		)

	def _complete_pending_details(
		self,
		run_id: str,
		settings: CrawlSettings,
		transport: CrawlTransport,
		budget: CrawlBudget,
	) -> int:
		if not settings.with_detail:
			return 0
		checks = 0
		for item in self._cache.list_crawl_jobs(run_id):
			if item["detail_done"]:
				continue
			checks += 1
			row = item["payload"]
			job_id = str(row.get("job_id", ""))
			cached = self._cache.get_job_desc(job_id)
			if cached:
				row["post_description"] = cached
				row["detail_status"] = "cached"
			else:
				security_id = str(row.get("security_id", ""))

				def fetch_detail() -> dict[str, Any]:
					return self._fetch_detail(transport, budget, security_id)

				detail = self._with_one_retry(fetch_detail)
				card = _unwrap_card(detail)
				row.update({
					"post_description": card.get("postDescription", ""), "address": card.get("address", ""),
					"boss_name": card.get("bossName", ""), "boss_title": card.get("bossTitle", ""), "detail_status": "fetched",
				})
				self._cache.put_job_desc(job_id, str(row["post_description"]))
			self._cache.put_crawl_job(run_id, item["job_key"], int(item["page_no"]), row, detail_done=True)
		return checks

	@staticmethod
	def _with_one_retry(action: Callable[[], dict[str, Any]]) -> dict[str, Any]:
		last_error: Exception | None = None
		for _ in range(2):
			try:
				return action()
			except CrawlRiskError:
				raise
			except Exception as exc:
				last_error = exc
		if last_error is None:
			raise RuntimeError("crawl operation failed")
		raise last_error

	@staticmethod
	def _fetch_list(transport: CrawlTransport, budget: CrawlBudget, settings: CrawlSettings, page_no: int) -> dict[str, Any]:
		budget.wait("list")
		payload = transport.fetch_page(settings.query, settings.city_code, page_no)
		if payload.get("code") in (37, 38):
			raise CrawlRiskError(f"第 {page_no} 页平台返回风险码 code={payload.get('code')}: {payload.get('message', '')}")
		if payload.get("code") not in (None, 0):
			raise RuntimeError(f"第 {page_no} 页接口错误 code={payload.get('code')}: {payload.get('message', '')}")
		return payload

	@staticmethod
	def _fetch_detail(transport: CrawlTransport, budget: CrawlBudget, security_id: str) -> dict[str, Any]:
		budget.wait("detail")
		payload = transport.fetch_detail(security_id)
		if payload.get("code") in (37, 38):
			raise CrawlRiskError(f"职位详情接口返回风险码 code={payload.get('code')}: {payload.get('message', '')}")
		if payload.get("code") not in (None, 0):
			raise RuntimeError(f"职位详情接口错误 code={payload.get('code')}: {payload.get('message', '')}")
		return payload


def _normalize_job(raw: dict[str, Any], query: str, page_no: int, rank: int) -> tuple[str, dict[str, Any]]:
	job_id = str(raw.get("encryptJobId") or "")
	security_id = str(raw.get("securityId") or "")
	job_key = job_id or security_id or f"{page_no}:{rank}:{raw.get('jobName', '')}"
	labels = raw.get("jobLabels", [])
	benefits = raw.get("welfareList", [])
	return job_key, {
		"query": query, "page": page_no, "rank": rank, "job_id": job_id, "security_id": security_id,
		"title": raw.get("jobName", ""), "salary": raw.get("salaryDesc", ""), "city": raw.get("cityName", ""),
		"district": raw.get("areaDistrict", ""), "business_district": raw.get("businessDistrict", ""),
		"company": raw.get("brandName", ""), "company_scale": raw.get("brandScaleName", ""),
		"industry": raw.get("brandIndustry", ""), "education": raw.get("jobDegree", ""),
		"experience": raw.get("jobExperience", ""), "labels": "、".join(labels) if isinstance(labels, list) else str(labels or ""),
		"benefits": "、".join(benefits) if isinstance(benefits, list) else str(benefits or ""),
		"post_description": "", "address": "", "boss_name": raw.get("bossName", ""),
		"boss_title": raw.get("bossTitle", ""), "detail_status": "not_requested",
	}


def _unwrap_card(payload: dict[str, Any]) -> dict[str, Any]:
	data = payload.get("zpData", {})
	if not isinstance(data, dict):
		return {}
	card = data.get("jobCard", {})
	return card if isinstance(card, dict) else {}


def _hook_metadata(hooks: tuple[HookInjection, ...]) -> list[dict[str, Any]]:
	return [{"name": item.name, "success": item.success, "reason": item.reason} for item in hooks]


def _hook_injections(raw: Any) -> tuple[HookInjection, ...]:
	if not isinstance(raw, list):
		return ()
	return tuple(
		HookInjection(
			name=str(item.get("name", "")),
			success=bool(item.get("success", False)),
			reason=str(item.get("reason", "")),
		)
		for item in raw
		if isinstance(item, dict)
	)


def _output_paths(output_dir: Path) -> dict[str, str]:
	return {extension: str(output_dir / f"jobs.{extension}") for extension in ("json", "csv", "xlsx")}
