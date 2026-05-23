import click

from boss_agent_cli.auth.health import assess_auth_health
from boss_agent_cli.auth.manager import AuthManager
from boss_agent_cli.commands._platform import get_platform_instance
from boss_agent_cli.display import handle_auth_errors, handle_error_output, handle_output, login_action_for_ctx, render_status


@click.command("status")
@click.option("--live", is_flag=True, default=False, help="执行一次只读在线验证（默认仅检查本地登录态）")
@click.pass_context
@handle_auth_errors("status")
def status_cmd(ctx: click.Context, live: bool) -> None:
	"""检查当前登录态"""
	data_dir = ctx.obj["data_dir"]
	logger = ctx.obj["logger"]
	platform_name = ctx.obj.get("platform", "zhipin")
	auth = AuthManager(data_dir, logger=logger, platform=platform_name)

	token = auth.check_status()
	auth_health = assess_auth_health(data_dir, platform=platform_name, token=token)
	if token is None:
		login_action = login_action_for_ctx(ctx)
		handle_error_output(
			ctx, "status",
			code="AUTH_REQUIRED",
			message=f"未登录，请先执行 {login_action}",
			recoverable=True, recovery_action=login_action,
			hints={"auth_health": auth_health.public_summary(), "checks": auth_health.checks_as_dicts()},
		)
		return

	data = {
		"logged_in": True,
		"live": live,
		"user_name": None,
		"token_expires_in": None,
		"auth_state": auth_health.auth_state,
		"auth_summary": auth_health.summary,
		"auth_health": auth_health.public_summary(),
		"checks": auth_health.checks_as_dicts(),
	}

	if not live:
		handle_output(
			ctx,
			"status",
			data,
			render=lambda payload: render_status(payload, login_action=login_action_for_ctx(ctx)),
			hints={
				"next_actions": _status_next_actions(auth_health.auth_state, platform_name=platform_name),
				"live_probe": "运行 boss status --live 执行一次只读在线验证",
			},
		)
		return

	with get_platform_instance(ctx, auth) as platform:
		info = platform.user_info()
		if not platform.is_success(info):
			code, message = platform.parse_error(info)
			handle_error_output(
				ctx,
				"status",
				code=code,
				message=message or "用户信息获取失败",
				recoverable=False,
				hints={"auth_health": auth_health.public_summary(), "checks": auth_health.checks_as_dicts()},
			)
			return
		user_info = platform.unwrap_data(info) or {}
		user_name = user_info.get("name", "未知用户")
		data["user_name"] = user_name
		handle_output(
			ctx,
			"status",
			data,
			render=lambda payload: render_status(payload, login_action=login_action_for_ctx(ctx)),
		)


def _status_next_actions(auth_state: str, *, platform_name: str) -> list[str]:
	login_action = "boss --platform zhilian login" if platform_name == "zhilian" else "boss login"
	if auth_state == "complete":
		return ["boss status --live — 可选执行一次只读在线验证"]
	if auth_state == "partial":
		return ["以 Chrome 远程调试端口启动浏览器后运行 boss login --cdp", "boss status --live — 验证部分登录态是否仍可读"]
	if auth_state == "broken":
		return [f"boss logout && {login_action} — 重建登录态"]
	return [f"{login_action} — 建立登录态"]
