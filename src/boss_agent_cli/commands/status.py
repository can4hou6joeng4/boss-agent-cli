import click

from boss_agent_cli.auth.manager import AuthManager
from boss_agent_cli.commands._platform import get_platform_instance
from boss_agent_cli.display import handle_auth_errors, handle_error_output, handle_output, render_status


@click.command("status")
@click.pass_context
@handle_auth_errors("status")
def status_cmd(ctx: click.Context) -> None:
	"""检查当前登录态"""
	data_dir = ctx.obj["data_dir"]
	logger = ctx.obj["logger"]
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

	with get_platform_instance(ctx, auth) as platform:
		info = platform.user_info()
		user_name = info.get("zpData", {}).get("name", "未知用户")
		data = {
			"logged_in": True,
			"user_name": user_name,
			"token_expires_in": None,
		}
		handle_output(ctx, "status", data, render=render_status)
