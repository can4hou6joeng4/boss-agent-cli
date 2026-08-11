import click

from boss_agent_cli.auth.manager import AuthManager
from boss_agent_cli.display import boss_command_for_ctx, login_action_for_ctx
from boss_agent_cli.output import emit_error, emit_success


def _classify_login_error(exc: Exception, ctx: click.Context) -> dict[str, object]:
	"""Return a user-facing, redacted login error envelope payload.

	The login flow intentionally remains unchanged; this helper only turns broad
	Cookie/CDP/QR/browser failures into actionable CLI diagnostics.

	hints 分两条受众通道（见 schema conventions.hints）：
	  next_actions     — 面向 Agent 的可执行命令
	  operator_actions — 面向真人操作者的自然语言指引（多半要离开终端完成）
	"""
	raw_message = str(exc) or exc.__class__.__name__
	message = raw_message.lower()
	recovery_action = login_action_for_ctx(ctx)
	login_cmd = login_action_for_ctx(ctx)
	status_cmd = boss_command_for_ctx(ctx, "status")
	doctor_cmd = boss_command_for_ctx(ctx, "doctor")

	def payload(
		code: str,
		user_message: str,
		next_actions: list[str],
		operator_actions: list[str],
		recovery: str | None = None,
	) -> dict[str, object]:
		return {
			"code": code,
			"message": user_message,
			"recoverable": True,
			"recovery_action": recovery or recovery_action,
			"hints": {
				"next_actions": next_actions,
				"operator_actions": operator_actions,
			},
		}

	if isinstance(exc, TimeoutError) or "timeout" in message or "超时" in raw_message:
		return payload(
			"LOGIN_TIMEOUT",
			f"登录等待超时: {raw_message}",
			[f"{login_cmd} --timeout 180", "boss-chrome"],
			[
				"确认二维码已完成扫码并在网页端授权登录",
				"网络较慢时可延长超时时间后重试",
				"如已打开本机 Chrome，可先启动带调试端口的 Chrome 再重试登录",
			],
		)

	if "executable doesn't exist" in message or "playwright was just installed" in message:
		return payload(
			"BROWSER_KERNEL_MISSING",
			f"patchright 浏览器内核缺失或与所需修订版不匹配: {raw_message}",
			["patchright install chromium", doctor_cmd, login_cmd],
			["浏览器内核安装完成后重新执行登录"],
			recovery="patchright install chromium",
		)

	if "cdp" in message or "chrome" in message or isinstance(exc, ConnectionError):
		return payload(
			"CDP_UNAVAILABLE",
			f"Chrome 调试连接不可用: {raw_message}",
			["boss-chrome", login_cmd],
			[
				"启动带调试端口的 Chrome 后重试，或去掉 --cdp 让命令自动降级到 Cookie / 扫码链路",
				"确认 --cdp-url 指向可访问的 Chrome DevTools 地址",
			],
		)

	if any(term in message for term in ("403", "forbidden", "风控", "risk", "rate limit", "too many")):
		return payload(
			"LOGIN_RISK_CONTROL",
			f"登录请求可能触发平台风控: {raw_message}",
			[],
			[
				"暂停自动化重试，改用浏览器手动确认账号状态",
				"降低请求频率，避免短时间重复登录或刷新",
				"必要时联系平台客服确认账号是否受限",
			],
		)

	if any(term in message for term in ("401", "unauthorized", "expired", "过期", "未登录")):
		return payload(
			"LOGIN_EXPIRED",
			f"登录态已失效或授权不足: {raw_message}",
			[login_cmd, status_cmd],
			[
				"重新登录并在网页端完成授权",
				"如使用 Cookie 提取，确认浏览器内目标平台仍处于登录状态",
			],
		)

	if any(term in message for term in ("cookie", "stoken", "token", "凭证")):
		return payload(
			"LOGIN_CREDENTIAL_EXTRACTION_FAILED",
			f"登录成功后提取凭证失败: {raw_message}",
			[f"{login_cmd} --cookie-source chrome", "boss-chrome"],
			[
				"确认浏览器已完成登录并进入平台首页",
				"若 Cookie 提取失败，可改用 --cookie-source 指定 chrome/firefox/edge",
			],
		)

	return payload(
		"NETWORK_ERROR",
		f"登录失败: {raw_message}",
		[doctor_cmd, login_cmd],
		[
			"检查网络连通性后重试",
			"如浏览器内已登录，可尝试用 --cookie-source 指定 chrome/firefox/edge",
			"若问题持续，请附带 doctor 诊断输出反馈",
		],
	)


@click.command("login")
@click.option("--timeout", default=120, help="扫码登录超时时间（秒）")
@click.option("--cookie-source", default=None, help="指定浏览器提取 Cookie（如 chrome/firefox/edge），不指定则自动检测")
@click.option("--cdp", is_flag=True, default=False, help="强制 CDP 模式（跳过 Cookie 提取，CDP 不可用直接报错）")
@click.pass_context
def login_cmd(ctx: click.Context, timeout: int, cookie_source: str | None, cdp: bool) -> None:
	"""登录当前招聘平台（按平台走对应的 Cookie / CDP / 浏览器降级链路）"""
	data_dir = ctx.obj["data_dir"]
	logger = ctx.obj["logger"]
	cdp_url = ctx.obj.get("cdp_url")
	platform_name = ctx.obj.get("platform") or "zhipin"

	auth = AuthManager(data_dir, logger=logger, platform=platform_name)
	try:
		token = auth.login(
			timeout=timeout,
			cookie_source=cookie_source,
			cdp_url=cdp_url,
			force_cdp=cdp,
		)
		method = token.pop("_method", "未知")
		status_cmd = boss_command_for_ctx(ctx, "status")
		search_cmd = boss_command_for_ctx(ctx, "search <query>")
		recommend_cmd = boss_command_for_ctx(ctx, "recommend")
		emit_success(
			"login",
			{"message": f"登录成功（{method}）"},
			hints={
				"next_actions": [
					f"{status_cmd} — 验证登录态",
					f"{search_cmd} — 搜索职位",
					f"{recommend_cmd} — 获取个性化推荐",
				],
			},
		)
	except Exception as e:
		emit_error("login", **_classify_login_error(e, ctx))
