"""招聘者 — 投递申请管理。"""
import click

from boss_agent_cli.auth.manager import AuthManager
from boss_agent_cli.commands._recruiter_platform import get_recruiter_platform_instance
from boss_agent_cli.display import handle_auth_errors, handle_output


@click.command("applications")
@click.option("--job-id", default=None, help="按职位筛选")
@click.option("--status", default=None, help="按状态筛选")
@click.option("--keyword", default=None, help="关键词筛选")
@click.option("--page", default=1, type=int, help="页码")
@click.pass_context
@handle_auth_errors("recruiter-applications")
def applications_cmd(ctx: click.Context, job_id: str | None, status: str | None, keyword: str | None, page: int) -> None:
	"""查看候选人投递申请列表"""
	data_dir = ctx.obj["data_dir"]
	logger = ctx.obj["logger"]

	auth = AuthManager(data_dir, logger=logger)
	with get_recruiter_platform_instance(ctx, auth) as platform:
		result = platform.list_applications(job_id=job_id, status=status, keyword=keyword, page=page)
		data = result.get("zpData", {})
		handle_output(
			ctx, "recruiter-applications", data,
			hints={"next_actions": [
				"boss --role recruiter recruiter resume <id> — 查看候选人简历",
				"boss --role recruiter recruiter chat — 查看沟通列表",
			]},
		)
