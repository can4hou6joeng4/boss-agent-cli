"""招聘者 — 职位管理。"""
import click

from boss_agent_cli.auth.manager import AuthManager
from boss_agent_cli.commands._recruiter_platform import get_recruiter_platform_instance
from boss_agent_cli.display import (
	boss_command_for_ctx,
	handle_auth_errors,
	handle_error_output,
	handle_output,
	handle_platform_error_output,
)


@click.group("jobs")
@click.pass_context
def jobs_group(ctx: click.Context) -> None:
	"""管理职位发布"""
	pass


@jobs_group.command("list")
@click.option("--page", default=1, type=click.IntRange(min=1), help="页码")
@click.pass_context
@handle_auth_errors("recruiter-jobs-list")
def jobs_list_cmd(ctx: click.Context, page: int) -> None:
	"""查看已发布的职位列表"""
	data_dir = ctx.obj["data_dir"]
	logger = ctx.obj["logger"]

	auth = AuthManager(data_dir, logger=logger, platform=ctx.obj.get("platform", "zhipin"))
	with get_recruiter_platform_instance(ctx, auth) as platform:
		result = platform.list_jobs(page=page)
		if not platform.is_success(result):
			handle_platform_error_output(ctx, "recruiter-jobs-list", platform, result, fallback_message="职位列表获取失败")
			return
		data = platform.unwrap_data(result) or {}
		handle_output(ctx, "recruiter-jobs-list", data)


@jobs_group.command("offline")
@click.argument("job_id")
@click.pass_context
@handle_auth_errors("recruiter-jobs-offline")
def jobs_offline_cmd(ctx: click.Context, job_id: str) -> None:
	"""下线职位"""
	# 空 job_id 无法命中任何职位，却会带着空参数发出一次上线状态变更请求。
	if not job_id.strip():
		handle_error_output(
			ctx,
			"recruiter-jobs-offline",
			code="INVALID_PARAM",
			message="job_id 不能为空；先用 boss hr jobs list 取到职位 ID",
			hints={"next_actions": [boss_command_for_ctx(ctx, "hr jobs list")]},
		)
		return
	data_dir = ctx.obj["data_dir"]
	logger = ctx.obj["logger"]

	auth = AuthManager(data_dir, logger=logger, platform=ctx.obj.get("platform", "zhipin"))
	with get_recruiter_platform_instance(ctx, auth) as platform:
		result = platform.job_offline(job_id)
		if not platform.is_success(result):
			handle_platform_error_output(ctx, "recruiter-jobs-offline", platform, result, fallback_message="职位下线失败")
			return
		data = {"job_id": job_id, "message": "职位已下线"}
		handle_output(ctx, "recruiter-jobs-offline", data)


@jobs_group.command("online")
@click.argument("job_id")
@click.pass_context
@handle_auth_errors("recruiter-jobs-online")
def jobs_online_cmd(ctx: click.Context, job_id: str) -> None:
	"""上线职位"""
	# 与 offline 同理：空 job_id 的「上线」请求只会换来一次无意义的平台往返。
	if not job_id.strip():
		handle_error_output(
			ctx,
			"recruiter-jobs-online",
			code="INVALID_PARAM",
			message="job_id 不能为空；先用 boss hr jobs list 取到职位 ID",
			hints={"next_actions": [boss_command_for_ctx(ctx, "hr jobs list")]},
		)
		return
	data_dir = ctx.obj["data_dir"]
	logger = ctx.obj["logger"]

	auth = AuthManager(data_dir, logger=logger, platform=ctx.obj.get("platform", "zhipin"))
	with get_recruiter_platform_instance(ctx, auth) as platform:
		result = platform.job_online(job_id)
		if not platform.is_success(result):
			handle_platform_error_output(ctx, "recruiter-jobs-online", platform, result, fallback_message="职位上线失败")
			return
		data = {"job_id": job_id, "message": "职位已上线"}
		handle_output(ctx, "recruiter-jobs-online", data)


@jobs_group.command("detail")
@click.argument("enc_job_id")
@click.pass_context
@handle_auth_errors("recruiter-jobs-detail")
def jobs_detail_cmd(ctx: click.Context, enc_job_id: str) -> None:
	"""查看职位详情（含完整 JD）"""
	if not enc_job_id.strip():
		handle_error_output(
			ctx,
			"recruiter-jobs-detail",
			code="INVALID_PARAM",
			message="enc_job_id 不能为空；先用 boss hr jobs list 取到职位 ID",
			hints={"next_actions": [boss_command_for_ctx(ctx, "hr jobs list")]},
		)
		return
	data_dir = ctx.obj["data_dir"]
	logger = ctx.obj["logger"]

	auth = AuthManager(data_dir, logger=logger, platform=ctx.obj.get("platform", "zhipin"))
	with get_recruiter_platform_instance(ctx, auth) as platform:
		result = platform.job_detail(enc_job_id)
		if not platform.is_success(result):
			handle_platform_error_output(ctx, "recruiter-jobs-detail", platform, result, fallback_message="职位详情获取失败")
			return
		data = platform.unwrap_data(result) or {}
		handle_output(ctx, "recruiter-jobs-detail", data)
