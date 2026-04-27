import click

from boss_agent_cli.auth.manager import AuthManager
from boss_agent_cli.commands._platform import get_platform_instance
from boss_agent_cli.display import handle_auth_errors, handle_error_output, handle_output, login_action_for_ctx, render_status


@click.command("status")
@click.pass_context
@handle_auth_errors("status")
def status_cmd(ctx: click.Context) -> None:
	"""检查当前登录态"""
	data_dir = ctx.obj["data_dir"]
	logger = ctx.obj["logger"]
	auth = AuthManager(data_dir, logger=logger, platform=ctx.obj.get("platform", "zhipin"))

	token = auth.check_status()
	if token is None:
		login_action = login_action_for_ctx(ctx)
		handle_error_output(
			ctx, "status",
			code="AUTH_REQUIRED",
			message=f"未登录，请先执行 {login_action}",
			recoverable=True, recovery_action=login_action,
		)
		return

	with get_platform_instance(ctx, auth) as platform:
		info = platform.user_info()
		user_info = platform.unwrap_data(info) or {}
		user_name = user_info.get("name", "未知用户")
		data = {
			"logged_in": True,
			"user_name": user_name,
			"token_expires_in": None,
		}
		handle_output(
			ctx,
			"status",
			data,
			render=lambda payload: render_status(payload, login_action=login_action_for_ctx(ctx)),
		)
