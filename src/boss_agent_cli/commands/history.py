import click

from boss_agent_cli.api.models import JobItem
from boss_agent_cli.auth.manager import AuthManager
from boss_agent_cli.commands._platform import get_platform_instance
from boss_agent_cli.display import boss_command_for_ctx, error_contract_for_code, handle_auth_errors, handle_error_output, handle_output, render_job_table

NOT_SUPPORTED_RECOVERY_ACTION = "切换平台或调整命令参数后重试"


@click.command("history")
@click.option("--page", default=1, help="页码")
@click.pass_context
@handle_auth_errors("history")
def history_cmd(ctx: click.Context, page: int) -> None:
	"""查看最近浏览过的职位"""
	data_dir = ctx.obj["data_dir"]
	logger = ctx.obj["logger"]

	auth = AuthManager(data_dir, logger=logger, platform=ctx.obj.get("platform", "zhipin"))
	with get_platform_instance(ctx, auth) as platform:
		try:
			raw = platform.job_history(page)
		except NotImplementedError as exc:
			handle_error_output(
				ctx, "history",
				code="NOT_SUPPORTED",
				message=str(exc) or "当前平台不支持浏览历史能力",
				recoverable=True,
				recovery_action=NOT_SUPPORTED_RECOVERY_ACTION,
			)
			return
		if not platform.is_success(raw):
			code, message = platform.parse_error(raw)
			recoverable, recovery_action = error_contract_for_code(code)
			handle_error_output(
				ctx, "history",
				code=code,
				message=message or "浏览历史获取失败",
				recoverable=recoverable,
				recovery_action=recovery_action,
			)
			return
		platform_data = platform.unwrap_data(raw) or {}
		job_list = platform_data.get("jobList", [])

		items = [JobItem.from_api(raw_item).to_dict() for raw_item in job_list]

	pagination = {
		"page": page,
		"has_more": platform_data.get("hasMore", False),
		"total": len(items),
	}
	hints = {
		"next_actions": [
			f"使用 {boss_command_for_ctx(ctx, 'detail <security_id>')} 查看职位详情",
			f"使用 {boss_command_for_ctx(ctx, 'greet <security_id> <job_id>')} 打招呼",
			f"使用 {boss_command_for_ctx(ctx, f'history --page {page + 1}')} 查看下一页",
		],
	}

	handle_output(
		ctx, "history", items,
		render=lambda data: render_job_table(
			data, "history",
			page=page,
			hint_next=(
				f"more: {boss_command_for_ctx(ctx, f'history --page {page + 1}')}"
				if platform_data.get("hasMore") else ""
			),
		),
		pagination=pagination, hints=hints,
	)
