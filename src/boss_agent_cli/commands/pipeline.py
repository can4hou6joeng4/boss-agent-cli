import time
from typing import Any

import click

from boss_agent_cli.api.client import BossClient
from boss_agent_cli.auth.manager import AuthManager
from boss_agent_cli.display import handle_auth_errors, handle_output, render_simple_list
from boss_agent_cli.pipeline_state import build_pipeline_items, select_follow_up_candidates


def _collect_pipeline_items(ctx: click.Context, *, now_ts_ms: int | None, stale_days: int) -> list[dict[str, Any]]:
	data_dir = ctx.obj["data_dir"]
	logger = ctx.obj["logger"]
	delay = ctx.obj["delay"]
	cdp_url = ctx.obj.get("cdp_url")
	auth = AuthManager(data_dir, logger=logger)

	with BossClient(auth, delay=delay, cdp_url=cdp_url) as client:
		friend_resp = client.friend_list(page=1)
		chat_items = friend_resp.get("zpData", {}).get("result") or friend_resp.get("zpData", {}).get("friendList") or []
		interview_resp = client.interview_data()
		interview_items = interview_resp.get("zpData", {}).get("interviewList") or []

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
