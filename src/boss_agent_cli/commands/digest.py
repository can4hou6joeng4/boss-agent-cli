import sys
import time
from pathlib import Path
from typing import Any

import click

from boss_agent_cli.auth.manager import AuthManager
from boss_agent_cli.compliance import require_compliance_allowed
from boss_agent_cli.commands._platform import get_platform_instance
from boss_agent_cli.services.output_path import prepare_output_path
from boss_agent_cli.services.friend_list_pages import collect_friend_list_page
from boss_agent_cli.digest import build_digest, render_digest_markdown
from boss_agent_cli.display import boss_command_for_ctx, handle_auth_errors, handle_output, handle_platform_error_output, json_forced, render_message_panel, render_simple_list, shell_argument
from boss_agent_cli.pipeline_state import build_pipeline_items, select_follow_up_candidates


# 会话类条目（新匹配 / 待跟进）与面试条目的展示列。两者字段形状相同，
# 但面试的 ``last_msg`` 是状态描述、``last_time`` 是面试时间，列名不同。
_CHAT_SECTION_COLUMNS = [
	("公司", "company", "green"),
	("职位", "title", "yellow"),
	("关系", "relation", ""),
	("未读", "unread", "red"),
	("最近时间", "last_time", "dim"),
	("最近消息", "last_msg", ""),
]
_INTERVIEW_SECTION_COLUMNS = [
	("公司", "company", "green"),
	("职位", "title", "yellow"),
	("面试时间", "last_time", "dim"),
	("状态", "last_msg", ""),
]


def _render_digest_section(items: object, title: str, columns: list[tuple[str, str, str]]) -> None:
	if isinstance(items, list) and items:
		render_simple_list(items, title, columns)


def _render_digest(data: dict[str, Any]) -> None:
	"""digest 的终端渲染：面板只放计数与摘要，三个列表分别用表格展示。

	``new_matches`` / ``follow_ups`` / ``interviews`` 都是完整结果项，直接插进
	面板会渲染成一大段 dict repr——日报一天几十条时既撑爆面板，也没法扫读
	公司、职位与未读状态。空小节不打印，避免「no xxx」占位。
	"""
	summary = {
		key: value
		for key, value in data.items()
		if key not in ("new_matches", "follow_ups", "interviews")
	}
	render_message_panel(summary, title="digest")
	_render_digest_section(data.get("new_matches"), "新匹配", _CHAT_SECTION_COLUMNS)
	_render_digest_section(data.get("follow_ups"), "待跟进", _CHAT_SECTION_COLUMNS)
	_render_digest_section(data.get("interviews"), "面试", _INTERVIEW_SECTION_COLUMNS)


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
	"""汇总新增职位、待跟进会话与面试项的日报（JSON 或 Markdown）。"""
	if not require_compliance_allowed(ctx, "digest"):
		ctx.exit(1)

	data_dir = ctx.obj["data_dir"]
	logger = ctx.obj["logger"]
	auth = AuthManager(data_dir, logger=logger, platform=ctx.obj.get("platform", "zhipin"))

	with get_platform_instance(ctx, auth) as platform:
		chat_items, friend_error, pages_scanned, truncated = collect_friend_list_page(platform)
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
	# 沟通列表达到分页上限时，上面所有计数都只是前 N 页的前缀；在 data 里显式
	# 标注，Agent 才不会把它当成全量日报（Issue #885）。
	data["chat_pages_scanned"] = pages_scanned
	data["truncated"] = truncated

	if output_format == "md":
		md_text = render_digest_markdown(data)
		if output_path:
			target = prepare_output_path(output_path)
			target.write_text(md_text, encoding="utf-8")
			handle_output(
				ctx,
				"digest",
				{"format": "md", "path": str(target), "bytes": len(md_text.encode("utf-8"))},
				hints={"next_actions": [f"open {shell_argument(str(target))}"],
					"operator_actions": ["用 cat 或邮件客户端直接发送该文件"]},
			)
		elif json_forced(ctx):
			# `--json` 要求 stdout 只有一个信封（MCP 总是这样调用）；把正文放进
			# data.content，而不是让 Agent 拿到一段解析不了的 Markdown。
			handle_output(
				ctx,
				"digest",
				{"format": "md", "content": md_text, "bytes": len(md_text.encode("utf-8"))},
				hints={"next_actions": [boss_command_for_ctx(ctx, "digest --format md -o <path>", include_data_dir=True)],
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
		render=_render_digest,
		hints={"next_actions": [
			boss_command_for_ctx(ctx, "pipeline", include_data_dir=True),
			boss_command_for_ctx(ctx, "follow-up", include_data_dir=True),
			boss_command_for_ctx(ctx, "watch list", include_data_dir=True),
		]},
	)
