import click

from boss_agent_cli.api.client import BossClient
from boss_agent_cli.auth.manager import AuthManager
from boss_agent_cli.display import handle_auth_errors, handle_error_output, handle_output, render_status


@click.command("status")
@click.pass_context
@handle_auth_errors("status")
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

	with BossClient(auth, delay=delay, cdp_url=cdp_url) as client:
		info = client.user_info()
		user_name = info.get("zpData", {}).get("name", "未知用户")
		data = {
			"logged_in": True,
			"user_name": user_name,
			"token_expires_in": None,
		}
		handle_output(ctx, "status", data, render=render_status)
