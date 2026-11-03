"""Explicit DrissionPage bulk crawl commands."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import click

from boss_agent_cli.api.cdp_risk_lock import UNLOCK_OPERATOR_ACTIONS, raise_if_locked
from boss_agent_cli.api.endpoints import CITY_CODES
from boss_agent_cli.auth.manager import AuthManager, AuthRequired
from boss_agent_cli.cache.store import CacheRecordInvalid, CacheStore
from boss_agent_cli.compliance import operating_mode, require_compliance_allowed
from boss_agent_cli.config import DEFAULTS
from boss_agent_cli.crawler.operations import crawl_results, crawl_status, import_crawl_shortlist
from boss_agent_cli.crawler.service import (
	CrawlService,
	CrawlSettings,
	crawl_cdp_port,
	crawl_chrome_path,
	crawl_int_setting,
	unused_local_port,
)
from boss_agent_cli.crawler.transport import DrissionCrawlerSession
from boss_agent_cli.services.json_files import write_json_atomic
from boss_agent_cli.display import (
	handle_auth_errors,
	handle_error_output,
	handle_output,
	render_action_result,
	render_record_result,
)

_HOOK_CHOICES = ("screenshot-full", "none")


@click.group("crawl")
def crawl_group() -> None:
	"""使用 DrissionPage 执行可恢复的批量职位采集。"""


def _crawl_config(ctx: click.Context) -> dict[str, Any]:
	defaults = dict(DEFAULTS["crawl"])
	raw = (ctx.obj.get("config") or {}).get("crawl", {})
	if isinstance(raw, dict):
		defaults.update(raw)
	return defaults


def _save_crawl_config(data_dir: Path, updates: dict[str, Any]) -> dict[str, Any]:
	config_path = data_dir / "config.json"
	user_cfg: dict[str, Any] = {}
	if config_path.exists():
		try:
			loaded = json.loads(config_path.read_text(encoding="utf-8"))
			if isinstance(loaded, dict):
				user_cfg = loaded
		except (OSError, json.JSONDecodeError, UnicodeDecodeError):
			pass
	crawl_cfg = user_cfg.get("crawl", {})
	if not isinstance(crawl_cfg, dict):
		crawl_cfg = {}
	crawl_cfg.update({key: value for key, value in updates.items() if value is not None})
	user_cfg["crawl"] = crawl_cfg
	# 原子写：写一半被中断的 config.json 会让所有命令卡在启动阶段。
	write_json_atomic(config_path, user_cfg, trailing_newline=True)
	return crawl_cfg


def _city_code(city: str) -> str:
	if city in CITY_CODES:
		return CITY_CODES[city]
	if city.isdigit():
		return city
	raise ValueError("city 必须是支持的城市名称或数字城市代码")


def _require_crawl_capabilities(ctx: click.Context, hook_profile: str | None = None) -> bool:
	"""Apply the shared policy before a crawl can open an isolated browser."""
	if not require_compliance_allowed(ctx, "crawl"):
		return False
	if not require_compliance_allowed(ctx, "crawl-cdp"):
		return False
	return hook_profile in (None, "none") or require_compliance_allowed(ctx, "crawl-hook")


def _settings_from_context(
	ctx: click.Context,
	*,
	query: str,
	city: str,
	pages: int,
	with_detail: bool,
	hook_profile: str | None,
	hook_dir: str | None,
	profile_path: str | None,
	chrome_path: str | None,
	cdp_port: int | None,
) -> CrawlSettings:
	cfg = _crawl_config(ctx)
	data_dir = Path(ctx.obj["data_dir"])
	resolved_profile = str(data_dir / "crawl" / "chrome-profile")
	resolved_hook = hook_profile or "none"
	if resolved_hook not in _HOOK_CHOICES:
		raise ValueError(f"unknown hook profile: {resolved_hook}")
	resolved_chrome = crawl_chrome_path(chrome_path or cfg.get("chrome_path"))
	resolved_port = cdp_port
	if resolved_port is None:
		raw_port = cfg.get("cdp_port")
		resolved_port = unused_local_port() if raw_port is None else crawl_cdp_port(raw_port)
	return CrawlSettings(
		query=query,
		city_code=_city_code(city),
		pages=pages,
		with_detail=with_detail,
		profile_path=Path(resolved_profile),
		chrome_path=resolved_chrome,
		cdp_port=resolved_port,
		hook_profile=resolved_hook,
		hook_dir=Path(hook_dir).expanduser() if hook_dir else None,
		max_requests=crawl_int_setting(cfg, "max_requests", 20),
		max_details=crawl_int_setting(cfg, "max_details", 50),
		max_seconds=crawl_int_setting(cfg, "max_seconds", 600),
		max_retries=crawl_int_setting(cfg, "max_retries", 1, minimum=0),
		operating_mode=operating_mode(ctx),
	)


def _session_seed(ctx: click.Context) -> tuple[dict[str, str], str]:
	"""Seed the isolated crawl Chrome profile with boss-login cookies."""
	auth = AuthManager(
		Path(ctx.obj["data_dir"]),
		logger=ctx.obj.get("logger"),
		platform=ctx.obj.get("platform", "zhipin"),
	)
	try:
		token = auth.get_token()
	except AuthRequired as exc:
		raise RuntimeError(str(exc)) from exc
	raw = token.get("cookies") if isinstance(token, dict) else None
	if not isinstance(raw, dict) or not raw:
		raise RuntimeError("本地登录态缺少 Cookie，请先 boss login")
	cookies = {str(name): str(value) for name, value in raw.items() if name and value is not None}
	if ctx.obj.get("platform", "zhipin") == "zhipin" and "wt2" not in cookies:
		raise RuntimeError("登录态缺少 wt2，请先 boss login")
	return cookies, str(token.get("user_agent") or "")


def _transport_factory(
	settings: CrawlSettings,
	*,
	seed_cookies: dict[str, str] | None = None,
	user_agent: str | None = None,
) -> DrissionCrawlerSession:
	return DrissionCrawlerSession(
		profile_path=settings.profile_path,
		chrome_path=settings.chrome_path,
		cdp_port=settings.cdp_port,
		hook_profile=settings.hook_profile,
		hook_dir=settings.hook_dir,
		seed_cookies=seed_cookies,
		user_agent=user_agent,
	)


def _report_corrupt_run(ctx: click.Context, command: str, run_id: str, exc: CacheRecordInvalid) -> None:
	"""本地 run 记录写坏：断点读不出来，给「重新创建任务」指引而不是 traceback。"""
	handle_error_output(
		ctx,
		command,
		code="CACHE_RECORD_INVALID",
		message=str(exc),
		hints={"next_actions": ["boss crawl start <关键词>", f"boss crawl status {run_id}"]},
	)


def _run_service(ctx: click.Context, service_call: Any) -> None:
	try:
		raise_if_locked(Path(ctx.obj["data_dir"]))
		seed_cookies, user_agent = _session_seed(ctx)
		with CacheStore(ctx.obj["data_dir"] / "cache" / "boss_agent.db") as cache:
			service = CrawlService(
				cache,
				data_dir=Path(ctx.obj["data_dir"]),
				transport_factory=lambda settings: _transport_factory(
					settings,
					seed_cookies=seed_cookies,
					user_agent=user_agent,
				),
			)
			outcome = service_call(service)
	except KeyError as exc:
		handle_error_output(ctx, "crawl", code="JOB_NOT_FOUND", message=f"未找到 crawl run: {exc.args[0]}")
		return
	except CacheRecordInvalid as exc:
		_report_corrupt_run(ctx, "crawl", exc.record_id, exc)
		return
	except ValueError as exc:
		handle_error_output(ctx, "crawl", code="INVALID_PARAM", message=str(exc))
		return
	except RuntimeError as exc:
		message = str(exc)
		if "未登录" in message or "Cookie" in message or "wt2" in message:
			handle_error_output(
				ctx,
				"crawl",
				code="AUTH_REQUIRED",
				message=message,
				recoverable=True,
				recovery_action="boss login",
			)
			return
		handle_error_output(
			ctx,
			"crawl",
			code="CRAWL_UNAVAILABLE",
			message=message,
			recoverable=True,
			recovery_action="安装 boss-agent-cli[crawl] 并执行 boss crawl configure",
		)
		return
	if outcome.status == "risk_stopped":
		hints = {"operator_actions": [
			*UNLOCK_OPERATOR_ACTIONS,
			f"完成人工恢复后，显式执行 boss crawl resume {outcome.run_id} 从原断点继续",
		]}
	elif outcome.status != "completed":
		hints = {"next_actions": [f"boss crawl resume {outcome.run_id}"]}
	else:
		hints = {
			"next_actions": [],
			"operator_actions": ["查看 output_paths 中的 JSON、CSV 或 XLSX 结果文件"],
		}
	handle_output(
		ctx,
		"crawl",
		outcome.as_dict(),
		render=lambda d: render_record_result(d, title="crawl", next_steps=hints.get("next_actions", [])),
		hints=hints,
	)


def _launch_background_resume(
	data_dir: Path,
	run_id: str,
	*,
	pages: int | None = None,
	with_detail: bool = False,
) -> None:
	"""Start the already-persisted task outside the MCP request process."""
	raise_if_locked(data_dir)
	command = [
		sys.executable,
		"-c",
		"from boss_agent_cli.main import cli; cli()",
		"--data-dir",
		str(data_dir),
		"--json",
		"crawl",
		"resume",
		run_id,
		"--from-queue",
	]
	if pages is not None:
		command.extend(["--pages", str(pages)])
	if with_detail:
		command.append("--with-detail")
	subprocess.Popen(  # noqa: S603 - fixed interpreter and in-package entrypoint
		command,
		stdin=subprocess.DEVNULL,
		stdout=subprocess.DEVNULL,
		stderr=subprocess.DEVNULL,
		creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
	)


@crawl_group.command("configure")
@click.option("--chrome-path", default=None, type=click.Path(path_type=Path), help="chrome.exe 路径")
@click.option("--port", "cdp_port", default=None, type=click.IntRange(1, 65535), help="Chrome 远程调试端口")
@click.option("--max-requests", type=click.IntRange(1), default=None, help="每个 crawl run 的总请求上限")
@click.option("--max-details", type=click.IntRange(1), default=None, help="每个 crawl run 的详情请求上限")
@click.option("--max-seconds", type=click.IntRange(1), default=None, help="每个 crawl run 的墙钟秒数上限")
@click.option("--max-retries", type=click.IntRange(0), default=None, help="单请求的重试次数上限")
@click.pass_context
def configure_cmd(
	ctx: click.Context,
	chrome_path: Path | None,
	cdp_port: int | None,
	max_requests: int | None,
	max_details: int | None,
	max_seconds: int | None,
	max_retries: int | None,
) -> None:
	"""设置 crawl 专用 Chrome 和 Hook 配置。"""
	if all(value is None for value in (chrome_path, cdp_port, max_requests, max_details, max_seconds, max_retries)):
		handle_error_output(ctx, "crawl.configure", code="INVALID_PARAM", message="至少提供一个 crawl 配置项")
		return
	updates = {
		"chrome_path": str(chrome_path) if chrome_path is not None else None,
		"cdp_port": cdp_port,
		"max_requests": max_requests,
		"max_details": max_details,
		"max_seconds": max_seconds,
		"max_retries": max_retries,
	}
	configured = _save_crawl_config(Path(ctx.obj["data_dir"]), updates)
	handle_output(
		ctx,
		"crawl.configure",
		{"crawl": configured},
		render=lambda d: render_record_result(d, title="crawl configure", next_steps=["boss crawl run <关键词> --city <城市>"]),
	)


@crawl_group.command("run")
@click.argument("query")
@click.option("--city", required=True, help="城市名称或数字城市代码")
@click.option("--pages", default=5, type=click.IntRange(1), show_default=True, help="严格页数上限")
@click.option("--with-detail", is_flag=True, default=False, help="串行补全所有职位的 job_card")
@click.option("--hook-profile", type=click.Choice(_HOOK_CHOICES), default=None)
@click.option(
	"--hook-dir", type=click.Path(path_type=Path), default=None, help="与 screenshot-full 一起显式指定的用户脚本目录"
)
@click.option("--chrome-path", default=None, type=click.Path(path_type=Path), help="覆盖配置的 chrome.exe")
@click.option("--port", "cdp_port", default=None, type=click.IntRange(1, 65535), help="覆盖配置的调试端口")
@click.pass_context
@handle_auth_errors("crawl")
def run_cmd(
	ctx: click.Context,
	query: str,
	city: str,
	pages: int,
	with_detail: bool,
	hook_profile: str | None,
	hook_dir: Path | None,
	chrome_path: Path | None,
	cdp_port: int | None,
) -> None:
	"""开始一个可恢复的 DP 批量采集任务。"""
	if not _require_crawl_capabilities(ctx, hook_profile):
		ctx.exit(1)
		return
	raise_if_locked(Path(ctx.obj["data_dir"]))
	try:
		settings = _settings_from_context(
			ctx,
			query=query,
			city=city,
			pages=pages,
			with_detail=with_detail,
			hook_profile=hook_profile,
			hook_dir=str(hook_dir) if hook_dir else None,
			profile_path=None,
			chrome_path=str(chrome_path) if chrome_path else None,
			cdp_port=cdp_port,
		)
	except ValueError as exc:
		handle_error_output(ctx, "crawl", code="INVALID_PARAM", message=str(exc))
		return
	_run_service(ctx, lambda service: service.create_and_run(settings))


@crawl_group.command("start")
@click.argument("query")
@click.option("--city", required=True, help="城市名称或数字城市代码")
@click.option("--pages", default=5, type=click.IntRange(1), show_default=True, help="严格页数上限")
@click.option("--with-detail", is_flag=True, default=False, help="串行补全所有职位的 job_card")
@click.option("--hook-profile", type=click.Choice(_HOOK_CHOICES), default=None)
@click.option(
	"--hook-dir", type=click.Path(path_type=Path), default=None, help="与 screenshot-full 一起显式指定的用户脚本目录"
)
@click.pass_context
@handle_auth_errors("crawl.start")
def start_cmd(
	ctx: click.Context,
	query: str,
	city: str,
	pages: int,
	with_detail: bool,
	hook_profile: str | None,
	hook_dir: Path | None,
) -> None:
	"""创建并后台运行一个 crawl 任务；适合 MCP 的任务式调度。"""
	if not _require_crawl_capabilities(ctx, hook_profile):
		ctx.exit(1)
		return
	raise_if_locked(Path(ctx.obj["data_dir"]))
	run_id: str | None = None
	try:
		settings = _settings_from_context(
			ctx,
			query=query,
			city=city,
			pages=pages,
			with_detail=with_detail,
			hook_profile=hook_profile,
			hook_dir=str(hook_dir) if hook_dir else None,
			profile_path=None,
			chrome_path=None,
			cdp_port=None,
		)
		with CacheStore(ctx.obj["data_dir"] / "cache" / "boss_agent.db") as cache:
			service = CrawlService(cache, data_dir=Path(ctx.obj["data_dir"]), transport_factory=_transport_factory)
			run_id = service.create(settings)
		_launch_background_resume(Path(ctx.obj["data_dir"]), run_id)
	except ValueError as exc:
		handle_error_output(ctx, "crawl.start", code="INVALID_PARAM", message=str(exc))
		return
	except OSError as exc:
		if run_id is not None:
			with CacheStore(ctx.obj["data_dir"] / "cache" / "boss_agent.db") as cache:
				try:
					run = cache.get_crawl_run(run_id)
				except CacheRecordInvalid:
					# 记录已损坏时无法回写状态：下面仍按启动失败回报，重建任务即可。
					run = None
				if run is not None:
					cache.update_crawl_run(
						run_id,
						status="stopped",
						next_page=int(run["next_page"]),
						error=f"无法启动后台任务: {exc}",
						list_finished=bool(run["list_finished"]),
					)
		# operating_mode 在新版本不再限制能力，指引应指向真正的启动失败原因。
		recovery_action = (
			f"检查磁盘空间与数据目录权限后执行 boss crawl resume {run_id} 重试"
			if run_id is not None
			else "检查磁盘空间与数据目录权限后重新执行 boss crawl start"
		)
		handle_error_output(
			ctx,
			"crawl.start",
			code="CRAWL_UNAVAILABLE",
			message=f"无法启动 crawl 后台任务: {exc}",
			recoverable=True,
			recovery_action=recovery_action,
		)
		return
	handle_output(
		ctx,
		"crawl.start",
		{
			"run_id": run_id,
			"status": "queued",
			"background": True,
			"checkpoint": {"resume_command": f"boss crawl resume {run_id}"},
		},
		render=lambda d: render_record_result(
			d, title="crawl start", next_steps=[f"boss crawl status {run_id}", f"boss crawl stop {run_id}"]
		),
		hints={"next_actions": [f"boss crawl status {run_id}", f"boss crawl results {run_id}"]},
	)


@crawl_group.command("resume")
@click.argument("run_id")
@click.option("--pages", default=None, type=click.IntRange(1), help="覆盖原任务页数上限")
@click.option("--with-detail", is_flag=True, default=False, help="补全已采职位和后续职位的 job_card")
@click.option("--background", is_flag=True, default=False, help="后台恢复，立即返回 run_id")
@click.option("--from-queue", is_flag=True, default=False, hidden=True)
@click.pass_context
@handle_auth_errors("crawl.resume")
def resume_cmd(
	ctx: click.Context,
	run_id: str,
	pages: int | None,
	with_detail: bool,
	background: bool,
	from_queue: bool,
) -> None:
	"""从已保存的页游标和详情队列恢复采集。"""
	if not _require_crawl_capabilities(ctx):
		ctx.exit(1)
		return
	raise_if_locked(Path(ctx.obj["data_dir"]))
	if background:
		already_complete = False
		try:
			with CacheStore(ctx.obj["data_dir"] / "cache" / "boss_agent.db") as cache:
				run = cache.get_crawl_run(run_id)
				if run is None:
					raise KeyError(run_id)
				status = str(run["status"])
				# 与 CrawlService.resume 的前置条件保持一致：完成的 run 只有在
				# 请求了更多页或还有待补详情时才有可恢复的工作。
				needs_more_pages = (
					not bool(run["list_finished"]) and pages is not None and pages >= int(run["next_page"])
				)
				pending_details = with_detail and any(
					not item["detail_done"] for item in cache.list_crawl_jobs(run_id)
				)
				already_complete = status == "completed" and not (needs_more_pages or pending_details)
			if not already_complete and status not in {"queued", "running"}:
				_launch_background_resume(
					Path(ctx.obj["data_dir"]),
					run_id,
					pages=pages,
					with_detail=with_detail,
				)
		except KeyError:
			handle_error_output(ctx, "crawl.resume", code="JOB_NOT_FOUND", message=f"未找到 crawl run: {run_id}")
			return
		except CacheRecordInvalid as exc:
			_report_corrupt_run(ctx, "crawl.resume", run_id, exc)
			return
		except OSError as exc:
			# 与 crawl start 同一类失败：OSError 多来自磁盘满或目录权限，直接重试不会好转。
			handle_error_output(
				ctx,
				"crawl.resume",
				code="CRAWL_UNAVAILABLE",
				message=f"无法启动 crawl 后台任务: {exc}",
				recoverable=True,
				recovery_action=f"检查磁盘空间与数据目录权限后执行 boss crawl resume {run_id} 重试",
			)
			return
		if already_complete:
			# 拉起一个没有工作的后台进程只会让它立即退出，却让调用方以为任务重新排队。
			handle_output(
				ctx,
				"crawl.resume",
				{"run_id": run_id, "status": "completed", "background": True},
				render=lambda d: render_action_result(d, title="crawl resume", next_steps=[f"boss crawl results {run_id}"]),
				hints={"next_actions": [f"boss crawl results {run_id}"]},
			)
			return
		handle_output(
			ctx,
			"crawl.resume",
			{"run_id": run_id, "status": status if status in {"queued", "running"} else "queued", "background": True},
			render=lambda d: render_action_result(d, title="crawl resume", next_steps=[f"boss crawl status {run_id}"]),
			hints={"next_actions": [f"boss crawl status {run_id}"]},
		)
		return
	_run_service(
		ctx,
		lambda service: service.resume(
			run_id,
			pages=pages,
			with_detail=with_detail,
			operating_mode=operating_mode(ctx),
			clear_stop=not from_queue,
		),
	)


@crawl_group.command("status")
@click.argument("run_id")
@click.pass_context
def status_cmd(ctx: click.Context, run_id: str) -> None:
	"""读取 crawl 任务状态，不打开浏览器。"""
	try:
		with CacheStore(ctx.obj["data_dir"] / "cache" / "boss_agent.db") as cache:
			payload = crawl_status(cache, run_id)
	except KeyError:
		handle_error_output(ctx, "crawl.status", code="JOB_NOT_FOUND", message=f"未找到 crawl run: {run_id}")
		return
	except CacheRecordInvalid as exc:
		_report_corrupt_run(ctx, "crawl.status", run_id, exc)
		return
	hints: dict[str, list[str]] | None = None
	if payload["status"] == "risk_stopped":
		# 风险锁会先于浏览器构造阻断 resume：直接照 checkpoint.resume_command
		# 重试只会再次撞锁。解锁必须由用户确认官方页面恢复后显式执行。
		hints = {
			"operator_actions": [
				*UNLOCK_OPERATOR_ACTIONS,
				f"完成人工恢复后，显式执行 {payload['checkpoint']['resume_command']} 从原断点继续",
			],
		}
	handle_output(
		ctx,
		"crawl.status",
		payload,
		render=lambda d: render_record_result(
			d,
			title="crawl status",
			next_steps=[f"boss crawl results {run_id}"]
			if payload["status"] == "risk_stopped"
			else [f"boss crawl results {run_id}", f"boss crawl stop {run_id}"],
		),
		hints=hints,
	)


@crawl_group.command("stop")
@click.argument("run_id")
@click.pass_context
def stop_cmd(ctx: click.Context, run_id: str) -> None:
	"""请求运行中的 crawl 在下一安全点停止并保留 checkpoint。"""
	with CacheStore(ctx.obj["data_dir"] / "cache" / "boss_agent.db") as cache:
		if not cache.request_crawl_stop(run_id):
			handle_error_output(ctx, "crawl.stop", code="JOB_NOT_FOUND", message=f"未找到 crawl run: {run_id}")
			return
	handle_output(
		ctx,
		"crawl.stop",
		{"run_id": run_id, "status": "stop_requested"},
		render=lambda d: render_action_result(d, title="crawl stop", next_steps=[f"boss crawl status {run_id}"]),
	)


@crawl_group.command("results")
@click.argument("run_id")
@click.option("--page", type=click.IntRange(1), default=None, help="仅返回指定 crawl 页")
@click.option("--detail-status", type=click.Choice(("completed", "pending")), default=None, help="按详情完成状态筛选")
@click.pass_context
def results_cmd(ctx: click.Context, run_id: str, page: int | None, detail_status: str | None) -> None:
	"""读取 crawl 已采集结果，不打开浏览器。"""
	try:
		with CacheStore(ctx.obj["data_dir"] / "cache" / "boss_agent.db") as cache:
			payload = crawl_results(cache, run_id, page=page, detail_status=detail_status)
	except KeyError:
		handle_error_output(ctx, "crawl.results", code="JOB_NOT_FOUND", message=f"未找到 crawl run: {run_id}")
		return
	except CacheRecordInvalid as exc:
		_report_corrupt_run(ctx, "crawl.results", run_id, exc)
		return
	handle_output(
		ctx,
		"crawl.results",
		payload,
		render=lambda d: render_record_result(d, title="crawl results", next_steps=[f"boss crawl shortlist {run_id}"]),
	)


@crawl_group.command("shortlist")
@click.argument("run_id")
@click.option("--selector", "selectors", multiple=True, help="导入 results 返回的 selector，可重复传入")
@click.option("--all", "include_all", is_flag=True, default=False, help="导入该 run 的全部可关联职位")
@click.option("--tags", default="", help="写入候选池的本地标签，逗号分隔")
@click.option("--note", default="", help="写入候选池的本地备注")
@click.pass_context
def shortlist_cmd(
	ctx: click.Context,
	run_id: str,
	selectors: tuple[str, ...],
	include_all: bool,
	tags: str,
	note: str,
) -> None:
	"""将 crawl 结果导入原项目的本地职位候选池。"""
	if include_all == bool(selectors):
		handle_error_output(
			ctx,
			"crawl.shortlist",
			code="INVALID_PARAM",
			message="必须且只能使用 --all 或至少一个 --selector 选择要导入的职位",
		)
		return

	try:
		with CacheStore(ctx.obj["data_dir"] / "cache" / "boss_agent.db") as cache:
			payload = import_crawl_shortlist(
				cache,
				run_id,
				selectors=selectors,
				include_all=include_all,
				tags=tuple(tag.strip() for tag in tags.split(",") if tag.strip()),
				note=note,
			)
	except KeyError:
		handle_error_output(ctx, "crawl.shortlist", code="JOB_NOT_FOUND", message=f"未找到 crawl run: {run_id}")
		return
	except CacheRecordInvalid as exc:
		_report_corrupt_run(ctx, "crawl.shortlist", run_id, exc)
		return
	except ValueError as exc:
		handle_error_output(ctx, "crawl.shortlist", code="INVALID_PARAM", message=str(exc))
		return

	handle_output(
		ctx,
		"crawl.shortlist",
		payload,
		render=lambda d: render_record_result(d, title="crawl shortlist", next_steps=["boss shortlist list"]),
		hints={
			"next_actions": [
				"boss shortlist list",
				"boss ai fit --resume <简历名> --limit 20",
			],
		},
	)
