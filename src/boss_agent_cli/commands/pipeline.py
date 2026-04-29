import time
from typing import Any

import click

from boss_agent_cli.auth.manager import AuthManager
from boss_agent_cli.commands._platform import get_platform_instance
from boss_agent_cli.display import error_contract_for_code, handle_auth_errors, handle_error_output, handle_output, render_simple_list
from boss_agent_cli.pipeline_state import build_pipeline_items, select_follow_up_candidates


def _collect_pipeline_items(ctx: click.Context, *, now_ts_ms: int | None, stale_days: int) -> list[dict[str, Any]]:
	data_dir = ctx.obj["data_dir"]
	logger = ctx.obj["logger"]
	auth = AuthManager(data_dir, logger=logger, platform=ctx.obj.get("platform", "zhipin"))

	with get_platform_instance(ctx, auth) as platform:
		friend_resp = platform.friend_list(page=1)
		if not platform.is_success(friend_resp):
			code, message = platform.parse_error(friend_resp)
			recoverable, recovery_action = error_contract_for_code(code)
			handle_error_output(
				ctx, "pipeline",
				code=code,
				message=message or "沟通列表获取失败",
				recoverable=recoverable,
				recovery_action=recovery_action,
			)
			return []
		friend_data = platform.unwrap_data(friend_resp) or {}
		chat_items = friend_data.get("result") or friend_data.get("friendList") or []
		interview_resp = platform.interview_data()
		if not platform.is_success(interview_resp):
			code, message = platform.parse_error(interview_resp)
			recoverable, recovery_action = error_contract_for_code(code)
			handle_error_output(
				ctx, "pipeline",
				code=code,
				message=message or "面试列表获取失败",
				recoverable=recoverable,
				recovery_action=recovery_action,
			)
			return []
		interview_data = platform.unwrap_data(interview_resp) or {}
		interview_items = interview_data.get("interviewList") or []

	return build_pipeline_items(
		chat_items=chat_items,
		interview_items=interview_items,
		now_ts_ms=now_ts_ms or int(time.time() * 1000),
		stale_days=stale_days,
	)


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
@click.option("--days-stale", default=3, type=int, help="超过 N 天未推进则标记为 follow_up")
@click.option("--now-ts-ms", default=None, type=int, help="测试用：覆盖当前时间戳（毫秒）")
@click.pass_context
@handle_auth_errors("pipeline")
def pipeline_cmd(ctx: click.Context, days_stale: int, now_ts_ms: int | None) -> None:
	items = _collect_pipeline_items(ctx, now_ts_ms=now_ts_ms, stale_days=days_stale)
	handle_output(
		ctx,
		"pipeline",
		items,
		render=lambda data: _render_pipeline(data, "pipeline"),
		hints={"next_actions": ["boss follow-up", "boss chat", "boss interviews"]},
	)


@click.command("follow-up")
@click.option("--days-stale", default=3, type=int, help="超过 N 天未推进则视为 follow_up")
@click.option("--now-ts-ms", default=None, type=int, help="测试用：覆盖当前时间戳（毫秒）")
@click.pass_context
@handle_auth_errors("follow-up")
def follow_up_cmd(ctx: click.Context, days_stale: int, now_ts_ms: int | None) -> None:
	items = _collect_pipeline_items(ctx, now_ts_ms=now_ts_ms, stale_days=days_stale)
	candidates = select_follow_up_candidates(items)
	handle_output(
		ctx,
		"follow-up",
		candidates,
		render=lambda data: _render_pipeline(data, "follow-up"),
		hints={"next_actions": ["boss chat", "boss mark <security_id> --label 沟通中"]},
	)
