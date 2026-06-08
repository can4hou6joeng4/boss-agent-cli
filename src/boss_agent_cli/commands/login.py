import click

from boss_agent_cli.auth.manager import AuthManager
from boss_agent_cli.display import boss_command_for_ctx, login_action_for_ctx
from boss_agent_cli.output import emit_error, emit_success


def _classify_login_error(exc: Exception, ctx: click.Context) -> dict[str, object]:
	"""Return a user-facing, redacted login error envelope payload.

	The login flow intentionally remains unchanged; this helper only turns broad
	Cookie/CDP/QR/browser failures into actionable CLI diagnostics.
	"""
	raw_message = str(exc) or exc.__class__.__name__
	message = raw_message.lower()
	recovery_action = login_action_for_ctx(ctx)

	def payload(code: str, user_message: str, next_actions: list[str]) -> dict[str, object]:
		return {
			"code": code,
			"message": user_message,
			"recoverable": True,
			"recovery_action": recovery_action,
			"hints": {"next_actions": next_actions},
		}

	if isinstance(exc, TimeoutError) or "timeout" in message or "超时" in raw_message:
		return payload(
			"LOGIN_TIMEOUT",
			f"登录等待超时: {raw_message}",
			[
				"确认二维码已完成扫码并在网页端授权登录",
				"网络较慢时可追加 --timeout 180 或更长时间",
				"如已打开本机 Chrome，可先运行 boss-chrome 后再重试登录",
			],
		)

	if "cdp" in message or "chrome" in message or isinstance(exc, ConnectionError):
		return payload(
			"CDP_UNAVAILABLE",
			f"Chrome 调试连接不可用: {raw_message}",
			[
				"运行 boss-chrome 启动带调试端口的 Chrome",
				"或去掉 --cdp，让命令自动尝试 Cookie / QR 登录降级链路",
				"确认 --cdp-url 指向可访问的 Chrome DevTools 地址",
			],
		)

	if any(term in message for term in ("403", "forbidden", "风控", "risk", "rate limit", "too many")):
		return payload(
			"LOGIN_RISK_CONTROL",
			f"登录请求可能触发平台风控: {raw_message}",
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
			[
				"重新执行登录并完成网页端授权",
				"如使用 Cookie 提取，确认浏览器内目标平台仍处于登录状态",
				"登录后运行 status 验证本地登录态",
			],
		)

	if any(term in message for term in ("cookie", "stoken", "token", "凭证")):
		return payload(
			"LOGIN_CREDENTIAL_EXTRACTION_FAILED",
			f"登录成功后提取凭证失败: {raw_message}",
			[
				"确认浏览器已完成登录并进入平台首页",
				"若 Cookie 提取失败，可指定 --cookie-source chrome/firefox/edge",
				"若仍失败，可运行 boss-chrome 后使用 --cdp 重试",
			],
		)

	return payload(
		"NETWORK_ERROR",
		f"登录失败: {raw_message}",
		[
			"检查网络连通性后重试",
			"如浏览器内已登录，可尝试指定 --cookie-source chrome/firefox/edge",
			"若问题持续，请运行 boss doctor 并附带诊断输出反馈",
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
		emit_success("login", {"message": f"登录成功（{method}）"}, hints={
			"next_actions": [
				f"{status_cmd} — 验证登录态",
				f"{search_cmd} — 搜索职位",
				f"{recommend_cmd} — 获取个性化推荐",
			],
		})
	except Exception as e:
		emit_error("login", **_classify_login_error(e, ctx))
