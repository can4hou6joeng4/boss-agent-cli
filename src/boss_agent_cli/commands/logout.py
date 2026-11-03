import click

from boss_agent_cli.display import error_contract_for_code, login_action_for_ctx
from boss_agent_cli.auth.manager import AuthManager
from boss_agent_cli.output import emit_error, emit_success


@click.command("logout")
@click.pass_context
def logout_cmd(ctx: click.Context) -> None:
	"""退出登录，清除本地保存的登录态"""
	data_dir = ctx.obj["data_dir"]
	logger = ctx.obj["logger"]
	auth = AuthManager(data_dir, logger=logger, platform=ctx.obj.get("platform", "zhipin"))
	try:
		auth.logout()
		emit_success("logout", {"message": "已退出登录"}, hints={
			"next_actions": [
				login_action_for_ctx(ctx),
			],
		})
	except Exception as e:
		# logout 只清除本地登录态，失败不是网络问题；按登记表给出可执行的恢复动作。
		recoverable, recovery_action = error_contract_for_code("LOGOUT_FAILED")
		emit_error(
			"logout",
			code="LOGOUT_FAILED",
			message=f"退出登录失败: {e}",
			recoverable=recoverable,
			recovery_action=recovery_action,
		)
