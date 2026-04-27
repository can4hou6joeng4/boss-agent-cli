import click

from boss_agent_cli.auth.manager import AuthManager
from boss_agent_cli.display import boss_command_for_ctx, login_action_for_ctx
from boss_agent_cli.output import emit_error, emit_success


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
	except ConnectionError as e:
		emit_error(
			"login",
			code="NETWORK_ERROR",
			message=str(e),
			recoverable=True,
			recovery_action="boss-chrome 启动 Chrome 后重试",
		)
	except TimeoutError as e:
		emit_error(
			"login",
			code="NETWORK_ERROR",
			message=str(e),
			recoverable=True,
			recovery_action=login_action_for_ctx(ctx),
		)
	except Exception as e:
		emit_error(
			"login",
			code="NETWORK_ERROR",
			message=f"登录失败: {e}",
			recoverable=True,
			recovery_action=login_action_for_ctx(ctx),
		)
