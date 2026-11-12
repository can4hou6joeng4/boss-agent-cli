import time
from typing import Any

import click

from boss_agent_cli.auth.manager import AuthManager
from boss_agent_cli.compliance import require_compliance_allowed
from boss_agent_cli.services.friend_list_pages import collect_friend_list_page
from boss_agent_cli.commands._platform import get_platform_instance
from boss_agent_cli.display import boss_command_for_ctx, handle_auth_errors, handle_output, handle_platform_error_output, render_simple_list
from boss_agent_cli.pipeline_state import build_pipeline_items, select_follow_up_candidates


def _collect_pipeline_items(
	ctx: click.Context, *, command_name: str, now_ts_ms: int | None, stale_days: int
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
	data_dir = ctx.obj["data_dir"]
	logger = ctx.obj["logger"]
	auth = AuthManager(data_dir, logger=logger, platform=ctx.obj.get("platform", "zhipin"))

	with get_platform_instance(ctx, auth) as platform:
		chat_items, friend_error, pages_scanned, truncated = collect_friend_list_page(platform)
		if friend_error is not None:
			handle_platform_error_output(ctx, command_name, platform, friend_error, fallback_message="沟通列表获取失败")
			return [], {}
		interview_resp = platform.interview_data()
		if not platform.is_success(interview_resp):
			handle_platform_error_output(ctx, command_name, platform, interview_resp, fallback_message="面试列表获取失败")
			return [], {}
		interview_data = platform.unwrap_data(interview_resp) or {}
		interview_items = interview_data.get("interviewList") or []

	items = build_pipeline_items(
		chat_items=chat_items,
		interview_items=interview_items,
		now_ts_ms=now_ts_ms or int(time.time() * 1000),
		stale_days=stale_days,
	)
	# 达到分页上限时 data 只覆盖前 N 页，必须显式告诉 Agent，否则会把前缀当全量（Issue #885）。
	# total 由各命令按自己的输出填，这里只给扫描范围。
	pagination = {"pages_scanned": pages_scanned, "truncated": truncated}
	return items, pagination


def _render_pipeline(data: list[dict[str, Any]], title: str) -> None:
	render_simple_list(
		data,
		title,
		[
			("阶段", "stage", "bold cyan"),
			("公司", "company", "green"),
			("职位/关系", "title", "yellow"),
			("来源", "source", "dim"),
			("未读", "unread", "red"),
			("最近时间", "last_time", "dim"),
			("原因", "reason", ""),
		],
	)


@click.command("pipeline")
@click.option("--days-stale", default=3, type=click.IntRange(min=0), help="超过 N 天未推进则标记为 follow_up")
@click.option("--now-ts-ms", default=None, type=int, help="测试用：覆盖当前时间戳（毫秒）")
@click.pass_context
@handle_auth_errors("pipeline")
def pipeline_cmd(ctx: click.Context, days_stale: int, now_ts_ms: int | None) -> None:
	"""聚合聊天与面试数据，生成候选人进度视图。"""
	if not require_compliance_allowed(ctx, "pipeline"):
		ctx.exit(1)

	items, pagination = _collect_pipeline_items(ctx, command_name="pipeline", now_ts_ms=now_ts_ms, stale_days=days_stale)
	handle_output(
		ctx,
		"pipeline",
		items,
		render=lambda data: _render_pipeline(data, "pipeline"),
		pagination={**pagination, "total": len(items), "total_kind": "returned_page_count"} if pagination else None,
		hints={"next_actions": [
			boss_command_for_ctx(ctx, "follow-up", include_data_dir=True),
			boss_command_for_ctx(ctx, "chat", include_data_dir=True),
			boss_command_for_ctx(ctx, "interviews", include_data_dir=True),
		]},
	)


@click.command("follow-up")
@click.option("--days-stale", default=3, type=click.IntRange(min=0), help="超过 N 天未推进则视为 follow_up")
@click.option("--now-ts-ms", default=None, type=int, help="测试用：覆盖当前时间戳（毫秒）")
@click.pass_context
@handle_auth_errors("follow-up")
def follow_up_cmd(ctx: click.Context, days_stale: int, now_ts_ms: int | None) -> None:
	"""筛出超过 N 天未推进、需要跟进的候选人与会话。"""
	if not require_compliance_allowed(ctx, "follow-up"):
		ctx.exit(1)

	items, pagination = _collect_pipeline_items(
		ctx, command_name="follow-up", now_ts_ms=now_ts_ms, stale_days=days_stale
	)
	candidates = select_follow_up_candidates(items)
	handle_output(
		ctx,
		"follow-up",
		candidates,
		render=lambda data: _render_pipeline(data, "follow-up"),
		pagination={**pagination, "total": len(candidates), "total_kind": "returned_page_count"} if pagination else None,
		hints={"next_actions": [
			boss_command_for_ctx(ctx, "chat", include_data_dir=True),
			boss_command_for_ctx(ctx, "mark <uid> --label 沟通中", include_data_dir=True),
		]},
	)
