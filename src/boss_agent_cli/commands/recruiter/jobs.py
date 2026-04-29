"""招聘者 — 职位管理。"""
import click

from boss_agent_cli.auth.manager import AuthManager
from boss_agent_cli.commands._recruiter_platform import get_recruiter_platform_instance
from boss_agent_cli.display import error_contract_for_code, handle_auth_errors, handle_error_output, handle_output


@click.group("jobs")
@click.pass_context
def jobs_group(ctx: click.Context) -> None:
	"""管理职位发布"""
	pass


@jobs_group.command("list")
@click.pass_context
@handle_auth_errors("recruiter-jobs-list")
def jobs_list_cmd(ctx: click.Context) -> None:
	"""查看已发布的职位列表"""
	data_dir = ctx.obj["data_dir"]
	logger = ctx.obj["logger"]

	auth = AuthManager(data_dir, logger=logger, platform=ctx.obj.get("platform", "zhipin"))
	with get_recruiter_platform_instance(ctx, auth) as platform:
		result = platform.list_jobs()
		if not platform.is_success(result):
			code, message = platform.parse_error(result)
			recoverable, recovery_action = error_contract_for_code(code)
			handle_error_output(
				ctx, "recruiter-jobs-list",
				code=code,
				message=message or "职位列表获取失败",
				recoverable=recoverable,
				recovery_action=recovery_action,
			)
			return
		data = platform.unwrap_data(result) or {}
		handle_output(ctx, "recruiter-jobs-list", data)


@jobs_group.command("offline")
@click.argument("job_id")
@click.pass_context
@handle_auth_errors("recruiter-jobs-offline")
def jobs_offline_cmd(ctx: click.Context, job_id: str) -> None:
	"""下线职位"""
	data_dir = ctx.obj["data_dir"]
	logger = ctx.obj["logger"]

	auth = AuthManager(data_dir, logger=logger, platform=ctx.obj.get("platform", "zhipin"))
	with get_recruiter_platform_instance(ctx, auth) as platform:
		result = platform.job_offline(job_id)
		if not platform.is_success(result):
			code, message = platform.parse_error(result)
			recoverable, recovery_action = error_contract_for_code(code)
			handle_error_output(
				ctx, "recruiter-jobs-offline",
				code=code,
				message=message or "职位下线失败",
				recoverable=recoverable,
				recovery_action=recovery_action,
			)
			return
		data = {"job_id": job_id, "message": "职位已下线"}
		handle_output(ctx, "recruiter-jobs-offline", data)


@jobs_group.command("online")
@click.argument("job_id")
@click.pass_context
@handle_auth_errors("recruiter-jobs-online")
def jobs_online_cmd(ctx: click.Context, job_id: str) -> None:
	"""上线职位"""
	data_dir = ctx.obj["data_dir"]
	logger = ctx.obj["logger"]

	auth = AuthManager(data_dir, logger=logger, platform=ctx.obj.get("platform", "zhipin"))
	with get_recruiter_platform_instance(ctx, auth) as platform:
		result = platform.job_online(job_id)
		if not platform.is_success(result):
			code, message = platform.parse_error(result)
			recoverable, recovery_action = error_contract_for_code(code)
			handle_error_output(
				ctx, "recruiter-jobs-online",
				code=code,
				message=message or "职位上线失败",
				recoverable=recoverable,
				recovery_action=recovery_action,
			)
			return
		data = {"job_id": job_id, "message": "职位已上线"}
		handle_output(ctx, "recruiter-jobs-online", data)
