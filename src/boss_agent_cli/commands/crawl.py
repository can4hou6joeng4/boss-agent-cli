"""Explicit DrissionPage bulk crawl commands."""

from __future__ import annotations

import json
import math
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import click

from boss_agent_cli.api.cdp_risk_lock import unlock_operator_actions, raise_if_locked
from boss_agent_cli.api.egress import EgressContradictionError
from boss_agent_cli.api.endpoints import CITY_CODES
from boss_agent_cli.auth.manager import AuthManager, AuthRequired
from boss_agent_cli.cache.store import CacheRecordInvalid, CacheStore
from boss_agent_cli.compliance import operating_mode, require_compliance_allowed
from boss_agent_cli.config import DEFAULTS
from boss_agent_cli.crawler.operations import crawl_results, crawl_status, import_crawl_shortlist
from boss_agent_cli.crawler.service import (
	CrawlBusyError,
	CrawlService,
	CrawlSettings,
	crawl_cdp_port,
	crawl_chrome_path,
	crawl_int_setting,
	unused_local_port,
	validate_pages_override,
)
from boss_agent_cli.crawler.controls import (
	CrawlBrowserStartError,
	CrawlExportError,
	CrawlHookDirInvalid,
	CrawlProfileBusy,
	CrawlProfilePathInvalid,
	CrawlRuntimeUnavailable,
	CrawlSessionAuthError,
)
from boss_agent_cli.crawler.hooks import HookRegistrationError, verify_hook_profile
from boss_agent_cli.crawler.transport import DrissionCrawlerSession, ensure_crawl_runtime
from boss_agent_cli.services.install_extras import extras_install_hint, extras_requirement
from boss_agent_cli.services.json_files import read_bytes_if_regular, write_json_atomic
from boss_agent_cli.display import (
	auth_required_hints,
	boss_command_for_ctx,
	handle_auth_errors,
	handle_error_output,
	handle_output,
	login_action_for_ctx,
	registry_recovery_for_ctx,
	render_action_result,
	risk_lock_persistence_actions,
	shell_argument,
	render_record_result,
)

_HOOK_CHOICES = ("screenshot-full", "none")

def _crawl_payload_for_ctx(ctx: click.Context, payload: dict[str, Any]) -> dict[str, Any]:
	"""采集结果里的 ``error`` 文案定位到本次运行的数据目录（Issue #1097）。

	``CrawlRiskError`` 在 ``crawler/`` 层构造，拿不到 CLI 上下文，文案里嵌的是
	裸 ``boss clean --risk-lock``；在出口按 ``hints`` 的同一口径改写。
	"""
	error = payload.get("error")
	if not isinstance(error, str) or not error:
		return payload
	return {**payload, "error": registry_recovery_for_ctx(ctx, error)}


# 本地采集任务不存在时，Agent 能直接执行的下一步：新建任务、或用真实 run_id 核对。
# 这两步都读本地状态，必须带上当前数据目录，否则会在默认目录里找同名任务。
def _run_not_found_hints(ctx: click.Context) -> dict[str, list[str]]:
	return {"next_actions": [
		boss_command_for_ctx(ctx, "crawl start <query>", include_data_dir=True),
		boss_command_for_ctx(ctx, "crawl status <run_id>", include_data_dir=True),
	]}


def _require_run_id(ctx: click.Context, command: str, run_id: str) -> bool:
	"""空 run_id 是 argv 少传了值，不是「任务不存在」。

	``--data-dir`` 拼错、复制命令时漏掉最后一个词都会落到这里。原样查缓存会报
	``CRAWL_RUN_NOT_FOUND``「未找到 crawl run: 」（冒号后面什么都没有），Agent 会
	以为任务丢了而重建；``crawl resume ""`` 更先撞上登录校验，报 ``AUTH_REQUIRED``，
	把参数写错的人送去重新登录。
	"""
	if run_id.strip():
		return True
	handle_error_output(
		ctx,
		command,
		code="INVALID_PARAM",
		message="run_id 不能为空；先用 boss crawl status <run_id> 核对已有任务",
		recovery_action=f"补上已有的 run_id 后重试；没有任务时先用 {boss_command_for_ctx(ctx, 'crawl start <query>', include_data_dir=True)} 新建",
		hints=_run_not_found_hints(ctx),
	)
	return False


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
	# 路径状态读不出来时上抛，不能带着空配置整文件覆盖（Issue #1695）。
	raw = read_bytes_if_regular(config_path)
	if raw is not None:
		try:
			loaded = json.loads(raw)
			if isinstance(loaded, dict):
				user_cfg = loaded
		# 超长整数（3.11+）与深嵌套同样按配置损坏降级，不能挡住 crawl 配置写入。
		except (ValueError, RecursionError):
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
	if city.isascii() and city.isdigit():
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
	# 空关键词不是「不限关键词」，而是不筛选：采集会按城市默认列表逐页抓取，
	# 既消耗请求预算，也把任务暴露在更宽的风控面里；watch / preset 早已在写盘前拒绝。
	if not query.strip():
		raise ValueError("搜索关键词不能为空")
	cfg = _crawl_config(ctx)
	data_dir = Path(ctx.obj["data_dir"])
	# 断点里存的是绝对路径：`--data-dir` 允许相对路径（如 `state`），若把
	# `state/crawl/chrome-profile` 原样落盘，之后在不同 cwd 下 resume 会指向
	# 另一个目录，而 from_dict 的绝对路径校验也会把刚建的任务判成记录损坏。
	resolved_profile = str((data_dir / "crawl" / "chrome-profile").resolve())
	resolved_hook = hook_profile or "none"
	if resolved_hook not in _HOOK_CHOICES:
		raise ValueError(f"unknown hook profile: {resolved_hook}")
	# 显式空路径不是「未提供」：必须在使用配置回退或创建任务之前拒绝
	# （Issue #1560）。`--hook-dir` 只有与 screenshot-full 一起使用才会生效，
	# 单独给出时静默忽略同样会让成功信封与真实行为不符（Issue #1559）。
	if chrome_path is not None and not chrome_path.strip():
		raise ValueError("--chrome-path 不能为空；省略该选项才会使用 crawl 配置中的 chrome_path")
	if hook_dir is not None and not hook_dir.strip():
		raise ValueError("--hook-dir 不能为空；不需要页面 Hook 时省略该选项")
	if hook_dir is not None and resolved_hook != "screenshot-full":
		raise ValueError("--hook-dir 只与 --hook-profile screenshot-full 一起使用；当前 Hook 配置不会读取该目录")
	resolved_chrome = crawl_chrome_path(chrome_path if chrome_path is not None else cfg.get("chrome_path"))
	resolved_port = cdp_port
	if resolved_port is None:
		raw_port = cfg.get("cdp_port")
		resolved_port = unused_local_port() if raw_port is None else crawl_cdp_port(raw_port)
	# Path.expanduser 在 ~某用户 查不到家目录时抛 RuntimeError，原来一路兜成
	# 可重试的 NETWORK_ERROR「重试」：参数问题被说成网络问题，重试只会重复失败。
	resolved_hook_dir: Path | None = None
	if hook_dir:
		try:
			# expanduser 只展开 ~；断点里存的是绝对路径，换 cwd 后恢复仍要指向
			# 同一目录（Issue #1408）。
			resolved_hook_dir = Path(hook_dir).expanduser().resolve()
		except RuntimeError as exc:
			raise ValueError(
				f"无法展开 --hook-dir 的家目录写法：{hook_dir!r}（{exc}）；请改用绝对路径或 ~/ 开头的当前用户路径"
			) from exc
	# 目录与 profile 必须成对（Issue #1116）：缺 --hook-dir 时任务会先落盘，打开浏览器、
	# 导航到平台之后才在注入阶段抛错并被记成 status=stopped 的成功信封；resume 没有
	# --hook-dir 参数，重试只会重复同一错误。参数问题必须在创建任务之前拒绝。
	if resolved_hook == "screenshot-full" and resolved_hook_dir is None:
		raise ValueError("--hook-profile screenshot-full 需要同时提供 --hook-dir（用户授权的原始 Hook 脚本目录）")
	# 目录内容同样必须在创建任务与打开浏览器之前校验（Issue #1348）：缺清单、
	# 空目录或脚本被改时，运行期注入失败只会把任务记成 stopped 并提示 resume，
	# 而 resume 没有 --hook-dir 参数可以修复。这里只读本地文件，不触碰浏览器。
	verify_hook_profile(resolved_hook, resolved_hook_dir)
	return CrawlSettings(
		query=query,
		city_code=_city_code(city),
		pages=pages,
		with_detail=with_detail,
		profile_path=Path(resolved_profile),
		chrome_path=resolved_chrome,
		cdp_port=resolved_port,
		hook_profile=resolved_hook,
		hook_dir=resolved_hook_dir,
		max_requests=crawl_int_setting(cfg, "max_requests", 20),
		max_details=crawl_int_setting(cfg, "max_details", 50),
		max_seconds=crawl_int_setting(cfg, "max_seconds", 600),
		max_retries=crawl_int_setting(cfg, "max_retries", 1, minimum=0),
		operating_mode=operating_mode(ctx),
	)


class _LoginSeedUnavailable(RuntimeError):
	"""本地登录态不足以给采集浏览器播种；出口统一为 ``AUTH_REQUIRED``。"""


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
		raise _LoginSeedUnavailable(str(exc)) from exc
	raw = token.get("cookies") if isinstance(token, dict) else None
	if not isinstance(raw, dict) or not raw:
		raise _LoginSeedUnavailable("本地登录态缺少 Cookie，请先 boss login")
	cookies = {str(name): str(value) for name, value in raw.items() if name and value is not None}
	if ctx.obj.get("platform", "zhipin") == "zhipin" and "wt2" not in cookies:
		raise _LoginSeedUnavailable("登录态缺少 wt2，请先 boss login")
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


def _crawl_preflight(ctx: click.Context) -> tuple[dict[str, str], str]:
	"""创建任务或启动进程之前必须成立的本地前提：可选依赖已安装、登录态可注入。

	依赖先于登录态：没装 `crawl` 依赖时登录也无法采集，不应先把调用方送去登录。
	"""
	ensure_crawl_runtime()
	return _session_seed(ctx)


def _seeded_transport_factory(ctx: click.Context) -> Callable[[CrawlSettings], DrissionCrawlerSession]:
	"""读取本地登录态并返回注入它的传输工厂；登录态不足时抛 ``_LoginSeedUnavailable``。

	采集浏览器的配置目录与日常 Chrome 隔离：不注入就是登录页，采集会按未登录
	落成 ``risk_stopped`` 并写下风险锁。所有会打开浏览器的入口都从这里取工厂。
	"""
	seed_cookies, user_agent = _crawl_preflight(ctx)
	return lambda settings: _transport_factory(settings, seed_cookies=seed_cookies, user_agent=user_agent)


class _LazySeededTransportFactory:
	"""按需读取登录态的传输工厂：没有恢复工作的 run 不触发登录前置条件（Issue #1412）。

	``crawl resume`` 原先把登录 / transport 预检放在调用 service 之前，已完成
	的 run 也会先被要求登录。现在预检只在 ``CrawlService`` 确认 ``has_resume_work``
	之后（``preflight``）或真正构造运输层时执行；完成路径直接返回既有结果。
	"""

	def __init__(self, ctx: click.Context) -> None:
		self._ctx = ctx
		self._factory: Callable[[CrawlSettings], DrissionCrawlerSession] | None = None

	def _ensure(self) -> Callable[[CrawlSettings], DrissionCrawlerSession]:
		if self._factory is None:
			self._factory = _seeded_transport_factory(self._ctx)
		return self._factory

	def preflight(self) -> None:
		"""确认本地登录态可注入；失败抛 ``_LoginSeedUnavailable``。"""
		self._ensure()

	def __call__(self, settings: CrawlSettings) -> DrissionCrawlerSession:
		return self._ensure()(settings)


def _report_corrupt_run(ctx: click.Context, command: str, run_id: str | None, exc: CacheRecordInvalid) -> None:
	"""本地 run 记录写坏：断点读不出来，给「重新创建任务」指引而不是 traceback。

	``run_id`` 未知（损坏的是结果行而调用方不知道所属任务）时不给 status 指引：
	异常里的 ``record_id`` 可能是 job_key，拼进 `crawl status` 只会得到
	``CRAWL_RUN_NOT_FOUND``（Issue #1773）。
	"""
	next_actions = [boss_command_for_ctx(ctx, "crawl start <关键词>", include_data_dir=True)]
	if run_id is not None:
		next_actions.append(boss_command_for_ctx(ctx, f"crawl status {shell_argument(run_id)}", include_data_dir=True))
	handle_error_output(
		ctx,
		command,
		code="CACHE_RECORD_INVALID",
		message=str(exc),
		hints={"next_actions": next_actions},
	)


def _report_login_required(ctx: click.Context, command: str, message: str) -> None:
	handle_error_output(
		ctx,
		command,
		code="AUTH_REQUIRED",
		message=message,
		recoverable=True,
		recovery_action=login_action_for_ctx(ctx),
		hints=auth_required_hints(ctx),
	)


def _report_crawl_runtime_missing(ctx: click.Context, command: str, message: str) -> None:
	"""`crawl` 可选依赖缺失：恢复同一个任务只会复现，后继动作只有安装。"""
	configure_cmd = boss_command_for_ctx(ctx, "crawl configure", include_data_dir=True)
	handle_error_output(
		ctx,
		command,
		code="CRAWL_UNAVAILABLE",
		message=message,
		recoverable=True,
		recovery_action=f"安装 {extras_requirement('crawl')} 并执行 {configure_cmd}",
		hints={"operator_actions": [
			# uv tool install 会按给定 requirement 重建环境：已有的依赖组必须一并列出，
			# 否则补上 crawl 的同时会丢掉 mcp（Issue #1068）。
			"用安装本工具的同一方式补上 crawl 可选依赖："
			f"{extras_install_hint('crawl')}；"
			"源码仓库内为 uv sync --extra crawl",
			"安装完成后重新执行原命令；已创建的任务可按原 run_id 恢复",
		]},
	)


def _report_crawl_browser_start_failure(
	ctx: click.Context, command: str, exc: CrawlBrowserStartError
) -> None:
	"""采集浏览器没能启动或附着：没有发出请求，指引检查 Chrome / 端口 / 配置目录。

	它与「可选依赖未安装」共用 ``CRAWL_UNAVAILABLE``，但两者的恢复动作不同：
	只提示安装 extras 会让已装依赖、只是 Chrome 路径或端口有问题的用户反复重试
	（Issue #1553）。
	"""
	configure_cmd = boss_command_for_ctx(ctx, "crawl configure", include_data_dir=True)
	if exc.attached:
		recovery_action = (
			"上次保留的采集 Chrome 可能已关闭：直接重新执行恢复命令即可新起浏览器；"
			f"仍失败时用 {configure_cmd} 核对 chrome_path，并确认调试端口 {exc.cdp_port} 未被其他程序占用"
		)
	else:
		recovery_action = (
			f"确认本机已安装 Chrome 并用 {configure_cmd} 设置 chrome_path，"
			f"确认调试端口 {exc.cdp_port} 未被其他程序占用、数据目录可写后重试；"
			f"依赖缺失时安装 {extras_requirement('crawl')}"
		)
	handle_error_output(
		ctx,
		command,
		code="CRAWL_UNAVAILABLE",
		message=str(exc),
		recoverable=True,
		recovery_action=recovery_action,
	)


def _report_egress_contradiction(
	ctx: click.Context, command: str, exc: EgressContradictionError
) -> None:
	"""出口与声明的语言/时区矛盾：刻意的安全拒绝，不是依赖缺失或网络错误。"""
	handle_error_output(
		ctx,
		command,
		code="EGRESS_CONTRADICTION",
		message=str(exc),
		recoverable=False,
		recovery_action=(
			"保持客户端声明的语言/时区不变，换回与之一致的出口后重试；"
			"不需要出口一致性检查时撤销 BOSS_EGRESS_PROBE_URL。程序不会自动改写语言或时区"
		),
		details={"egress": exc.verdict.as_dict()},
	)


def _run_service(
	ctx: click.Context,
	service_call: Any,
	*,
	on_error: Callable[[str, str], None] | None = None,
	defer_transport_preflight: bool = False,
	run_id: str | None = None,
) -> None:
	def note(code: str, message: str) -> None:
		# 错误出口会直接结束进程：后台 worker 要在那之前留下原因（Issue #1042）。
		if on_error is not None:
			on_error(code, message)

	# 新建采集在导出前就已把 run 落库：导出失败时要把 run_id 交回调用方，
	# 否则只能重建任务、重复采集（Issue #1835）。
	created: dict[str, str] = {}

	def capture_created(inner_run_id: str) -> None:
		if isinstance(inner_run_id, str) and inner_run_id.strip():
			created.setdefault("run_id", inner_run_id.strip())

	try:
		raise_if_locked(Path(ctx.obj["data_dir"]))
		transports = _LazySeededTransportFactory(ctx)
		if not defer_transport_preflight:
			# 新建任务的路径必须在落库 / 打开浏览器之前确认登录态可注入；resume
			# 则交给 CrawlService 在确认还有恢复工作之后调用 preflight
			# （Issue #1412）。
			transports.preflight()
		with CacheStore(ctx.obj["data_dir"] / "cache" / "boss_agent.db") as cache:
			service = CrawlService(
				cache,
				data_dir=Path(ctx.obj["data_dir"]),
				transport_factory=transports,
				on_run_created=capture_created,
				preflight=transports.preflight,
			)
			outcome = service_call(service)
	except KeyError as exc:
		handle_error_output(ctx, "crawl", code="CRAWL_RUN_NOT_FOUND", message=f"未找到 crawl run: {exc.args[0]}", hints=_run_not_found_hints(ctx))
		return
	except CacheRecordInvalid as exc:
		# 结果行损坏时 record_id 是 job_key，不是 run_id（Issue #1773）。
		known_run_id = run_id if run_id is not None else (exc.record_id if exc.kind == "crawl_run" else None)
		_report_corrupt_run(ctx, "crawl", known_run_id, exc)
		return
	except CrawlBusyError as exc:
		note("CRAWL_BUSY", str(exc))
		# 同一个 run 只能有一个执行器：另一个进程正在抓页时，本次调用没有发出任何请求。
		# 照做 --resume 只会再撞一次，后继动作只有查看状态。
		status_command = boss_command_for_ctx(ctx, f"crawl status {shell_argument(exc.run_id)}", include_data_dir=True)
		handle_error_output(
			ctx,
			"crawl",
			code="CRAWL_BUSY",
			message=str(exc),
			details={"run_id": exc.run_id},
			recovery_action=f"用 {status_command} 查看进度，等当前执行结束后再恢复；不要并发恢复同一个 run_id",
			hints={"next_actions": [status_command]},
		)
		return
	except CrawlHookDirInvalid as exc:
		# 断点里的 Hook 目录坏了：resume 没有 --hook-dir 可改，crawl configure
		# 也修不了已存任务。按确定性本地失败收口（Issue #1460）。
		note("LOCAL_IO_ERROR", str(exc))
		hook_dir = f" {exc.hook_dir}" if exc.hook_dir is not None else ""
		handle_error_output(
			ctx,
			"crawl",
			code="LOCAL_IO_ERROR",
			message=str(exc),
			recoverable=False,
			recovery_action=(
				f"停止自动重试：核对 Hook 目录{hook_dir} 中的 7 个脚本与 SHA256SUMS，"
				"修复后重新执行 crawl resume；目录已迁移时用新的 --hook-dir 重新创建任务"
			),
		)
		return
	except ValueError as exc:
		note("INVALID_PARAM", str(exc))
		configure_cmd = boss_command_for_ctx(ctx, "crawl configure --help", include_data_dir=True)
		handle_error_output(
			ctx,
			"crawl",
			code="INVALID_PARAM",
			message=str(exc),
			recovery_action=f"按提示修正 crawl 配置后重试；可用 {configure_cmd} 查看可配置项",
			hints={"next_actions": [configure_cmd]},
		)
		return
	except CrawlExportError as exc:
		# 导出失败是本地目录 / 权限问题：CrawlExportError 是 RuntimeError 子类，
		# 不单独捕获会被兜成 CRAWL_UNAVAILABLE 并让人去安装 crawl 依赖（Issue #1238）。
		note("LOCAL_IO_ERROR", str(exc))
		known_run_id = run_id if run_id is not None else created.get("run_id")
		if known_run_id:
			# 采集结果已按 run 落库：给出可直接续跑的恢复命令，别让人重建任务
			# 重复采集（Issue #1835）。
			resume_command = boss_command_for_ctx(
				ctx, f"crawl resume {shell_argument(known_run_id)}", include_data_dir=True
			)
			status_command = boss_command_for_ctx(
				ctx, f"crawl status {shell_argument(known_run_id)}", include_data_dir=True
			)
			handle_error_output(
				ctx,
				"crawl",
				code="LOCAL_IO_ERROR",
				message=str(exc),
				recoverable=False,
				recovery_action=(
					f"本次采集记录已保存在 crawl run {known_run_id}：确认输出目录可写（不是同名的已有文件）、"
					f"目录权限与磁盘空间正常，然后执行 {resume_command} 从断点继续；进度可用 {status_command} 查看"
				),
				details={"run_id": known_run_id},
				hints={"next_actions": [status_command]},
			)
			return
		handle_error_output(
			ctx,
			"crawl",
			code="LOCAL_IO_ERROR",
			message=str(exc),
			recoverable=False,
			recovery_action="确认输出目录可写（不是同名的已有文件）、目录权限与磁盘空间正常，修正后重新执行 crawl resume",
		)
		return
	except CrawlProfileBusy as exc:
		# 同一数据目录的采集共用一个 Chrome 配置目录：另一个 run 的 Chrome 正在
		# 使用时，本次调用既没有发出请求，也不该删锁或起第二个实例（Issue #1266）。
		note("CRAWL_PROFILE_BUSY", str(exc))
		# `crawl status` 的 run_id 是必填参数：不带值的命令照抄后必然 INVALID_PARAM
		# （Issue #1289）。占位符与项目其他 hint 一致。
		status_command = boss_command_for_ctx(ctx, "crawl status <run_id>", include_data_dir=True)
		handle_error_output(
			ctx,
			"crawl",
			code="CRAWL_PROFILE_BUSY",
			message=str(exc),
			hints={"next_actions": [status_command]},
		)
		return
	except CrawlProfilePathInvalid as exc:
		# 配置目录被同名文件占用或不可写：确定性本地路径失败，重试或 resume 只会
		# 复现同一个错误，不能给 ok=true 的 stopped 与恢复指引（Issue #1290）。
		note("LOCAL_IO_ERROR", str(exc))
		handle_error_output(
			ctx,
			"crawl",
			code="LOCAL_IO_ERROR",
			message=str(exc),
			recoverable=False,
			recovery_action=(
				"检查数据目录下的 crawl/chrome-profile 是否被同名文件占用、"
				"目录权限与磁盘空间是否正常，修正后重新执行"
			),
		)
		return
	except CrawlSessionAuthError as exc:
		# 本地 Cookie 注入失败不是平台风控：不能写风险锁，也不能提示 clean
		# --risk-lock；按认证前置条件让用户重新登录后重试（Issue #1405）。
		note("AUTH_REQUIRED", str(exc))
		_report_login_required(ctx, "crawl", str(exc))
		return
	except HookRegistrationError as exc:
		# Hook 注入失败是确定性本地失败：resume 没有 --hook-dir 可以修复坏目录，
		# 给 ok:true 的 stopped 与 resume 指引只会无限重复（Issue #1408）。
		note("LOCAL_IO_ERROR", str(exc))
		hook_dir = f" {exc.hook_dir}" if exc.hook_dir is not None else ""
		handle_error_output(
			ctx,
			"crawl",
			code="LOCAL_IO_ERROR",
			message=str(exc),
			recoverable=False,
			recovery_action=(
				f"停止自动重试：核对 Hook 目录{hook_dir} 中的 7 个脚本与 SHA256SUMS，"
				"修复后重新执行 crawl resume；目录已迁移时用新的 --hook-dir 重新创建任务"
			),
		)
		return
	except EgressContradictionError as exc:
		# 出口与声明的语言/时区矛盾是刻意的安全拒绝：不是可选依赖缺失，也不是
		# 可重试的网络错误（Issue #1555）。
		note("EGRESS_CONTRADICTION", str(exc))
		_report_egress_contradiction(ctx, "crawl", exc)
		return
	except CrawlBrowserStartError as exc:
		# Chrome 未能启动：本次没有发出请求，但原样上抛会被 service 的通用分支
		# 记成 stopped 的 ok:true（Issue #1553）。
		note("CRAWL_UNAVAILABLE", str(exc))
		_report_crawl_browser_start_failure(ctx, "crawl", exc)
		return
	except RuntimeError as exc:
		message = str(exc)
		if isinstance(exc, _LoginSeedUnavailable) or "未登录" in message or "Cookie" in message or "wt2" in message:
			note("AUTH_REQUIRED", message)
			_report_login_required(ctx, "crawl", message)
			return
		note("CRAWL_UNAVAILABLE", message)
		if isinstance(exc, CrawlRuntimeUnavailable):
			_report_crawl_runtime_missing(ctx, "crawl", message)
			return
		configure_cmd = boss_command_for_ctx(ctx, "crawl configure", include_data_dir=True)
		handle_error_output(
			ctx,
			"crawl",
			code="CRAWL_UNAVAILABLE",
			message=message,
			recoverable=True,
			recovery_action=f"安装 {extras_requirement('crawl')} 并执行 {configure_cmd}",
		)
		return
	resume_command = boss_command_for_ctx(ctx, f"crawl resume {shell_argument(outcome.run_id)}", include_data_dir=True)
	status_command = boss_command_for_ctx(ctx, f"crawl status {shell_argument(outcome.run_id)}", include_data_dir=True)
	if outcome.status == "risk_stopped":
		hints = {"operator_actions": [
			*unlock_operator_actions(boss_command_for_ctx(ctx, "clean --risk-lock", include_data_dir=True)),
			f"完成人工恢复后，显式执行 {resume_command} 从原断点继续",
			# 风险锁没写盘时保护只在本进程内存里：不提示会让操作者以为新进程
			# 也被拦住（Issue #1281，文案与认证错误路径同一真源）。helper 接收
			# 异常 details 结构，锁本身包成 {"risk_lock": ...}。
			*risk_lock_persistence_actions({"risk_lock": outcome.risk_lock}),
		]}
	elif outcome.status == "budget_stopped":
		# 预算计数与 settings 都持久化，resume 不会推进（Issue #1238）：只给只读状态。
		hints = {
			"next_actions": [status_command],
			"operator_actions": [
				"该任务的预算已用完，继续采集需要新建任务（或调高 max_requests 后重新 crawl start）",
			],
		}
	elif outcome.status == "throttle_stopped":
		# 本地共享请求预算等待超时：run 自己的预算没有耗尽，也没有发出请求，
		# 等待后 resume 即可从断点继续（Issue #1556）。
		hints = {
			"next_actions": [resume_command],
			"operator_actions": [
				"本地请求节流等待超时（共享请求预算正被其他进程使用或平台冷却未结束），"
				"本次没有发出请求；等待片刻后执行 resume 从断点继续，不需要新建任务。"
				f"进度可用 {status_command} 查看",
			],
		}
	elif outcome.status == "workflow_stopped":
		hints = {
			"next_actions": [status_command],
			"operator_actions": [
				"任务已按停止请求结束，未继续采集；如需继续，显式执行 crawl resume（它会清除停止标记）",
			],
		}
	elif outcome.status != "completed":
		hints = {"next_actions": [resume_command]}
	else:
		hints = {
			# 采集跑完后 output_paths 只是落盘文件；Agent 要继续筛选得先按 run_id 读结果，
			# 空 next_actions 会让它只能自己猜命令（crawl status 的 completed 分支同此口径）。
			"next_actions": [boss_command_for_ctx(ctx, f"crawl results {shell_argument(outcome.run_id)}", include_data_dir=True)],
			"operator_actions": ["查看 output_paths 中的 JSON、CSV 或 XLSX 结果文件"],
		}
	handle_output(
		ctx,
		"crawl",
		_crawl_payload_for_ctx(ctx, outcome.as_dict()),
		render=lambda d: render_record_result(d, title="crawl", next_steps=hints.get("next_actions", [])),
		hints=hints,
	)


# worker 从被拉起到取得执行锁只需要解释器启动与一次本地读取；超过这段时间
# 仍是 queued 且锁空闲，说明它没有活下来（Issue #1045）。
_QUEUE_HANDOFF_GRACE_SECONDS = 30.0


def _tolerated_timestamp(value: Any) -> float:
	"""本地记录时间戳的容错读取：漂移值按 0 参与调度（与 Issue #1192 同口径）。

	`get_crawl_run` 故意不校验 `created_at` / `updated_at`，消费方必须自行降级；
	纯本地元数据问题不能拦停恢复，更不能被兜成可重试的 NETWORK_ERROR（Issue #1206）。
	`inf` / `nan` 同样按 0：`time.time() - inf` 是 `-inf`，小于交接窗口，会让
	queued 任务被永远当成「刚排队」，后台恢复返回成功却不启动 worker（Issue #1349）；
	超大整数在 `float()` 抛 `OverflowError`，也不能逃成 INTERNAL_ERROR。
	"""
	try:
		parsed = float(value or 0.0)
	except (TypeError, ValueError, OverflowError):
		return 0.0
	return parsed if math.isfinite(parsed) else 0.0


def _awaiting_queued_worker(run: dict[str, Any]) -> bool:
	"""任务刚排队：原 worker 可能正要取锁，父进程不探测、不抢占。"""
	if run["status"] != "queued":
		return False
	# 时钟回拨得到负值时按仍在窗口内处理，宁可多等也不抢；时间戳类型漂移
	# 无法判断新鲜度，按「排队已久」去探测锁，不能让父进程 traceback（Issue #1192）。
	updated = _tolerated_timestamp(run["updated_at"])
	return time.time() - updated < _QUEUE_HANDOFF_GRACE_SECONDS


def _prepare_background_resume(
	data_dir: Path,
	run_id: str,
	*,
	pages: int | None,
	with_detail: bool,
	clear_stop: bool,
	preflight: Callable[[], object],
) -> tuple[str, bool]:
	"""返回调度状态与是否需要启动 worker；启动前交还探测用的执行锁。"""
	with CacheStore(data_dir / "cache" / "boss_agent.db") as cache:
		service = CrawlService(cache, data_dir=data_dir, transport_factory=_transport_factory)
		run = cache.get_crawl_run(run_id)
		if run is None:
			raise KeyError(run_id)
		if not service.has_resume_work(run, pages=pages, with_detail=with_detail):
			# completed 快速路径同样要过设置校验：与前台 resume 同口径，损坏断点
			# 不能因为无需启动 worker 就被当成成功完成（Issue #2032）。后台信封
			# 只回报 run_id / status，hook_results 漂移不参与这里的结果。
			CrawlSettings.from_dict(run["params"], run_id=run_id)
			return "completed", False
		# 刚排队的任务不抢锁，以免让正准备启动的 worker 被拒绝；排队过久的
		# 与遗留的 running 一样靠执行锁判断是否还有执行器。
		if _awaiting_queued_worker(run):
			return "queued", False
		with service.execution_lock(run_id) as acquired:
			if not acquired:
				return "running", False
			# running 可能来自已退出的 worker（Issue #1038）：锁空闲才
			# 重读最新断点，不能把旧状态当作进程仍在执行的证据。
			run = cache.get_crawl_run(run_id)
			if run is None:
				raise KeyError(run_id)
			if not service.has_resume_work(run, pages=pages, with_detail=with_detail):
				return "completed", False
			if _awaiting_queued_worker(run):
				return "queued", False
			# 将要启动 worker 才校验持锁重读的设置；损坏断点在修改 stop 或
			# 探测依赖/登录态之前由父进程返回错误（Issue #1125）。
			CrawlSettings.from_dict(run["params"], run_id=run_id)
			# 与前台 resume 同口径：worker 会在同一个断点上再次校验，但父进程
			# 必须先把矛盾的 --pages 报出来，不能先返回 queued 再让 worker
			# 静默失败、任务停在无人执行的状态（Issue #2075）。
			if pages is not None:
				validate_pages_override(int(run["next_page"]), pages)
			# 旧 stop 必须在预检之前清掉：预检要探测可选依赖、读取登录态，可能
			# 耗时；这期间到达的新 stop 不能被后面的清理当成启动前残留一起清掉
			# （Issue #1115）。预检失败时还原旧 stop 与时间戳，保持「失败的调用
			# 不改动任务」的既有约定（Issue #1042）；失败且无旧 stop 时不写库。
			previous_stop = bool(run.get("stop_requested"))
			# 仅用于预检失败时还原；类型漂移按 0 降级，不能让恢复本身中断（Issue #1206）。
			previous_updated_at = _tolerated_timestamp(run.get("updated_at"))
			if clear_stop and previous_stop:
				cache.clear_crawl_stop_request(run_id)
			try:
				# worker 的错误信封写进空设备：依赖缺失与登录态不足要在这里由父进程报出来。
				preflight()
			except BaseException:
				if clear_stop and previous_stop:
					cache.restore_crawl_stop_request(run_id, updated_at=previous_updated_at)
				raise
			# 信封报 queued，库里也要是 queued（Issue #1771）：否则 worker 接手前
			# `crawl stop` 按旧终态被拒、重复的后台恢复会再拉起一个 worker。同时
			# 刷新时间给 worker 留足交接窗口（Issue #1045），不清新到达的 stop。
			cache.mark_crawl_run_active(run_id, "queued")
		return "queued", True


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


_UNSETTLED_STATUSES = ("queued", "running")


def _settle_unstarted_worker(data_dir: Path, run_id: str, detail: str) -> None:
	"""后台 worker 退出时任务仍显示排队或运行中：没有执行器了，把原因写回（Issue #1042）。

	worker 的信封写进空设备；不写回的话任务永远停在 ``queued``、没有错误，
	而后台恢复又把 ``queued`` 当作「原 worker 即将接手」，不会再启动。恢复遗留
	``running`` 的 worker 没能接手时同理。执行器写下的终态（含 ``stopped``）不改：
	那是上一次真实执行的结果。
	"""
	reason = (
		f"后台任务未能开始执行（{detail}）"
		if detail
		else "后台任务未能开始执行；在前台执行 crawl resume 可查看具体原因"
	)
	try:
		with CacheStore(data_dir / "cache" / "boss_agent.db") as cache:
			run = cache.get_crawl_run(run_id)
			if run is None or run["status"] not in _UNSETTLED_STATUSES:
				return
			service = CrawlService(cache, data_dir=data_dir, transport_factory=_transport_factory)
			with service.execution_lock(run_id) as acquired:
				if not acquired:
					# 另一个执行器已取得执行权，任务由它负责。
					return
				# 执行器全程持锁并在放锁前写终态：锁空闲而状态仍未结束，说明无人执行。
				run = cache.get_crawl_run(run_id)
				if run is None or run["status"] not in _UNSETTLED_STATUSES:
					return
				cache.update_crawl_run(
					run_id,
					status="stopped",
					next_page=int(run["next_page"]),
					error=reason,
					list_finished=bool(run["list_finished"]),
				)
	except Exception:
		# 写回失败不能盖住 worker 原本的退出原因。
		return


@crawl_group.command("configure")
@click.option("--chrome-path", default=None, type=click.Path(), help="chrome.exe 路径")
@click.option("--port", "cdp_port", default=None, type=click.IntRange(1, 65535), help="Chrome 远程调试端口")
@click.option("--max-requests", type=click.IntRange(1), default=None, help="每个 crawl run 的总请求上限")
@click.option("--max-details", type=click.IntRange(1), default=None, help="每个 crawl run 的详情请求上限")
@click.option("--max-seconds", type=click.IntRange(1), default=None, help="每个 crawl run 的墙钟秒数上限")
@click.option("--max-retries", type=click.IntRange(0), default=None, help="单请求的重试次数上限")
@click.pass_context
def configure_cmd(
	ctx: click.Context,
	chrome_path: str | None,
	cdp_port: int | None,
	max_requests: int | None,
	max_details: int | None,
	max_seconds: int | None,
	max_retries: int | None,
) -> None:
	"""设置 crawl 专用 Chrome 和 Hook 配置。"""
	if all(value is None for value in (chrome_path, cdp_port, max_requests, max_details, max_seconds, max_retries)):
		configure_help = boss_command_for_ctx(ctx, "crawl configure --help", include_data_dir=True)
		handle_error_output(
			ctx,
			"crawl.configure",
			code="INVALID_PARAM",
			message="至少提供一个 crawl 配置项",
			recovery_action=f"补上要调整的配置项后重试；可用 {configure_help} 查看可配置项",
			hints={"next_actions": [configure_help]},
		)
		return
	# 显式空路径不是「未提供」：原来的 click.Path(path_type=Path) 会把空串归一成
	# 当前目录，随后把 "." 写进 crawl.chrome_path，下一次采集才在浏览器启动时
	# 失败（Issue #1560）。必须在写盘之前拒绝。
	if chrome_path is not None and not chrome_path.strip():
		configure_help = boss_command_for_ctx(ctx, "crawl configure --help", include_data_dir=True)
		handle_error_output(
			ctx,
			"crawl.configure",
			code="INVALID_PARAM",
			message="--chrome-path 不能为空；省略该选项才读取现有配置",
			recovery_action=f"给出 Chrome 可执行文件的完整路径后重试；可用 {configure_help} 查看可配置项",
			hints={"next_actions": [configure_help]},
		)
		return
	updates = {
		"chrome_path": chrome_path,
		"cdp_port": cdp_port,
		"max_requests": max_requests,
		"max_details": max_details,
		"max_seconds": max_seconds,
		"max_retries": max_retries,
	}
	configured = _save_crawl_config(Path(ctx.obj["data_dir"]), updates)
	# 配置写在当前数据目录；后继的 crawl run 必须用同一目录，否则新预算与
	# Chrome 设置不会生效，断点与产物也落进默认目录。
	run_command = boss_command_for_ctx(ctx, "crawl run <关键词> --city <城市>", include_data_dir=True)
	handle_output(
		ctx,
		"crawl.configure",
		{"crawl": configured},
		render=lambda d: render_record_result(d, title="crawl configure", next_steps=[run_command]),
		hints={"next_actions": [run_command]},
	)


@crawl_group.command("run")
@click.argument("query")
@click.option("--city", required=True, help="城市名称或数字城市代码")
@click.option("--pages", default=5, type=click.IntRange(1), show_default=True, help="严格页数上限")
@click.option("--with-detail", is_flag=True, default=False, help="串行补全所有职位的 job_card")
@click.option("--hook-profile", type=click.Choice(_HOOK_CHOICES), default=None, help="页面 Hook 配置：none（默认，不注入脚本）/ screenshot-full（注入用户授权脚本，需同时给 --hook-dir）")
@click.option(
	"--hook-dir", type=click.Path(), default=None, help="与 screenshot-full 一起显式指定的用户脚本目录"
)
@click.option("--chrome-path", default=None, type=click.Path(), help="覆盖配置的 chrome.exe")
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
	hook_dir: str | None,
	chrome_path: str | None,
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
			hook_dir=hook_dir,
			profile_path=None,
			chrome_path=chrome_path,
			cdp_port=cdp_port,
		)
	except ValueError as exc:
		configure_cmd = boss_command_for_ctx(ctx, "crawl configure --help", include_data_dir=True)
		handle_error_output(
			ctx,
			"crawl",
			code="INVALID_PARAM",
			message=str(exc),
			recovery_action=f"按提示修正关键词、城市或 Hook 配置后重试；可用 {configure_cmd} 查看可配置项",
			hints={"next_actions": [configure_cmd]},
		)
		return
	_run_service(ctx, lambda service: service.create_and_run(settings))


@crawl_group.command("start")
@click.argument("query")
@click.option("--city", required=True, help="城市名称或数字城市代码")
@click.option("--pages", default=5, type=click.IntRange(1), show_default=True, help="严格页数上限")
@click.option("--with-detail", is_flag=True, default=False, help="串行补全所有职位的 job_card")
@click.option("--hook-profile", type=click.Choice(_HOOK_CHOICES), default=None, help="页面 Hook 配置：none（默认，不注入脚本）/ screenshot-full（注入用户授权脚本，需同时给 --hook-dir）")
@click.option(
	"--hook-dir", type=click.Path(), default=None, help="与 screenshot-full 一起显式指定的用户脚本目录"
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
	hook_dir: str | None,
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
			hook_dir=hook_dir,
			profile_path=None,
			chrome_path=None,
			cdp_port=None,
		)
		# worker 的错误信封写进空设备：依赖缺失与登录态不足必须在创建任务之前由
		# 父进程报出来，否则留下一个没人能执行的任务（Issue #1042、#1050）。
		_crawl_preflight(ctx)
		with CacheStore(ctx.obj["data_dir"] / "cache" / "boss_agent.db") as cache:
			service = CrawlService(cache, data_dir=Path(ctx.obj["data_dir"]), transport_factory=_transport_factory)
			run_id = service.create(settings)
		_launch_background_resume(Path(ctx.obj["data_dir"]), run_id)
	except _LoginSeedUnavailable as exc:
		_report_login_required(ctx, "crawl.start", str(exc))
		return
	except CrawlRuntimeUnavailable as exc:
		_report_crawl_runtime_missing(ctx, "crawl.start", str(exc))
		return
	except EgressContradictionError as exc:
		# create() 在落库前做出口一致性检查；原样上抛会落成 INTERNAL_ERROR
		# （Issue #1555）。
		_report_egress_contradiction(ctx, "crawl.start", exc)
		return
	except ValueError as exc:
		configure_cmd = boss_command_for_ctx(ctx, "crawl configure --help", include_data_dir=True)
		handle_error_output(
			ctx,
			"crawl.start",
			code="INVALID_PARAM",
			message=str(exc),
			recovery_action=f"按提示修正关键词、城市或 Hook 配置后重试；可用 {configure_cmd} 查看可配置项",
			hints={"next_actions": [configure_cmd]},
		)
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
		resume_command = boss_command_for_ctx(ctx, f"crawl resume {shell_argument(run_id)}", include_data_dir=True) if run_id is not None else None
		recovery_action = (
			f"检查磁盘空间与数据目录权限后执行 {resume_command} 重试"
			if resume_command is not None
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
	# 终端里看到的下一步必须与信封 hints 同口径：此前终端只提示 stop、
	# 信封只提示 results，真人不知道去哪里看结果，Agent 不知道可以中止。
	next_steps = [
		boss_command_for_ctx(ctx, f"crawl status {shell_argument(run_id)}", include_data_dir=True),
		boss_command_for_ctx(ctx, f"crawl results {shell_argument(run_id)}", include_data_dir=True),
		boss_command_for_ctx(ctx, f"crawl stop {shell_argument(run_id)}", include_data_dir=True),
	]
	resume_command = boss_command_for_ctx(ctx, f"crawl resume {shell_argument(run_id)}", include_data_dir=True)
	handle_output(
		ctx,
		"crawl.start",
		{
			"run_id": run_id,
			"status": "queued",
			"background": True,
			"checkpoint": {"resume_command": resume_command},
		},
		render=lambda d: render_record_result(d, title="crawl start", next_steps=next_steps),
		hints={"next_actions": next_steps},
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
	failures: list[tuple[str, str]] = []
	try:
		_resume(
			ctx, run_id, pages, with_detail, background, from_queue,
			on_error=lambda code, message: failures.append((code, message)),
		)
	except Exception as exc:
		# 由外层装饰器出信封的异常（风险锁等）只留稳定错误码，不留平台原文。
		failures.append((str(getattr(exc, "code", "") or type(exc).__name__), ""))
		raise
	finally:
		code, message = failures[-1] if failures else ("", "")
		# 被另一个执行器拒绝时任务归它负责：即使它随后放锁，也不把它的任务记成本次未接手。
		if from_queue and not background and code != "CRAWL_BUSY":
			_settle_unstarted_worker(
				Path(ctx.obj["data_dir"]), run_id, f"{code}: {message}" if message else code
			)


def _resume(
	ctx: click.Context,
	run_id: str,
	pages: int | None,
	with_detail: bool,
	background: bool,
	from_queue: bool,
	*,
	on_error: Callable[[str, str], None],
) -> None:
	if not _require_run_id(ctx, "crawl.resume", run_id):
		ctx.exit(1)
		return
	if not _require_crawl_capabilities(ctx):
		ctx.exit(1)
		return
	raise_if_locked(Path(ctx.obj["data_dir"]))
	# run 记录只存在本地缓存：ID 写错时要在要求登录之前报出来，否则调用方
	# 会先被送去 `boss login`，登录回来才发现任务根本不存在（与空 run_id 同类）。
	try:
		with CacheStore(ctx.obj["data_dir"] / "cache" / "boss_agent.db") as cache:
			run_exists = cache.get_crawl_run(run_id) is not None
	except CacheRecordInvalid as exc:
		_report_corrupt_run(ctx, "crawl.resume", run_id, exc)
		return
	if not run_exists:
		handle_error_output(
			ctx, "crawl.resume", code="CRAWL_RUN_NOT_FOUND", message=f"未找到 crawl run: {run_id}", hints=_run_not_found_hints(ctx)
		)
		return
	if background:
		try:
			status, should_launch = _prepare_background_resume(
				Path(ctx.obj["data_dir"]), run_id,
				pages=pages, with_detail=with_detail, clear_stop=not from_queue,
				preflight=lambda: _crawl_preflight(ctx),
			)
		except _LoginSeedUnavailable as exc:
			_report_login_required(ctx, "crawl.resume", str(exc))
			return
		except CrawlRuntimeUnavailable as exc:
			_report_crawl_runtime_missing(ctx, "crawl.resume", str(exc))
			return
		except KeyError:
			handle_error_output(ctx, "crawl.resume", code="CRAWL_RUN_NOT_FOUND", message=f"未找到 crawl run: {run_id}", hints=_run_not_found_hints(ctx))
			return
		except CacheRecordInvalid as exc:
			_report_corrupt_run(ctx, "crawl.resume", run_id, exc)
			return
		except ValueError as exc:
			# 后台恢复同样要像前台一样拒绝与断点进度矛盾的 --pages（Issue #2075）：
			# 先返回 queued 再让 worker 静默失败，会让任务停在无人执行的状态。
			status_command = boss_command_for_ctx(ctx, f"crawl status {shell_argument(run_id)}", include_data_dir=True)
			handle_error_output(
				ctx,
				"crawl.resume",
				code="INVALID_PARAM",
				message=str(exc),
				recovery_action="按提示把 --pages 调整到断点进度允许的下界后重试；不带 --pages 可沿用原任务上限",
				hints={"next_actions": [status_command]},
			)
			return
		if should_launch:
			# 先释放探测锁再启动，worker 会自行取得执行权并重读断点。
			try:
				_launch_background_resume(
					Path(ctx.obj["data_dir"]),
					run_id,
					pages=pages,
					with_detail=with_detail,
				)
			except OSError as exc:
				# 状态已落成 queued：worker 没能拉起时把原因写回，不留无人执行的排队。
				_settle_unstarted_worker(Path(ctx.obj["data_dir"]), run_id, f"无法启动后台任务: {exc}")
				resume_command = boss_command_for_ctx(ctx, f"crawl resume {shell_argument(run_id)}", include_data_dir=True)
				handle_error_output(
					ctx,
					"crawl.resume",
					code="CRAWL_UNAVAILABLE",
					message=f"无法启动 crawl 后台任务: {exc}",
					recoverable=True,
					recovery_action=f"检查磁盘空间与数据目录权限后执行 {resume_command} 重试",
				)
				return
		if status == "completed":
			# 拉起一个没有工作的后台进程只会让它立即退出，却让调用方以为任务重新排队。
			results_command = boss_command_for_ctx(ctx, f"crawl results {shell_argument(run_id)}", include_data_dir=True)
			handle_output(
				ctx,
				"crawl.resume",
				{"run_id": run_id, "status": "completed", "background": True},
				render=lambda d: render_action_result(d, title="crawl resume", next_steps=[results_command]),
				hints={"next_actions": [results_command]},
			)
			return
		status_command = boss_command_for_ctx(ctx, f"crawl status {shell_argument(run_id)}", include_data_dir=True)
		handle_output(
			ctx,
			"crawl.resume",
			{"run_id": run_id, "status": status, "background": True},
			render=lambda d: render_action_result(d, title="crawl resume", next_steps=[status_command]),
			hints={"next_actions": [status_command]},
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
		on_error=on_error,
		run_id=run_id,
		# 登录 / transport 预检延后到 CrawlService 确认 has_resume_work 之后：
		# 已完成且无待补详情的 run 直接返回既有结果（Issue #1412）。
		defer_transport_preflight=True,
	)


@crawl_group.command("status")
@click.argument("run_id")
@click.pass_context
def status_cmd(ctx: click.Context, run_id: str) -> None:
	"""读取 crawl 任务状态，不打开浏览器。"""
	if not _require_run_id(ctx, "crawl.status", run_id):
		return
	try:
		with CacheStore(ctx.obj["data_dir"] / "cache" / "boss_agent.db") as cache:
			payload = crawl_status(cache, run_id, data_dir=ctx.obj["data_dir"])
			run = cache.get_crawl_run(run_id)
			queue_overdue = run is not None and run["status"] == "queued" and not _awaiting_queued_worker(run)
	except KeyError:
		handle_error_output(ctx, "crawl.status", code="CRAWL_RUN_NOT_FOUND", message=f"未找到 crawl run: {run_id}", hints=_run_not_found_hints(ctx))
		return
	except CacheRecordInvalid as exc:
		_report_corrupt_run(ctx, "crawl.status", run_id, exc)
		return
	hints: dict[str, list[str]] | None = None
	# TTY 的下一步与信封 hints 必须同一口径：只有还在跑的 run 才值得提示 stop，
	# 已结束的 run 上 `crawl stop` 仍会返回 stop_requested，但什么都不会停。
	next_steps = [boss_command_for_ctx(ctx, f"crawl results {shell_argument(run_id)}", include_data_dir=True)]
	if payload["status"] in ("queued", "running"):
		next_steps.append(boss_command_for_ctx(ctx, f"crawl stop {shell_argument(run_id)}", include_data_dir=True))
	if queue_overdue:
		# 排队过久多半是后台进程没活下来：只轮询 status 永远等不到结果。后台恢复
		# 会先探测执行锁，仍有执行器时只回报 running，不会重复启动（Issue #1045）。
		next_steps.append(
			boss_command_for_ctx(ctx, f"crawl resume {shell_argument(run_id)} --background", include_data_dir=True)
		)
	if payload["status"] == "risk_stopped":
		# 风险锁会先于浏览器构造阻断 resume：直接照 checkpoint.resume_command
		# 重试只会再次撞锁。解锁必须由用户确认官方页面恢复后显式执行。
		hints = {
			"operator_actions": [
				*unlock_operator_actions(boss_command_for_ctx(ctx, "clean --risk-lock", include_data_dir=True)),
				f"完成人工恢复后，显式执行 {payload['checkpoint']['resume_command']} 从原断点继续",
			],
		}
	else:
		hints = {"next_actions": next_steps}
	handle_output(
		ctx,
		"crawl.status",
		_crawl_payload_for_ctx(ctx, payload),
		render=lambda d: render_record_result(
			d,
			title="crawl status",
			next_steps=next_steps,
		),
		hints=hints,
	)


@crawl_group.command("stop")
@click.argument("run_id")
@click.pass_context
def stop_cmd(ctx: click.Context, run_id: str) -> None:
	"""请求运行中的 crawl 在下一安全点停止并保留 checkpoint。"""
	if not _require_run_id(ctx, "crawl.stop", run_id):
		return
	try:
		with CacheStore(ctx.obj["data_dir"] / "cache" / "boss_agent.db") as cache:
			run = cache.get_crawl_run(run_id)
			if run is None:
				handle_error_output(ctx, "crawl.stop", code="CRAWL_RUN_NOT_FOUND", message=f"未找到 crawl run: {run_id}", hints=_run_not_found_hints(ctx))
				return
			# 终态 run 上停止请求不会生效（Issue #1574）：继续报 stop_requested 会让
			# 调用方以为任务已被叫停。只有 queued / running 接受停止请求。
			status = run["status"]
			if status not in ("queued", "running"):
				status_command = boss_command_for_ctx(ctx, f"crawl status {shell_argument(run_id)}", include_data_dir=True)
				results_command = boss_command_for_ctx(ctx, f"crawl results {shell_argument(run_id)}", include_data_dir=True)
				handle_error_output(
					ctx,
					"crawl.stop",
					code="CRAWL_RUN_FINISHED",
					message=f"crawl 任务已结束（{status}），停止请求没有生效",
					recovery_action=f"用 {status_command} 查看最终状态，或 {results_command} 读取已采集结果；需要重新采集时新建 crawl 任务",
					hints={"next_actions": [status_command, results_command]},
				)
				return
			cache.request_crawl_stop(run_id)
	except CacheRecordInvalid as exc:
		_report_corrupt_run(ctx, "crawl.stop", run_id, exc)
		return
	status_command = boss_command_for_ctx(ctx, f"crawl status {shell_argument(run_id)}", include_data_dir=True)
	handle_output(
		ctx,
		"crawl.stop",
		{"run_id": run_id, "status": "stop_requested"},
		render=lambda d: render_action_result(d, title="crawl stop", next_steps=[status_command]),
		hints={"next_actions": [status_command]},
	)


@crawl_group.command("results")
@click.argument("run_id")
@click.option("--page", type=click.IntRange(1), default=None, help="仅返回指定 crawl 页")
@click.option("--detail-status", type=click.Choice(("completed", "pending")), default=None, help="按详情完成状态筛选")
@click.pass_context
def results_cmd(ctx: click.Context, run_id: str, page: int | None, detail_status: str | None) -> None:
	"""读取 crawl 已采集结果，不打开浏览器。"""
	if not _require_run_id(ctx, "crawl.results", run_id):
		return
	try:
		with CacheStore(ctx.obj["data_dir"] / "cache" / "boss_agent.db") as cache:
			payload = crawl_results(cache, run_id, page=page, detail_status=detail_status)
	except KeyError:
		handle_error_output(ctx, "crawl.results", code="CRAWL_RUN_NOT_FOUND", message=f"未找到 crawl run: {run_id}", hints=_run_not_found_hints(ctx))
		return
	except CacheRecordInvalid as exc:
		_report_corrupt_run(ctx, "crawl.results", run_id, exc)
		return
	# 后继导入必须明确选择范围：缺少 --selector / --all 会被 shortlist 拒绝。
	# 使用本次实际返回的 selector，保留页码和详情状态筛选的结果范围。
	argv = ["crawl"]
	if payload["jobs"]:
		argv.extend(["shortlist", run_id])
		for job in payload["jobs"]:
			argv.extend(["--selector", str(job["selector"])])
	else:
		argv.extend(["status", run_id])
	next_steps = [boss_command_for_ctx(ctx, " ".join(shell_argument(part) for part in argv), include_data_dir=True)]
	handle_output(
		ctx,
		"crawl.results",
		payload,
		render=lambda d: render_record_result(d, title="crawl results", next_steps=next_steps),
		hints={"next_actions": next_steps},
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
		results_cmd = boss_command_for_ctx(ctx, f"crawl results {shell_argument(run_id)}", include_data_dir=True)
		handle_error_output(
			ctx,
			"crawl.shortlist",
			code="INVALID_PARAM",
			message="必须且只能使用 --all 或至少一个 --selector 选择要导入的职位",
			recovery_action=f"用 --all 导入该 run 的全部结果，或改用至少一个 --selector 指定要导入的职位；候选 selector 可用 {results_cmd} 查看",
			hints={"next_actions": [results_cmd]},
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
		handle_error_output(ctx, "crawl.shortlist", code="CRAWL_RUN_NOT_FOUND", message=f"未找到 crawl run: {run_id}", hints=_run_not_found_hints(ctx))
		return
	except CacheRecordInvalid as exc:
		_report_corrupt_run(ctx, "crawl.shortlist", run_id, exc)
		return
	except ValueError as exc:
		results_cmd = boss_command_for_ctx(ctx, f"crawl results {shell_argument(run_id)}", include_data_dir=True)
		handle_error_output(
			ctx,
			"crawl.shortlist",
			code="INVALID_PARAM",
			message=str(exc),
			recovery_action=f"用 {results_cmd} 核对可用 selector 后重试，或改用 --all 导入该 run 的全部结果",
			hints={"next_actions": [results_cmd]},
		)
		return

	list_command = boss_command_for_ctx(ctx, "shortlist list", include_data_dir=True)
	handle_output(
		ctx,
		"crawl.shortlist",
		payload,
		render=lambda d: render_record_result(d, title="crawl shortlist", next_steps=[list_command]),
		hints={
			"next_actions": [
				list_command,
				boss_command_for_ctx(ctx, "ai fit --resume <简历名> --limit 20", include_data_dir=True),
			],
		},
	)
