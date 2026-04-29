import click

from boss_agent_cli.auth.manager import AuthManager
from boss_agent_cli.commands._platform import get_platform_instance
from boss_agent_cli.display import boss_command_for_ctx, error_contract_for_code, handle_auth_errors, handle_error_output, handle_output, login_action_for_ctx, render_simple_list
from typing import Any

NOT_SUPPORTED_RECOVERY_ACTION = "切换平台或调整命令参数后重试"


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
			handle_error_output(
				ctx, "interviews",
				code="NOT_SUPPORTED",
				message=str(exc) or "当前平台不支持面试邀请能力",
				recoverable=True,
				recovery_action=NOT_SUPPORTED_RECOVERY_ACTION,
			)
			return
		if not platform.is_success(raw):
			code, message = platform.parse_error(raw)
			recoverable, recovery_action = error_contract_for_code(code)
			handle_error_output(
				ctx, "interviews",
				code=code,
				message=message or "面试邀请获取失败",
				recoverable=recoverable,
				recovery_action=recovery_action,
			)
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

	handle_output(
		ctx, "interviews", items,
		render=_render,
		hints={"next_actions": [boss_command_for_ctx(ctx, "search <query>")]},
	)
