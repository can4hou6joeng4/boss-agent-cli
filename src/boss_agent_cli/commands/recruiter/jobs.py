"""招聘者 — 职位管理。"""
import click

from boss_agent_cli.auth.manager import AuthManager
from boss_agent_cli.commands._recruiter_platform import get_recruiter_platform_instance
from boss_agent_cli.display import handle_auth_errors, handle_output


@click.group("jobs")
@click.pass_context
def jobs_group(ctx: click.Context) -> None:
	"""管理职位发布"""
	pass


@jobs_group.command("list")
@click.option("--page", default=1, type=int, help="页码")
@click.pass_context
@handle_auth_errors("recruiter-jobs-list")
def jobs_list_cmd(ctx: click.Context, page: int) -> None:
	"""查看已发布的职位列表"""
	data_dir = ctx.obj["data_dir"]
	logger = ctx.obj["logger"]

	auth = AuthManager(data_dir, logger=logger)
	with get_recruiter_platform_instance(ctx, auth) as platform:
		result = platform.list_jobs(page)
		data = result.get("zpData", {})
		handle_output(ctx, "recruiter-jobs-list", data)


@jobs_group.command("detail")
@click.argument("job_id")
@click.pass_context
@handle_auth_errors("recruiter-jobs-detail")
def jobs_detail_cmd(ctx: click.Context, job_id: str) -> None:
	"""查看职位详情与申请人统计"""
	data_dir = ctx.obj["data_dir"]
	logger = ctx.obj["logger"]

	auth = AuthManager(data_dir, logger=logger)
	with get_recruiter_platform_instance(ctx, auth) as platform:
		result = platform.job_detail(job_id)
		data = result.get("zpData", {})
		handle_output(ctx, "recruiter-jobs-detail", data)


@jobs_group.command("close")
@click.argument("job_id")
@click.pass_context
@handle_auth_errors("recruiter-jobs-close")
def jobs_close_cmd(ctx: click.Context, job_id: str) -> None:
	"""关闭职位发布"""
	data_dir = ctx.obj["data_dir"]
	logger = ctx.obj["logger"]

	auth = AuthManager(data_dir, logger=logger)
	with get_recruiter_platform_instance(ctx, auth) as platform:
		platform.close_job(job_id)
		data = {"job_id": job_id, "message": "职位已关闭"}
		handle_output(ctx, "recruiter-jobs-close", data)
