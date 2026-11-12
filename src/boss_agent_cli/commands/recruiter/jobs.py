"""招聘者 — 职位管理。"""
from typing import Any

import click

from boss_agent_cli.auth.manager import AuthManager
from boss_agent_cli.commands._recruiter_platform import get_recruiter_platform_instance
from boss_agent_cli.commands.recruiter._hints import hint_command
from boss_agent_cli.display import (
	handle_auth_errors,
	handle_error_output,
	handle_output,
	handle_platform_error_output,
	render_action_result,
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
		# 平台把分页事实放在 data.page / hasMore / totalSize 里，信封 pagination
		# 却一直是 null：Agent 按契约只看 pagination 时既不知道当前页，也没有
		# 「是否还有下一页」的证据。data 原样保留，避免破坏已有消费方。
		if isinstance(data, dict):
			raw_page = data.get("page")
			current_page = (
				raw_page
				if isinstance(raw_page, int) and not isinstance(raw_page, bool) and raw_page >= 1
				else page
			)
			raw_has_more = data.get("hasMore")
			has_more = raw_has_more if isinstance(raw_has_more, bool) else None
			raw_total = data.get("totalSize")
			total_available = (
				raw_total
				if isinstance(raw_total, int) and not isinstance(raw_total, bool) and raw_total >= 0
				else None
			)
			rows = data.get("data")
			row_count = len(rows) if isinstance(rows, list) else 0
			pagination: dict[str, Any] = {
				"page": current_page,
				"has_more": has_more,
				"has_more_source": "platform_hasMore" if has_more is not None else "unknown",
				"total": row_count,
				"total_kind": "returned_page_count",
				"total_available": total_available,
			}
		else:
			pagination = {
				"page": page,
				"has_more": None,
				"has_more_source": "unknown",
				"total": None,
				"total_kind": "returned_page_count",
				"total_available": None,
			}
		# 成功信封原先没有下一步：Agent 拿到职位清单后不知道可以点进详情
		# （列表为空时更没有任何可执行动作），只能自己翻 `boss hr --help`。
		next_actions = [hint_command(ctx, "hr jobs detail <enc_job_id>")]
		if pagination["has_more"] is True:
			next_actions.append(hint_command(ctx, f"hr jobs list --page {pagination['page'] + 1}"))
		hints: dict[str, Any] = {"next_actions": next_actions}
		if pagination["has_more"] is None:
			hints["operator_actions"] = ["平台未提供可靠的下一页标记，不能仅凭本页数量判断是否还有职位"]
		handle_output(
			ctx,
			"recruiter-jobs-list",
			data,
			pagination=pagination,
			hints=hints,
		)


@jobs_group.command("offline")
@click.argument("job_id")
@click.pass_context
@handle_auth_errors("recruiter-jobs-offline")
def jobs_offline_cmd(ctx: click.Context, job_id: str) -> None:
	"""下线职位"""
	# 空 job_id 无法命中任何职位，却会带着空参数发出一次上线状态变更请求。
	if not job_id.strip():
		list_cmd = hint_command(ctx, "hr jobs list")
		handle_error_output(
			ctx,
			"recruiter-jobs-offline",
			code="INVALID_PARAM",
			message=f"job_id 不能为空；先用 {list_cmd} 取到职位 ID",
			recovery_action=f"补上 job_id 后重试；职位 ID 可从 {list_cmd} 的结果中取到",
			hints={"next_actions": [list_cmd]},
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
		# 上下线是成对动作：下线后 Agent 要能核对状态、或把职位重新上架；
		# 只回一句 message 时两条路径都没有可执行的后继命令。
		next_actions = [
			hint_command(ctx, "hr jobs list"),
			hint_command(ctx, f"hr jobs online {job_id}"),
		]
		handle_output(
			ctx,
			"recruiter-jobs-offline",
			data,
			render=lambda d: render_action_result(d, title="hr jobs offline", next_steps=next_actions),
			hints={"next_actions": next_actions},
		)


@jobs_group.command("online")
@click.argument("job_id")
@click.pass_context
@handle_auth_errors("recruiter-jobs-online")
def jobs_online_cmd(ctx: click.Context, job_id: str) -> None:
	"""上线职位"""
	# 与 offline 同理：空 job_id 的「上线」请求只会换来一次无意义的平台往返。
	if not job_id.strip():
		list_cmd = hint_command(ctx, "hr jobs list")
		handle_error_output(
			ctx,
			"recruiter-jobs-online",
			code="INVALID_PARAM",
			message=f"job_id 不能为空；先用 {list_cmd} 取到职位 ID",
			recovery_action=f"补上 job_id 后重试；职位 ID 可从 {list_cmd} 的结果中取到",
			hints={"next_actions": [list_cmd]},
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
		next_actions = [
			hint_command(ctx, "hr jobs list"),
			hint_command(ctx, f"hr jobs offline {job_id}"),
		]
		handle_output(
			ctx,
			"recruiter-jobs-online",
			data,
			render=lambda d: render_action_result(d, title="hr jobs online", next_steps=next_actions),
			hints={"next_actions": next_actions},
		)


@jobs_group.command("detail")
@click.argument("enc_job_id")
@click.pass_context
@handle_auth_errors("recruiter-jobs-detail")
def jobs_detail_cmd(ctx: click.Context, enc_job_id: str) -> None:
	"""查看职位详情（含完整 JD）"""
	if not enc_job_id.strip():
		list_cmd = hint_command(ctx, "hr jobs list")
		handle_error_output(
			ctx,
			"recruiter-jobs-detail",
			code="INVALID_PARAM",
			message=f"enc_job_id 不能为空；先用 {list_cmd} 取到职位 ID",
			recovery_action=f"补上 enc_job_id 后重试；编号可从 {list_cmd} 的结果中取到",
			hints={"next_actions": [list_cmd]},
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
		handle_output(
			ctx,
			"recruiter-jobs-detail",
			data,
			hints={
				"next_actions": [
					hint_command(ctx, f"hr jobs offline {enc_job_id}"),
					hint_command(ctx, f"hr jobs online {enc_job_id}"),
					hint_command(ctx, "hr jobs list"),
				]
			},
		)
