import click

from boss_agent_cli.api.models import JobItem, field_list
from boss_agent_cli.auth.manager import AuthManager
from boss_agent_cli.commands._platform import get_platform_instance
from boss_agent_cli.display import boss_command_for_ctx, handle_auth_errors, handle_not_supported, handle_output, handle_platform_error_output, render_job_table


@click.command("history")
@click.option("--page", default=1, type=click.IntRange(min=1), help="页码")
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
			handle_not_supported(ctx, "history", exc, fallback_message="当前平台不支持浏览历史能力")
			return
		if not platform.is_success(raw):
			handle_platform_error_output(
				ctx, "history", platform, raw,
				fallback_message="浏览历史获取失败",
			)
			return
		platform_data = platform.unwrap_data(raw) or {}
		job_list = field_list(platform_data, "jobList")

		items = [JobItem.from_api(raw_item).to_dict() for raw_item in job_list]

	# 平台缺失 hasMore 时不能当成 false：那会让 Agent 把「平台没说」读成
	# 「确实没有下一页」并停止翻页。与 recommend / chat 同一套保守语义。
	raw_has_more = platform_data.get("hasMore")
	has_more = raw_has_more if isinstance(raw_has_more, bool) else None
	pagination = {
		"page": page,
		"has_more": has_more,
		"has_more_source": "platform_hasMore" if has_more is not None else "unknown",
		"total": len(items),
		"total_kind": "returned_page_count",
	}
	next_actions = [
		boss_command_for_ctx(ctx, "detail <security_id>"),
		boss_command_for_ctx(ctx, "apply <security_id> <job_id>"),
		boss_command_for_ctx(ctx, "greet <security_id> <job_id>"),
	]
	# 没有下一页时不能建议 --page N+1：Agent 照着执行只会拿到空页，
	# 与 TTY 的 hint_next 守卫（hasMore 为假时不显示 more）保持一致。
	if pagination["has_more"] is True:
		next_actions.append(boss_command_for_ctx(ctx, f"history --page {page + 1}"))
	hints = {"next_actions": next_actions}
	if has_more is None:
		hints["operator_actions"] = ["平台未提供可靠的下一页标记，不能仅凭本页数量判断是否还有记录"]

	handle_output(
		ctx, "history", items,
		render=lambda data: render_job_table(
			data, "history",
			page=page,
			hint_next=(
				f"more: {boss_command_for_ctx(ctx, f'history --page {page + 1}')}"
				if pagination["has_more"] else ""
			),
		),
		pagination=pagination, hints=hints,
	)
