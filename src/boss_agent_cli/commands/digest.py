import time

import click

from boss_agent_cli.api.client import BossClient
from boss_agent_cli.auth.manager import AuthManager
from boss_agent_cli.digest import build_digest
from boss_agent_cli.display import handle_auth_errors, handle_output, render_message_panel
from boss_agent_cli.pipeline_state import build_pipeline_items, select_follow_up_candidates


@click.command("digest")
@click.option("--days-stale", default=3, type=int, help="超过 N 天未推进则视为 follow_up")
@click.option("--now-ts-ms", default=None, type=int, help="测试用：覆盖当前时间戳（毫秒）")
@click.pass_context
@handle_auth_errors("digest")
def digest_cmd(ctx, days_stale, now_ts_ms):
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

	items = build_pipeline_items(
		chat_items=chat_items,
		interview_items=interview_items,
		now_ts_ms=now_ts_ms or int(time.time() * 1000),
		stale_days=days_stale,
	)
	follow_ups = select_follow_up_candidates(items)
	new_matches = [item for item in items if item.get("source") == "chat" and item.get("stage") == "reply_needed"]

	data = build_digest(
		new_matches=new_matches,
		follow_ups=follow_ups,
		interviews=[item for item in items if item.get("source") == "interview"],
	)

	handle_output(
		ctx,
		"digest",
		data,
		render=lambda d: render_message_panel(d, title="digest"),
		hints={"next_actions": ["boss pipeline", "boss follow-up", "boss watch list"]},
	)
