"""Recruiter automation commands."""

from __future__ import annotations

import json
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

import click

from boss_agent_cli.automation.adapters import build_automation_adapter
from boss_agent_cli.automation.config import automation_config_from_dict
from boss_agent_cli.automation.events import make_event, now_iso
from boss_agent_cli.automation.models import (
	AutomationMode,
	EventStatus,
	PlatformAction,
	RunReport,
)
from boss_agent_cli.automation.runner import run_automation_cycle
from boss_agent_cli.automation.storage import AutomationStore
from boss_agent_cli.ai.service import AIServiceError
from boss_agent_cli.cache.store import CacheStore
from boss_agent_cli.commands.ai_cmd import _build_fit_prompt, _create_ai_service
from boss_agent_cli.commands.crawl import _require_crawl_capabilities, _settings_from_context, _transport_factory
from boss_agent_cli.crawler.operations import crawl_status, import_crawl_shortlist
from boss_agent_cli.crawler.service import CrawlService
from boss_agent_cli.display import handle_error_output, handle_output
from boss_agent_cli.resume.models import resume_to_text
from boss_agent_cli.resume.store import ResumeStore


@click.group("agent")
def agent_group() -> None:
	"""招聘自动化入口。"""


@agent_group.command("run")
@click.option(
	"--dry-run",
	is_flag=True,
	default=False,
	help="只演练自动化决策，不执行真实平台动作",
)
@click.option("--limit", default=None, type=int, help="本轮最多处理多少个会话")
@click.pass_context
def run_cmd(ctx: click.Context, dry_run: bool, limit: int | None) -> None:
	"""运行一轮招聘自动化。"""
	report = _run_agent(ctx, dry_run=dry_run, limit=limit, force_mode=None)
	handle_output(ctx, "agent.run", _report_payload(report), hints=_agent_hints(ctx))


@agent_group.command("crawl")
@click.option("--run-id", default=None, help="分析指定的已完成 crawl run")
@click.option("--query", default=None, help="新采集的职位关键词；需要 --allow-crawl")
@click.option("--city", default=None, help="新采集城市；与 --query 同时使用")
@click.option("--pages", default=5, type=click.IntRange(1), show_default=True, help="新采集严格页数上限")
@click.option("--with-detail", is_flag=True, default=False, help="新采集时串行补职位详情")
@click.option("--allow-crawl", is_flag=True, default=False, help="明确授权 Agent 启动新的真实 Chrome 采集")
@click.option("--resume", "resume_name", required=True, help="用于 ai fit 的本地简历名称")
@click.option("--limit", default=10, type=click.IntRange(min=1), show_default=True, help="按匹配分返回前 N 个职位")
@click.pass_context
def crawl_cmd(
	ctx: click.Context,
	run_id: str | None,
	query: str | None,
	city: str | None,
	pages: int,
	with_detail: bool,
	allow_crawl: bool,
	resume_name: str,
	limit: int,
) -> None:
	"""将 crawl 结果导入候选池并执行 ai fit；新采集必须显式授权。"""
	if bool(run_id) == bool(query):
		handle_error_output(
			ctx,
			"agent.crawl",
			code="INVALID_PARAM",
			message="必须且只能使用 --run-id 或 --query",
		)
		return
	if query and not allow_crawl:
		handle_error_output(
			ctx,
			"agent.crawl",
			code="CRAWL_PERMISSION_REQUIRED",
			message="Agent 默认不启动新的 crawl；需要 operating_mode=research 和 --allow-crawl",
			recoverable=True,
			recovery_action="boss config set operating_mode research; boss agent crawl --query <关键词> --city <城市> --allow-crawl --resume <简历名>",
		)
		return
	if query and not _require_crawl_capabilities(ctx):
		ctx.exit(1)
		return
	if query and not city:
		handle_error_output(ctx, "agent.crawl", code="INVALID_PARAM", message="使用 --query 时必须提供 --city")
		return

	data_dir = ctx.obj["data_dir"]
	try:
		with CacheStore(data_dir / "cache" / "boss_agent.db") as cache:
			if query:
				settings = _settings_from_context(
					ctx,
					query=query,
					city=city or "",
					pages=pages,
					with_detail=with_detail,
					hook_profile=None,
					hook_dir=None,
					profile_path=None,
					chrome_path=None,
					cdp_port=None,
				)
				outcome = CrawlService(
					cache,
					data_dir=data_dir,
					transport_factory=_transport_factory,
				).create_and_run(settings)
				run_id = outcome.run_id
			status = crawl_status(cache, run_id or "")
			if status["status"] != "completed":
				handle_error_output(
					ctx,
					"agent.crawl",
					code="CRAWL_NOT_COMPLETED",
					message=f"crawl run {run_id} 当前状态为 {status['status']}，不会导入不完整结果",
					recoverable=True,
					recovery_action=status["checkpoint"]["resume_command"],
				)
				return
			imported = import_crawl_shortlist(
				cache,
				run_id or "",
				include_all=True,
				tags=("agent", "crawl"),
				note=f"agent:crawl:{run_id}",
			)
			# Keep raw identifiers inside the local run while returning selectors to callers.
			crawl_jobs = cache.list_crawl_jobs(run_id or "")
			jobs, missing = _crawl_fit_jobs(cache, crawl_jobs)
	except KeyError:
		handle_error_output(ctx, "agent.crawl", code="JOB_NOT_FOUND", message=f"未找到 crawl run: {run_id}")
		return
	except ValueError as exc:
		handle_error_output(ctx, "agent.crawl", code="INVALID_PARAM", message=str(exc))
		return
	except RuntimeError as exc:
		handle_error_output(
			ctx,
			"agent.crawl",
			code="CRAWL_UNAVAILABLE",
			message=str(exc),
			recoverable=True,
			recovery_action="安装 boss-agent-cli[crawl] 并执行 boss crawl configure",
		)
		return

	resume_text = _resume_text(data_dir, resume_name)
	if resume_text is None:
		handle_error_output(ctx, "agent.crawl", code="RESUME_NOT_FOUND", message=f"简历 '{resume_name}' 不存在")
		return
	if not jobs:
		handle_output(
			ctx,
			"agent.crawl",
			{
				"run_id": run_id,
				"crawl": status,
				"shortlist": imported,
				"results": [],
				"missing": missing,
				"summary": {"analyzed": 0, "missing_details": len(missing)},
			},
			hints={"next_actions": [f"boss crawl resume {run_id} --with-detail"]},
		)
		return

	svc = _create_ai_service(ctx)
	if svc is None:
		handle_error_output(
			ctx,
			"agent.crawl",
			code="AI_NOT_CONFIGURED",
			message="AI 服务未配置；crawl 结果已导入 shortlist",
			recoverable=True,
			recovery_action="boss ai config --provider <provider> --model <model> --api-key <key>",
		)
		return
	try:
		fit = _fit_crawl_jobs(svc, resume_text, jobs)
	except (AIServiceError, ValueError, RuntimeError) as exc:
		handle_error_output(
			ctx,
			"agent.crawl",
			code="AI_API_ERROR",
			message=f"AI 匹配失败: {exc}",
			recoverable=True,
			recovery_action="检查 AI 配置后重试 boss agent crawl --run-id <run_id> --resume <简历名>",
		)
		return
	results = fit.get("results", [])
	if not isinstance(results, list):
		results = []
	results = _public_fit_results(sorted(results, key=_fit_score, reverse=True)[:limit], jobs)
	handle_output(
		ctx,
		"agent.crawl",
		{
			"run_id": run_id,
			"crawl": status,
			"shortlist": imported,
			"results": results,
			"missing": missing,
			"summary": {"analyzed": len(jobs), "missing_details": len(missing), "returned": len(results)},
		},
		hints={
			"next_actions": [
				f"boss crawl results {run_id}",
				"boss shortlist list",
			],
		},
	)


@agent_group.command("train")
@click.option(
	"--dry-run/--live",
	"dry_run",
	default=True,
	help="训练模式默认只写人审队列",
)
@click.option("--limit", default=None, type=int, help="本轮最多处理多少个会话")
@click.pass_context
def train_cmd(ctx: click.Context, dry_run: bool, limit: int | None) -> None:
	"""运行训练校准模式：自动判断，但动作进入人审。"""
	report = _run_agent(
		ctx,
		dry_run=dry_run,
		limit=limit,
		force_mode=AutomationMode.TRAINING,
	)
	handle_output(ctx, "agent.train", _report_payload(report), hints=_agent_hints(ctx))


@agent_group.group("review")
def review_group() -> None:
	"""人工复核队列。"""


@review_group.command("list")
@click.pass_context
def review_list_cmd(ctx: click.Context) -> None:
	store = AutomationStore(ctx.obj["data_dir"])
	handle_output(
		ctx,
		"agent.review.list",
		{"items": [asdict(item) for item in store.read_reviews()]},
		hints=_agent_hints(ctx),
	)


@review_group.command("approve")
@click.argument("review_id")
@click.pass_context
def review_approve_cmd(ctx: click.Context, review_id: str) -> None:
	"""批准一条人工复核动作，写入 pending 队列。"""
	store = AutomationStore(ctx.obj["data_dir"])
	pending = store.approve_review(review_id, now_iso())
	if pending is None:
		raise click.ClickException(f"review item not found or not reviewable: {review_id}")
	handle_output(
		ctx,
		"agent.review.approve",
		{"pending": asdict(pending)},
		hints=_agent_hints(ctx),
	)


@review_group.command("reject")
@click.argument("review_id")
@click.option("--reason", default="human-rejected", help="拒绝原因")
@click.pass_context
def review_reject_cmd(ctx: click.Context, review_id: str, reason: str) -> None:
	"""拒绝一条人工复核动作，并记录跳过事件。"""
	store = AutomationStore(ctx.obj["data_dir"])
	rejected = store.reject_review(review_id, reason, now_iso())
	if rejected is None:
		raise click.ClickException(f"review item not found or not reviewable: {review_id}")
	event = make_event(
		rejected.platform,
		rejected.candidate_key,
		PlatformAction(rejected.action),
		EventStatus.SKIPPED,
		rejected.confidence,
		f"human rejected: {reason}",
	)
	store.append_event(event)
	handle_output(
		ctx,
		"agent.review.reject",
		{"review": asdict(rejected), "event": asdict(event)},
		hints=_agent_hints(ctx),
	)


@agent_group.group("pending")
def pending_group() -> None:
	"""待执行动作队列。"""


@pending_group.command("list")
@click.pass_context
def pending_list_cmd(ctx: click.Context) -> None:
	store = AutomationStore(ctx.obj["data_dir"])
	handle_output(
		ctx,
		"agent.pending.list",
		{"items": [asdict(item) for item in store.read_pending()]},
		hints=_agent_hints(ctx),
	)


@agent_group.command("stats")
@click.pass_context
def agent_stats_cmd(ctx: click.Context) -> None:
	"""查看招聘自动化统计。"""
	store = AutomationStore(ctx.obj["data_dir"])
	handle_output(ctx, "agent.stats", store.stats(), hints=_agent_hints(ctx))


@agent_group.command("control")
@click.pass_context
def control_cmd(ctx: click.Context) -> None:
	"""返回本地控制台入口信息。"""
	handle_output(
		ctx,
		"agent.control",
		{
			"status": "available_via_cli",
			"note": (
				"首版控制台能力已统一到 agent CLI；"
				"Web 控制台将在后续接入同一状态目录"
			),
			"commands": [
				"boss agent run",
				"boss agent stats",
				"boss agent review list",
				"boss agent pending list",
			],
		},
		hints=_agent_hints(ctx),
	)


@agent_group.command("stop")
@click.option("--reason", default="manual-stop", help="熔断原因")
@click.pass_context
def stop_cmd(ctx: click.Context, reason: str) -> None:
	"""打开招聘自动化熔断。"""
	store = AutomationStore(ctx.obj["data_dir"])
	state = store.read_state()
	state.setdefault("autonomy", {})["circuit_breaker"] = {
		"open": True,
		"reason": reason,
	}
	store.write_state(state)
	handle_output(
		ctx,
		"agent.stop",
		{"status": "CIRCUIT_BREAKER_OPEN", "reason": reason},
		hints=_agent_hints(ctx),
	)


def _run_agent(
	ctx: click.Context,
	*,
	dry_run: bool,
	limit: int | None,
	force_mode: AutomationMode | None,
) -> RunReport:
	cfg = automation_config_from_dict((ctx.obj.get("config") or {}).get("automation"))
	if force_mode is not None:
		cfg = replace(cfg, mode=force_mode)
	platform = ctx.obj.get("platform") or "zhipin"
	store = AutomationStore(ctx.obj["data_dir"])
	adapter = build_automation_adapter(
		platform,
		data_dir=ctx.obj["data_dir"],
		delay=ctx.obj.get("delay", (1.5, 3.0)),
		cdp_url=ctx.obj.get("cdp_url"),
		live=not dry_run or platform == "zhilian",
	)
	return run_automation_cycle(
		adapter,
		store,
		cfg,
		platform=platform,
		dry_run=dry_run,
		limit=limit,
	)


def _resume_text(data_dir: Path, resume_name: str) -> str | None:
	resume = ResumeStore(data_dir / "resumes").get(resume_name)
	return resume_to_text(resume) if resume is not None else None


def _crawl_fit_jobs(cache: CacheStore, crawl_jobs: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
	jobs: list[dict[str, Any]] = []
	missing: list[dict[str, Any]] = []
	for crawl_job in crawl_jobs:
		item = crawl_job["payload"]
		selector = str(crawl_job["selector"])
		job_id = str(item.get("job_id") or "")
		description = cache.get_job_desc(job_id) or str(item.get("post_description") or "")
		if description:
			jobs.append({
				"selector": selector,
				"title": item.get("title", ""),
				"company": item.get("company", ""),
				"city": item.get("city", ""),
				"salary": item.get("salary", ""),
				"description": description,
			})
		else:
			missing.append({
				"selector": selector,
				"title": item.get("title", ""),
				"company": item.get("company", ""),
				"status": "缺详情",
			})
	return jobs, missing


def _public_fit_results(results: list[Any], jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
	"""Replace local identifiers in AI fit output with the run-scoped selector."""
	selectors = {str(job["selector"]) for job in jobs}
	selectors_by_title = {
		str(job["title"]): str(job["selector"])
		for job in jobs
		if sum(str(candidate["title"]) == str(job["title"]) for candidate in jobs) == 1
	}
	public: list[dict[str, Any]] = []
	for result in results:
		if not isinstance(result, dict):
			continue
		selector = str(result.get("selector") or "")
		item = {
			key: value
			for key, value in result.items()
			if key not in {"job_id", "security_id", "boss_name", "boss_title", "recruiter"}
		}
		if selector in selectors:
			item["selector"] = selector
		elif str(result.get("title") or "") in selectors_by_title:
			item["selector"] = selectors_by_title[str(result["title"])]
		public.append(item)
	return public


def _fit_crawl_jobs(svc: Any, resume_text: str, jobs: list[dict[str, Any]]) -> dict[str, Any]:
	raw = svc.chat([
		{"role": "system", "content": "你是求职顾问。所有输出使用 JSON 格式。"},
		{"role": "user", "content": _build_fit_prompt(resume_text, jobs)},
	])
	text = str(raw).strip()
	if text.startswith("```"):
		text = "\n".join(line for line in text.splitlines() if not line.startswith("```")).strip()
	try:
		payload = json.loads(text)
	except json.JSONDecodeError as exc:
		raise ValueError("AI 返回结果不是 JSON") from exc
	if not isinstance(payload, dict):
		raise ValueError("AI 返回结果不是对象")
	return payload


def _fit_score(item: Any) -> int:
	if not isinstance(item, dict):
		return 0
	try:
		return int(item.get("match_score", 0))
	except (TypeError, ValueError):
		return 0


def _report_payload(report: RunReport) -> dict[str, Any]:
	payload = asdict(report)
	payload["mode"] = report.mode.value
	return payload


def _agent_hints(ctx: click.Context) -> dict[str, Any]:
	platform = ctx.obj.get("platform") or "zhipin"
	prefix = "boss" if platform == "zhipin" else f"boss --platform {platform}"
	return {
		"next_actions": [
			f"{prefix} --role recruiter agent stats",
			f"{prefix} --role recruiter agent review list",
			f"{prefix} --role recruiter agent pending list",
		],
	}
