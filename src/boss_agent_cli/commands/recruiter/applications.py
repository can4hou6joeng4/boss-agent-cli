"""招聘者 — 候选人投递申请管理。"""
from typing import Any

import click

from boss_agent_cli.auth.manager import AuthManager
from boss_agent_cli.compliance import require_compliance_allowed
from boss_agent_cli.commands._recruiter_platform import get_recruiter_platform_instance
from boss_agent_cli.commands.recruiter._hints import hint_command
from boss_agent_cli.display import (
	handle_auth_errors,
	handle_output,
	handle_platform_error_output,
	shell_argument,
)
from boss_agent_cli.services.pagination import (
	UNKNOWN_HAS_MORE_OPERATOR_ACTION,
	list_pagination,
	rows_from,
)


@click.command("applications")
@click.option("--job-id", default=None, help="按职位筛选")
@click.option("--label-id", default=0, type=click.IntRange(min=0), help="按标签筛选（0=全部, 1=新招呼, 2=沟通中）")
@click.option("--page", default=1, type=click.IntRange(min=1), help="页码")
@click.pass_context
@handle_auth_errors("recruiter-applications")
def applications_cmd(ctx: click.Context, job_id: str | None, label_id: int, page: int) -> None:
	"""查看候选人投递申请列表"""
	if not require_compliance_allowed(ctx, "recruiter-applications"):
		return

	data_dir = ctx.obj["data_dir"]
	logger = ctx.obj["logger"]

	auth = AuthManager(data_dir, logger=logger, platform=ctx.obj.get("platform", "zhipin"))
	with get_recruiter_platform_instance(ctx, auth) as platform:
		result = platform.friend_list(page=page, label_id=label_id, job_id=job_id)
		if not platform.is_success(result):
			handle_platform_error_output(ctx, "recruiter-applications", platform, result, fallback_message="投递申请列表获取失败")
			return
		data = platform.unwrap_data(result) or {}
		pagination = list_pagination(data, page=page, rows=rows_from(data, "friendList"))
		next_actions = [
			hint_command(ctx, "hr resume <geek_id> --job-id <id> --security-id <id>"),
			hint_command(ctx, "hr chat"),
		]
		if pagination["has_more"] is True:
			next_page = f"hr applications --page {pagination['page'] + 1}"
			if job_id:
				next_page += f" --job-id {shell_argument(job_id)}"
			if label_id:
				next_page += f" --label-id {label_id}"
			next_actions.append(hint_command(ctx, next_page))
		hints: dict[str, Any] = {"next_actions": next_actions}
		if pagination["has_more"] is None:
			hints["operator_actions"] = [UNKNOWN_HAS_MORE_OPERATOR_ACTION]
		handle_output(
			ctx, "recruiter-applications", data,
			pagination=pagination,
			hints=hints,
		)
