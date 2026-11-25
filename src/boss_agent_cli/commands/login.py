import os
import stat
import sys
from typing import TextIO

import click

from boss_agent_cli.api.client import PlatformRateLimitError, PlatformRiskError
from boss_agent_cli.api.proxy import ProxyConfigurationError
from boss_agent_cli.auth.manager import MAX_LOGIN_TIMEOUT_SECONDS, AuthManager
from boss_agent_cli.commands._login_errors import classify_login_error
from boss_agent_cli.display import (
	INTERNAL_ERROR_TYPES,
	boss_command_for_ctx,
	error_contract_for_code,
	handle_error_output,
	handle_output,
	render_action_result,
)
from boss_agent_cli.output import is_local_io_error

#: ``--curl-file`` 的读取上限（字符数）：粘贴的 cURL 文本远小于此值；``/dev/zero``、
#: FIFO 或不结束的 stdin 不能把登录命令拖成无界读取（Issue #2310）。
_MAX_CURL_FILE_CHARS = 1024 * 1024


def _read_curl_command(curl_file: TextIO) -> str:
	"""有界读取 ``--curl-file``：只接受普通文件或标准输入。"""
	is_stdin = curl_file is sys.stdin or getattr(curl_file, "name", None) == "<stdin>"
	if not is_stdin:
		name = getattr(curl_file, "name", None)
		if not isinstance(name, str):
			raise ValueError("--curl-file 必须是普通文件或标准输入（-）")
		try:
			mode = os.stat(name).st_mode
		except OSError as exc:
			raise ValueError("--curl-file 必须是普通文件或标准输入（-）") from exc
		if not stat.S_ISREG(mode):
			raise ValueError("--curl-file 必须是普通文件或标准输入（-）")
	text = curl_file.read(_MAX_CURL_FILE_CHARS + 1)
	if not isinstance(text, str):
		raise ValueError("--curl-file 内容不是文本")
	if len(text) > _MAX_CURL_FILE_CHARS:
		raise ValueError(f"--curl-file 内容超过 {_MAX_CURL_FILE_CHARS // 1024} KiB 上限")
	return text


def _reject_conflicting_options(
	ctx: click.Context, *, cookie_source: str | None, cdp: bool, force: bool, curl_file: TextIO | None,
) -> None:
	"""选项互斥是用法错误，必须在使用任何登录链路前判定。

	原来这两条检查在 ``try`` 里抛 ``ValueError``，而 ``except`` 对带 ``--curl-file``
	的调用统一回「cURL 登录态导入失败、可重试」：真正要改的 ``--cdp`` / ``--force``
	被藏起来，Agent 还会拿同一组选项重试。
	"""
	login_path = boss_command_for_ctx(ctx, "login")
	curl_conflicts = [
		name for name, enabled in (
			("--cookie-source", cookie_source is not None),
			("--cdp", cdp),
			("--force", force),
		) if enabled
	]
	if curl_file is not None and curl_conflicts:
		joined = "、".join(curl_conflicts)
		handle_error_output(
			ctx,
			"login",
			code="INVALID_PARAM",
			message=f"--curl-file 不能与 {joined} 同时使用",
			recoverable=False,
			recovery_action=f"去掉 {joined} 后重试；用 {login_path} --help 查看可用组合",
			hints={"next_actions": [f"{login_path} --help"]},
		)
	if force and cookie_source is not None:
		handle_error_output(
			ctx,
			"login",
			code="INVALID_PARAM",
			message="--force 会跳过本地 Cookie 提取，不能与 --cookie-source 同时使用",
			recoverable=False,
			recovery_action=f"去掉 --cookie-source 或 --force 后重试；用 {login_path} --help 查看可用组合",
			hints={"next_actions": [f"{login_path} --help"]},
		)


@click.command("login")
@click.option(
	"--timeout",
	default=120,
	type=click.IntRange(min=1, max=MAX_LOGIN_TIMEOUT_SECONDS),
	help=f"扫码登录超时时间（1-{MAX_LOGIN_TIMEOUT_SECONDS} 秒）",
)
@click.option("--cookie-source", default=None, help="指定浏览器提取 Cookie（如 chrome/firefox/edge），不指定则自动检测")
@click.option("--cdp", is_flag=True, default=False, help="强制 CDP 模式（跳过 Cookie 提取，CDP 不可用直接报错）")
@click.option("--force", is_flag=True, default=False, help="不复用任何既有登录态：跳过本地 Cookie 提取，CDP 下不复用已登录 context 并清掉目标平台 cookie 后重新登录")
@click.option("--ignore-risk-lock", is_flag=True, default=False, help="历史兼容参数，不再跳过 BOSS 本地风控锁；恢复请由用户执行 clean --risk-lock")
@click.option("--curl-file", type=click.File("r", encoding="utf-8"), default=None, help="从 Copy as cURL (bash) 文件导入 BOSS 登录态；- 表示标准输入")
@click.pass_context
def login_cmd(
	ctx: click.Context, timeout: int, cookie_source: str | None, cdp: bool, force: bool,
	ignore_risk_lock: bool, curl_file: TextIO | None,
) -> None:
	"""登录当前招聘平台（按平台走对应的 Cookie / CDP / 浏览器降级链路）"""
	data_dir = ctx.obj["data_dir"]
	logger = ctx.obj["logger"]
	cdp_url = ctx.obj.get("cdp_url")
	platform_name = ctx.obj.get("platform") or "zhipin"

	_reject_conflicting_options(ctx, cookie_source=cookie_source, cdp=cdp, force=force, curl_file=curl_file)
	try:
		# 构造也放进 try：--data-dir 不可写等本地 IO 失败要走 LOCAL_IO_ERROR，
		# 而不是逃出命令层被兜成 INTERNAL_ERROR（Issue #2311）。
		auth = AuthManager(data_dir, logger=logger, platform=platform_name)
		if curl_file is not None:
			token = auth.import_curl(_read_curl_command(curl_file))
		else:
			token = auth.login(
				timeout=timeout,
				cookie_source=cookie_source,
				cdp_url=cdp_url,
				force_cdp=cdp,
				force_relogin=force,
				ignore_risk_lock=ignore_risk_lock,
			)
		method = token.pop("_method", "未知")
		status_cmd = boss_command_for_ctx(ctx, "status", include_data_dir=True)
		search_cmd = boss_command_for_ctx(ctx, "search <query>", include_data_dir=True)
		recommend_cmd = boss_command_for_ctx(ctx, "recommend", include_data_dir=True)
		next_actions = [status_cmd, search_cmd, recommend_cmd]
		handle_output(
			ctx,
			"login",
			{"message": f"登录成功（{method}）"},
			render=lambda data: render_action_result(data, title="login", next_steps=next_actions),
			hints={"next_actions": next_actions},
		)
	except Exception as e:
		if isinstance(e, PlatformRiskError):
			handle_error_output(ctx, "login", **classify_login_error(e, ctx))
		elif isinstance(e, (PlatformRateLimitError, ProxyConfigurationError)):
			# 限流 / 代理配置错误带专用契约；curl 分支不能把它们兜成通用的
			# 「导入失败、可重试」（Issue #2308）。
			handle_error_output(ctx, "login", **classify_login_error(e, ctx))
		elif isinstance(e, INTERNAL_ERROR_TYPES):
			# 确定性内部错误不能落到文本启发式被报成可重试网络故障。登录输入
			# 可能夹带凭据，message 只放类型名，不回显异常文本（Issue #2307）。
			recoverable, recovery_action = error_contract_for_code("INTERNAL_ERROR")
			handle_error_output(
				ctx,
				"login",
				code="INTERNAL_ERROR",
				message=f"登录流程内部错误: {type(e).__name__}",
				recoverable=recoverable,
				recovery_action=recovery_action,
			)
		elif is_local_io_error(e):
			# 本地目录 / 磁盘错误不是网络问题，也不该被 curl 分支的通用文案吞掉。
			handle_error_output(ctx, "login", **classify_login_error(e, ctx))
		elif curl_file is not None:
			operator_actions = [
				"仅支持 BOSS 直聘 Copy as cURL (bash) 原始文本，不能与 --cdp 或 --cookie-source 混用",
				"检查文件格式、网络和网页登录状态；导入失败不会自动打开浏览器或重放请求",
			]
			if isinstance(e, ValueError):
				# 解析器 / 有界读取的 ValueError 文案是固定文本，不含输入内容；回显
				# 它能定位 BOM、大小上限等具体原因（Issue #2310）。非 ValueError
				# （网络库 / 未知异常）仍可能与凭据有关，完全透传原文被禁止。
				handle_error_output(
					ctx,
					"login",
					code="INVALID_PARAM",
					message=f"cURL 登录态导入失败: {e}",
					# 与登记表及相邻参数分支一致：修正输入不可重试（Issue #1624）。
					recoverable=False,
					recovery_action="核对 BOSS cURL 原始文本、选项及平台登录状态后重新导入",
					hints={"operator_actions": operator_actions},
				)
			else:
				handle_error_output(
					ctx,
					"login",
					code="LOGIN_CREDENTIAL_EXTRACTION_FAILED",
					message="cURL 登录态导入失败，未完成会话更新",
					recoverable=True,
					recovery_action="核对 BOSS cURL 原始文本、选项及平台登录状态后重新导入",
					hints={"operator_actions": operator_actions},
				)
		else:
			handle_error_output(ctx, "login", **classify_login_error(e, ctx))
