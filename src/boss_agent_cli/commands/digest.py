import sys
import time
from pathlib import Path

import click

from boss_agent_cli.auth.manager import AuthManager
from boss_agent_cli.compliance import require_compliance_allowed
from boss_agent_cli.commands._platform import get_platform_instance
from boss_agent_cli.services.output_path import prepare_output_path
from boss_agent_cli.services.friend_list_pages import collect_friend_list_items
from boss_agent_cli.digest import build_digest, render_digest_markdown
from boss_agent_cli.display import boss_command_for_ctx, handle_auth_errors, handle_output, handle_platform_error_output, json_forced, render_message_panel
from boss_agent_cli.pipeline_state import build_pipeline_items, select_follow_up_candidates


@click.command("digest")
@click.option("--days-stale", default=3, type=click.IntRange(min=0, max=36500), help="超过 N 天未推进则视为 follow_up")
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
	help="Markdown 输出路径（仅 --format md 时有效，未指定时写到 stdout；--json 时放进信封 data.content）",
)
@click.pass_context
@handle_auth_errors("digest")
def digest_cmd(ctx: click.Context, days_stale: int, now_ts_ms: int | None, output_format: str, output_path: Path | None) -> None:
	if not require_compliance_allowed(ctx, "digest"):
		ctx.exit(1)

	data_dir = ctx.obj["data_dir"]
	logger = ctx.obj["logger"]
	auth = AuthManager(data_dir, logger=logger, platform=ctx.obj.get("platform", "zhipin"))

	with get_platform_instance(ctx, auth) as platform:
		chat_items, friend_error = collect_friend_list_items(platform)
		if friend_error is not None:
			handle_platform_error_output(ctx, "digest", platform, friend_error, fallback_message="沟通列表获取失败")
			return
		interview_resp = platform.interview_data()
		if not platform.is_success(interview_resp):
			handle_platform_error_output(ctx, "digest", platform, interview_resp, fallback_message="面试列表获取失败")
			return
		interview_data = platform.unwrap_data(interview_resp) or {}
		interview_items = interview_data.get("interviewList") or []

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
			target = prepare_output_path(output_path)
			target.write_text(md_text, encoding="utf-8")
			handle_output(
				ctx,
				"digest",
				{"format": "md", "path": str(target), "bytes": len(md_text.encode("utf-8"))},
				hints={"next_actions": [f"open {target}"],
					"operator_actions": ["用 cat 或邮件客户端直接发送该文件"]},
			)
		elif json_forced(ctx):
			# `--json` 要求 stdout 只有一个信封（MCP 总是这样调用）；把正文放进
			# data.content，而不是让 Agent 拿到一段解析不了的 Markdown。
			handle_output(
				ctx,
				"digest",
				{"format": "md", "content": md_text, "bytes": len(md_text.encode("utf-8"))},
				hints={"next_actions": [boss_command_for_ctx(ctx, "digest --format md -o <path>")],
					"operator_actions": ["把 data.content 写成 .md 文件或直接发送"]},
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
