import sys
import time
from pathlib import Path

import click

from boss_agent_cli.api.client import BossClient
from boss_agent_cli.auth.manager import AuthManager
from boss_agent_cli.digest import build_digest, render_digest_markdown
from boss_agent_cli.display import handle_auth_errors, handle_output, render_message_panel
from boss_agent_cli.pipeline_state import build_pipeline_items, select_follow_up_candidates


@click.command("digest")
@click.option("--days-stale", default=3, type=int, help="超过 N 天未推进则视为 follow_up")
@click.option("--now-ts-ms", default=None, type=int, help="测试用：覆盖当前时间戳（毫秒）")
@click.option(
	"--format", "output_format",
	type=click.Choice(["json", "md"]),
	default="json",
	help="输出格式（json 走 JSON 信封；md 生成可直接发邮件/飞书的 Markdown）",
)
@click.option(
	"-o", "--output", "output_path",
	type=click.Path(dir_okay=False, writable=True, path_type=Path),
	default=None,
	help="Markdown 输出路径（仅 --format md 时有效，未指定时写到 stdout）",
)
@click.pass_context
@handle_auth_errors("digest")
def digest_cmd(ctx, days_stale, now_ts_ms, output_format, output_path):
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

	if output_format == "md":
		md_text = render_digest_markdown(data)
		if output_path:
			output_path.write_text(md_text, encoding="utf-8")
			handle_output(
				ctx,
				"digest",
				{"format": "md", "path": str(output_path), "bytes": len(md_text.encode("utf-8"))},
				hints={"next_actions": [f"open {output_path}", "cat 或邮件客户端直接发送"]},
			)
		else:
			sys.stdout.write(md_text)
			sys.stdout.flush()
		return

	handle_output(
		ctx,
		"digest",
		data,
		render=lambda d: render_message_panel(d, title="digest"),
		hints={"next_actions": ["boss pipeline", "boss follow-up", "boss watch list"]},
	)
