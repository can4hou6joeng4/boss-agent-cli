import click

from boss_agent_cli.auth.manager import AuthManager
from boss_agent_cli.output import emit_error, emit_success


@click.command("login")
@click.option("--timeout", default=120, help="扫码登录超时时间（秒）")
@click.option("--cookie-source", default=None, help="指定浏览器提取 Cookie（如 chrome/firefox/edge），不指定则自动检测")
@click.option("--cdp", is_flag=True, default=False, help="强制 CDP 模式（跳过 Cookie 提取，CDP 不可用直接报错）")
@click.pass_context
def login_cmd(ctx: click.Context, timeout: int, cookie_source: str | None, cdp: bool) -> None:
	"""登录 BOSS 直聘（三级降级：Cookie 提取 → CDP 自动探测 → patchright 扫码）"""
	data_dir = ctx.obj["data_dir"]
	logger = ctx.obj["logger"]
	cdp_url = ctx.obj.get("cdp_url")

	auth = AuthManager(data_dir, logger=logger)
	try:
		token = auth.login(
			timeout=timeout,
			cookie_source=cookie_source,
			cdp_url=cdp_url,
			force_cdp=cdp,
		)
		method = token.pop("_method", "未知")
		emit_success("login", {"message": f"登录成功（{method}）"}, hints={
			"next_actions": [
				"boss status — 验证登录态",
				"boss search <query> — 搜索职位",
				"boss recommend — 获取个性化推荐",
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
			recovery_action="boss login",
		)
	except Exception as e:
		emit_error(
			"login",
			code="NETWORK_ERROR",
			message=f"登录失败: {e}",
			recoverable=True,
			recovery_action="boss login",
		)
