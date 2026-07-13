import click

from boss_agent_cli.auth.manager import AuthManager
from boss_agent_cli.commands._platform import get_platform_instance
from boss_agent_cli.display import boss_command_for_ctx, handle_auth_errors, handle_error_output, handle_not_supported, handle_output, handle_platform_error_output, login_action_for_ctx, render_simple_list
from typing import Any


@click.command("interviews")
@click.pass_context
@handle_auth_errors("interviews")
def interviews_cmd(ctx: click.Context) -> None:
	"""查看面试邀请列表"""
	data_dir = ctx.obj["data_dir"]
	logger = ctx.obj["logger"]
	auth = AuthManager(data_dir, logger=logger, platform=ctx.obj.get("platform", "zhipin"))

	token = auth.check_status()
	if token is None:
		login_action = login_action_for_ctx(ctx)
		handle_error_output(
			ctx, "interviews",
			code="AUTH_REQUIRED",
			message=f"未登录，请先执行 {login_action}",
			recoverable=True, recovery_action=login_action,
		)
		return

	with get_platform_instance(ctx, auth) as platform:
		try:
			raw = platform.interview_data()
		except NotImplementedError as exc:
			handle_not_supported(ctx, "interviews", exc, fallback_message="当前平台不支持面试邀请能力")
			return
		if not platform.is_success(raw):
			handle_platform_error_output(ctx, "interviews", platform, raw, fallback_message="面试邀请获取失败")
			return
		platform_data = platform.unwrap_data(raw) or {}
		interview_list = platform_data.get("interviewList", [])

	items = [
		{
			"jobName": it.get("jobName", "-"),
			"brandName": it.get("brandName", "-"),
			"interviewTime": it.get("interviewTime", "-"),
			"address": it.get("address", "-"),
			"statusDesc": it.get("statusDesc", "-"),
		}
		for it in interview_list
	]

	def _render(data: list[dict[str, Any]]) -> None:
		render_simple_list(
			data,
			"interviews",
			columns=[
				("job", "jobName", "bold cyan"),
				("company", "brandName", "green"),
				("time", "interviewTime", "yellow"),
				("address", "address", ""),
				("status", "statusDesc", "blue"),
			],
		)

	hints: dict[str, Any] = {"next_actions": [boss_command_for_ctx(ctx, "search <query>")]}

	handle_output(
		ctx, "interviews", items,
		render=_render,
		hints=hints,
	)
