import click

from boss_agent_cli.api.client import BossClient
from boss_agent_cli.auth.manager import AuthManager, AuthRequired, TokenRefreshFailed
from boss_agent_cli.display import handle_error_output, handle_output, render_status


@click.command("status")
@click.pass_context
def status_cmd(ctx):
	"""检查当前登录态"""
	data_dir = ctx.obj["data_dir"]
	logger = ctx.obj["logger"]
	delay = ctx.obj["delay"]
	cdp_url = ctx.obj.get("cdp_url")
	auth = AuthManager(data_dir, logger=logger)

	token = auth.check_status()
	if token is None:
		handle_error_output(
			ctx, "status",
			code="AUTH_REQUIRED",
			message="未登录，请先执行 boss login",
			recoverable=True, recovery_action="boss login",
		)
		return

	try:
		with BossClient(auth, delay=delay, cdp_url=cdp_url) as client:
			info = client.user_info()
			user_name = info.get("zpData", {}).get("name", "未知用户")
			data = {
				"logged_in": True,
				"user_name": user_name,
				"token_expires_in": None,
			}
			handle_output(ctx, "status", data, render=render_status)
	except AuthRequired:
		handle_error_output(
			ctx, "status",
			code="AUTH_REQUIRED",
			message="登录态已失效，请重新登录",
			recoverable=True, recovery_action="boss login",
		)
	except TokenRefreshFailed:
		handle_error_output(
			ctx, "status",
			code="TOKEN_REFRESH_FAILED",
			message="Token 刷新失败，请重新登录",
			recoverable=True, recovery_action="boss login",
		)
	except Exception as e:
		handle_error_output(
			ctx, "status",
			code="NETWORK_ERROR",
			message=f"验证登录态失败: {e}",
			recoverable=True, recovery_action="重试",
		)
