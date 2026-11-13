import datetime
import os
from typing import Any

import click

from boss_agent_cli.auth.manager import AuthManager
from boss_agent_cli.compliance import require_compliance_allowed
from boss_agent_cli.commands._platform import get_platform_instance
from boss_agent_cli.commands._output_path import validate_output_path_or_emit
from boss_agent_cli.services.chat_export import render_export
from boss_agent_cli.services.output_path import prepare_output_path
from boss_agent_cli.services.chat_snapshot import save_snapshot_and_diff, load_snapshot
from boss_agent_cli.services.chat_utils import (
	RELATION_LABELS, FROM_FILTER, MSG_STATUS_LABELS,
	contact_identity, int_code, sanitize_csv_cell, escape_md_cell,
)
from boss_agent_cli.display import boss_command_for_ctx, handle_auth_errors, handle_error_output, handle_not_supported, handle_output, handle_platform_error_output, render_simple_list

# 向后兼容别名（旧测试引用 chat._sanitize_csv_cell 等）
_RELATION_LABELS = RELATION_LABELS
_FROM_FILTER = FROM_FILTER
_MSG_STATUS_LABELS = MSG_STATUS_LABELS
_GROUP_ORDER = ["对方主动", "我主动", "投递"]
_sanitize_csv_cell = sanitize_csv_cell
_escape_md_cell = escape_md_cell


@click.command("chat")
@click.option("--page", default=1, type=click.IntRange(min=1), help="页码")
@click.option("--from", "from_who", default=None, type=click.Choice(["boss", "me"]),
	help="筛选发起方：boss=对方主动联系 / me=我主动打招呼")
@click.option("--days", default=None, type=click.IntRange(min=0, max=36500), help="只显示最近 N 天的记录")
@click.option("--export", "export_fmt", default=None,
	type=click.Choice(["html", "md", "csv", "json"]),
	help="导出格式：html=HTML / md=Markdown / csv=CSV / json=JSON")
@click.option("-o", "--output", "output_path", default=None,
	help="输出文件路径（不指定则自动保存到配置的 export_dir）")
@click.pass_context
@handle_auth_errors("chat")
def chat_cmd(ctx: click.Context, page: int, from_who: str | None, days: int | None, export_fmt: str | None, output_path: str | None) -> None:
	"""查看沟通列表（支持按发起方、时间筛选，支持导出）"""
	if not require_compliance_allowed(ctx, "chat"):
		ctx.exit(1)

	# 只在真要导出时校验：`-o` 指向目录这类参数错误必须在第一次平台请求之前暴露，
	# 而不是抓完沟通列表、写完快照之后再报一个与真正原因无关的信封。
	if export_fmt and output_path:
		validated = validate_output_path_or_emit(ctx, "chat", output_path)
		if validated is None:
			return
		output_path = validated

	data_dir = ctx.obj["data_dir"]
	logger = ctx.obj["logger"]
	auth = AuthManager(data_dir, logger=logger, platform=ctx.obj.get("platform", "zhipin"))

	with get_platform_instance(ctx, auth) as platform:
		try:
			resp = platform.friend_list(page=page)
		except NotImplementedError as exc:
			handle_not_supported(ctx, "chat", exc, fallback_message="当前平台不支持沟通列表能力")
			return
		if not platform.is_success(resp):
			handle_platform_error_output(ctx, "chat", platform, resp, fallback_message="沟通列表获取失败")
			return
		platform_data = platform.unwrap_data(resp) or {}
		items = platform_data.get("result") or platform_data.get("friendList") or []

		# 时间筛选阈值
		cutoff_ts = None
		if days is not None:
			cutoff = datetime.datetime.now() - datetime.timedelta(days=days)
			cutoff_ts = cutoff.timestamp() * 1000

		# 发起方筛选值
		relation_filter = _FROM_FILTER.get(from_who) if from_who else None
		relation_label = _RELATION_LABELS.get(relation_filter) if relation_filter is not None else None

		def _friend_record(item: dict[str, Any]) -> dict[str, Any]:
			# lastTS / relationType / status / unreadMsgCount 都是平台数值编码：
			# JSON 布尔不能当整数用，否则 True 会被翻译成「未读」「对方主动」
			# 或参与未读计数（Issue #915）。
			last_ts = int_code(item.get("lastTS")) or 0
			if last_ts:
				last_time_str = _format_ts(last_ts)
			else:
				last_time_str = item.get("lastTime", "-")
			last_message_info = item.get("lastMessageInfo") or {}
			message_status = last_message_info.get("status")
			msg_status_label = _MSG_STATUS_LABELS.get(int_code(message_status), "未知")
			return {
				"name": item.get("name") or "-",
				"title": item.get("title") or "-",
				"brand_name": item.get("brandName") or "-",
				"initiated_by": _RELATION_LABELS.get(int_code(item.get("relationType")), "未知"),
				"last_msg": item.get("lastMsg") or "-",
				"last_time": last_time_str,
				"last_ts": last_ts,
				"msg_status": msg_status_label,
				# uid 是跨请求稳定的联系人标识，chatmsg/chat-summary/mark/exchange
				# 都应优先使用它；security_id 是每请求轮换的令牌，仅作参考。
				"uid": item.get("uid") or "",
				"security_id": item.get("securityId") or "",
				"encrypt_job_id": item.get("encryptJobId") or "",
				"unread": int_code(item.get("unreadMsgCount")) or 0,
			}

		# 快照保存本次平台页面的完整观察结果；--from / --days 只影响导出内容。
		# 用筛选后的列表做快照，会把用户没选中的联系人当成“已消失”（Issue #911）。
		all_friends = [_friend_record(item) for item in items]

		def _matches_filter(friend: dict[str, Any]) -> bool:
			return (
				(not cutoff_ts or not friend.get("last_ts") or friend["last_ts"] >= cutoff_ts)
				and (relation_label is None or friend.get("initiated_by", "未知") == relation_label)
			)

		friends = [friend for friend in all_friends if _matches_filter(friend)]

		raw_has_more = platform_data.get("hasMore")
		has_more = raw_has_more if isinstance(raw_has_more, bool) else None
		# 只有第 1 页且平台明确没有下一页，才能证明本次看到完整沟通列表；
		# 部分页或未知 hasMore 不能作为“联系人已消失”的证据。
		collection_complete = page == 1 and has_more is False

		# ── 导出模式 ──────────────────────────────────────────────
		if export_fmt:
			# L3: 保存 JSON 快照 + diff
			snapshot_dir = os.path.join(data_dir, "chat-history")
			diff_result = save_snapshot_and_diff(
				snapshot_dir, all_friends, logger, complete=collection_complete
			)
			# 变化摘要只报告导出范围内的联系人：筛选条件之外的新增 / 未读变化
			# 不属于这份导出，不能出现在摘要里（Issue #911）。
			exported_ids = {contact_identity(friend) for friend in friends}
			diff_result = {
				**diff_result,
				"added": [
					item for item in diff_result["added"] if contact_identity(item) in exported_ids
				],
				"removed": [item for item in diff_result["removed"] if _matches_filter(item)],
				"new_unread": [
					item
					for item in diff_result["new_unread"]
					if contact_identity(item) in exported_ids
				],
			}

			content = render_export(friends, export_fmt, from_who, days, diff_result)

			# 未指定 -o 时，自动生成默认路径（日期命名，同天覆盖）
			if not output_path:
				today = datetime.date.today().isoformat()
				# 优先读 config 中的 export_dir，否则 fallback 到 data_dir/chat-export
				export_dir = ctx.obj.get("config", {}).get("export_dir")
				if export_dir and not isinstance(export_dir, str):
					# config.json 被手改成数字 / 数组时 os.path.expanduser 会抛 TypeError：
					# 按参数错误给信封，并指出只改这一项的修复命令。
					handle_error_output(
						ctx,
						"chat",
						code="INVALID_PARAM",
						message=f"export_dir 必须是目录路径字符串或 null，收到 {export_dir!r}",
						recoverable=True,
						recovery_action=boss_command_for_ctx(ctx, "config reset export_dir", include_data_dir=True),
					)
					ctx.exit(1)
					return
				if export_dir:
					export_dir = os.path.expanduser(export_dir)
				else:
					export_dir = os.path.join(data_dir, "chat-export")
				os.makedirs(export_dir, exist_ok=True)
				output_path = os.path.join(export_dir, f"沟通列表-{today}.{export_fmt}")

			# 显式指定的写入位置由用户负责，这里只展开 ~ 并建好父目录，
			# 与 digest / stats / resume export 共用同一套 -o 语义。
			target = prepare_output_path(output_path)
			with open(target, "w", encoding="utf-8", newline="") as f:
				f.write(content)
			output_path = str(target)
			handle_output(
				ctx, "chat",
				{
					"message": f"已导出 {len(friends)} 条到 {output_path}",
					"count": len(friends),
					"format": export_fmt,
					"path": output_path,
					"diff": diff_result,
				},
				render=lambda d: click.echo(
					f"已导出 {d['count']} 条到 {d['path']}", err=True
				),
					hints={"next_actions": [
						boss_command_for_ctx(ctx, "detail <security_id>", include_data_dir=True),
						boss_command_for_ctx(ctx, "chatmsg <uid>", include_data_dir=True),
				]},
			)
			return

		# ── 普通输出模式 ──────────────────────────────────────────
		# 根据筛选条件动态调整标题
		title = "沟通列表"
		if from_who == "boss":
			title = "对方主动联系"
		elif from_who == "me":
			title = "我主动打招呼"
		if days is not None:
			title += f"（最近 {days} 天）"

		def _render(data: list[dict[str, Any]]) -> None:
			render_simple_list(
				data,
				title,
				[
					("Boss", "name", "bold cyan"),
					("职称", "title", "dim"),
					("公司", "brand_name", "green"),
					("uid", "uid", "dim"),
					("发起方", "initiated_by", "magenta"),
					("未读", "unread", "red"),
					("已读", "msg_status", "dim"),
					("最近消息", "last_msg", "yellow"),
					("时间", "last_time", "dim"),
				],
			)

		# --page 一直存在，但此前不从平台响应推导分页事实：Agent 翻页时既不知道
		# 当前是哪一页，也没有「是否还有下一页」的证据。与 recommend 保持同一套
		# 保守语义：只有平台返回布尔 hasMore 才据此给出下一页命令。
		pagination: dict[str, Any] = {
			"page": page,
			"has_more": has_more,
			"has_more_source": "platform_hasMore" if has_more is not None else "unknown",
			"total": len(friends),
			"total_kind": "returned_page_count",
		}
		next_actions = [
			boss_command_for_ctx(ctx, "detail <security_id>", include_data_dir=True),
			boss_command_for_ctx(ctx, "chatmsg <uid>", include_data_dir=True),
		]
		if has_more is True:
			next_cmd = f"chat --page {page + 1}"
			if from_who:
				next_cmd += f" --from {from_who}"
			if days is not None:
				next_cmd += f" --days {days}"
			next_actions.append(boss_command_for_ctx(ctx, next_cmd, include_data_dir=True))
		hints: dict[str, Any] = {"next_actions": next_actions}
		if has_more is None:
			hints["operator_actions"] = ["平台未提供可靠的下一页标记，不能仅凭本页数量判断是否还有记录"]

		handle_output(
			ctx, "chat", friends,
			render=_render,
			pagination=pagination,
			hints=hints,
		)



# ── 时间格式化 ────────────────────────────────────────────────────


def _format_ts(ts_ms: int) -> str:
	"""将毫秒时间戳格式化为可读日期（使用本地时区）"""
	dt = datetime.datetime.fromtimestamp(ts_ms / 1000, tz=datetime.timezone.utc).astimezone()
	now = datetime.datetime.now(tz=datetime.timezone.utc).astimezone()
	if dt.date() == now.date():
		return dt.strftime("今天 %H:%M")
	delta = (now.date() - dt.date()).days
	if delta == 1:
		return dt.strftime("昨天 %H:%M")
	if delta < 7:
		return f"{delta}天前"
	return dt.strftime("%m-%d %H:%M")


# 向后兼容：旧测试通过 chat._load_snapshot 引用
_load_snapshot = load_snapshot
