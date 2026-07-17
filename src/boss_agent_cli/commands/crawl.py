"""Explicit DrissionPage bulk crawl commands."""

from __future__ import annotations

import json
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any

import click

from boss_agent_cli.api.endpoints import CITY_CODES
from boss_agent_cli.cache.store import CacheStore
from boss_agent_cli.compliance import operating_mode, require_compliance_allowed
from boss_agent_cli.config import DEFAULTS
from boss_agent_cli.crawler.operations import crawl_results, crawl_status, import_crawl_shortlist
from boss_agent_cli.crawler.service import CrawlService, CrawlSettings
from boss_agent_cli.crawler.transport import DrissionCrawlerSession
from boss_agent_cli.display import handle_error_output, handle_output

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
		except (OSError, json.JSONDecodeError):
			pass
	crawl_cfg = user_cfg.get("crawl", {})
	if not isinstance(crawl_cfg, dict):
		crawl_cfg = {}
	crawl_cfg.update({key: value for key, value in updates.items() if value is not None})
	user_cfg["crawl"] = crawl_cfg
	config_path.parent.mkdir(parents=True, exist_ok=True)
	config_path.write_text(json.dumps(user_cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
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
	return CrawlSettings(
		query=query,
		city_code=_city_code(city),
		pages=pages,
		with_detail=with_detail,
		profile_path=Path(resolved_profile),
		chrome_path=chrome_path or cfg.get("chrome_path"),
		cdp_port=int(cdp_port or cfg.get("cdp_port") or _unused_local_port()),
		hook_profile=resolved_hook,
		hook_dir=Path(hook_dir).expanduser() if hook_dir else None,
		max_requests=int(cfg.get("max_requests") or 20),
		max_details=int(cfg.get("max_details") or 50),
		max_seconds=int(cfg.get("max_seconds") or 600),
		max_retries=int(cfg.get("max_retries") or 1),
		operating_mode=operating_mode(ctx),
	)


def _unused_local_port() -> int:
	with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as connection:
		connection.bind(("127.0.0.1", 0))
		return int(connection.getsockname()[1])


def _transport_factory(settings: CrawlSettings) -> DrissionCrawlerSession:
	return DrissionCrawlerSession(
		profile_path=settings.profile_path,
		chrome_path=settings.chrome_path,
		cdp_port=settings.cdp_port,
		hook_profile=settings.hook_profile,
		hook_dir=settings.hook_dir,
	)


def _run_service(ctx: click.Context, service_call: Any) -> None:
	try:
		with CacheStore(ctx.obj["data_dir"] / "cache" / "boss_agent.db") as cache:
			service = CrawlService(cache, data_dir=Path(ctx.obj["data_dir"]), transport_factory=_transport_factory)
			outcome = service_call(service)
	except KeyError as exc:
		handle_error_output(ctx, "crawl", code="JOB_NOT_FOUND", message=f"未找到 crawl run: {exc.args[0]}")
		return
	except ValueError as exc:
		handle_error_output(ctx, "crawl", code="INVALID_PARAM", message=str(exc))
		return
	except RuntimeError as exc:
		handle_error_output(
			ctx, "crawl", code="CRAWL_UNAVAILABLE", message=str(exc), recoverable=True,
			recovery_action="安装 boss-agent-cli[crawl] 并执行 boss crawl configure",
		)
		return
	hints = {"next_actions": [f"boss crawl resume {outcome.run_id}"]} if outcome.status != "completed" else {
		"next_actions": ["查看 output_paths 中的 JSON、CSV 或 XLSX 结果文件"]
	}
	handle_output(ctx, "crawl", outcome.as_dict(), hints=hints)


def _launch_background_resume(
	data_dir: Path,
	run_id: str,
	*,
	pages: int | None = None,
	with_detail: bool = False,
) -> None:
	"""Start the already-persisted task outside the MCP request process."""
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
	handle_output(ctx, "crawl.configure", {"crawl": configured})


@crawl_group.command("run")
@click.argument("query")
@click.option("--city", required=True, help="城市名称或数字城市代码")
@click.option("--pages", default=5, type=click.IntRange(1), show_default=True, help="严格页数上限")
@click.option("--with-detail", is_flag=True, default=False, help="串行补全所有职位的 job_card")
@click.option("--hook-profile", type=click.Choice(_HOOK_CHOICES), default=None)
@click.option("--hook-dir", type=click.Path(path_type=Path), default=None, help="与 screenshot-full 一起显式指定的用户脚本目录")
@click.option("--chrome-path", default=None, type=click.Path(path_type=Path), help="覆盖配置的 chrome.exe")
@click.option("--port", "cdp_port", default=None, type=click.IntRange(1, 65535), help="覆盖配置的调试端口")
@click.pass_context
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
	try:
		settings = _settings_from_context(
			ctx, query=query, city=city, pages=pages, with_detail=with_detail, hook_profile=hook_profile,
			hook_dir=str(hook_dir) if hook_dir else None, profile_path=None,
			chrome_path=str(chrome_path) if chrome_path else None, cdp_port=cdp_port,
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
@click.option("--hook-dir", type=click.Path(path_type=Path), default=None, help="与 screenshot-full 一起显式指定的用户脚本目录")
@click.pass_context
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
	run_id: str | None = None
	try:
		settings = _settings_from_context(
			ctx, query=query, city=city, pages=pages, with_detail=with_detail, hook_profile=hook_profile,
			hook_dir=str(hook_dir) if hook_dir else None, profile_path=None, chrome_path=None, cdp_port=None,
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
				run = cache.get_crawl_run(run_id)
				if run is not None:
					cache.update_crawl_run(
						run_id,
						status="stopped",
						next_page=int(run["next_page"]),
						error=f"无法启动后台任务: {exc}",
						list_finished=bool(run["list_finished"]),
					)
		handle_error_output(
			ctx,
			"crawl.start",
			code="CRAWL_UNAVAILABLE",
			message=f"无法启动 crawl 后台任务: {exc}",
			recoverable=True,
			recovery_action="设置 operating_mode=research 后执行 boss crawl resume <run_id> 重试",
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
		hints={"next_actions": [f"boss crawl status {run_id}", f"boss crawl results {run_id}"]},
	)


@crawl_group.command("resume")
@click.argument("run_id")
@click.option("--pages", default=None, type=click.IntRange(1), help="覆盖原任务页数上限")
@click.option("--with-detail", is_flag=True, default=False, help="补全已采职位和后续职位的 job_card")
@click.option("--background", is_flag=True, default=False, help="后台恢复，立即返回 run_id")
@click.option("--from-queue", is_flag=True, default=False, hidden=True)
@click.pass_context
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
	if background:
		try:
			with CacheStore(ctx.obj["data_dir"] / "cache" / "boss_agent.db") as cache:
				run = cache.get_crawl_run(run_id)
				if run is None:
					raise KeyError(run_id)
				status = str(run["status"])
			should_launch = status not in {"queued", "running"}
			if should_launch:
				_launch_background_resume(
					Path(ctx.obj["data_dir"]),
					run_id,
					pages=pages,
					with_detail=with_detail,
				)
		except KeyError:
			handle_error_output(ctx, "crawl.resume", code="JOB_NOT_FOUND", message=f"未找到 crawl run: {run_id}")
			return
		except OSError as exc:
			handle_error_output(
				ctx,
				"crawl.resume",
				code="CRAWL_UNAVAILABLE",
				message=f"无法启动 crawl 后台任务: {exc}",
				recoverable=True,
				recovery_action=f"执行 boss crawl resume {run_id} 重试",
			)
			return
		handle_output(
			ctx,
			"crawl.resume",
			{"run_id": run_id, "status": status if status in {"queued", "running"} else "queued", "background": True},
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
	handle_output(ctx, "crawl.status", payload)


@crawl_group.command("stop")
@click.argument("run_id")
@click.pass_context
def stop_cmd(ctx: click.Context, run_id: str) -> None:
	"""请求运行中的 crawl 在下一安全点停止并保留 checkpoint。"""
	with CacheStore(ctx.obj["data_dir"] / "cache" / "boss_agent.db") as cache:
		if not cache.request_crawl_stop(run_id):
			handle_error_output(ctx, "crawl.stop", code="JOB_NOT_FOUND", message=f"未找到 crawl run: {run_id}")
			return
	handle_output(ctx, "crawl.stop", {"run_id": run_id, "status": "stop_requested"})


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
	handle_output(ctx, "crawl.results", payload)


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
	except ValueError as exc:
		handle_error_output(ctx, "crawl.shortlist", code="INVALID_PARAM", message=str(exc))
		return

	handle_output(
		ctx,
		"crawl.shortlist",
		payload,
		hints={
			"next_actions": [
				"boss shortlist list",
				"boss ai fit --resume <简历名> --limit 20",
			],
		},
	)
