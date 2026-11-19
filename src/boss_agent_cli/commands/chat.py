import datetime
import os
from typing import Any

import click

from boss_agent_cli.auth.manager import AuthManager
from boss_agent_cli.compliance import require_compliance_allowed
from boss_agent_cli.commands._platform import get_platform_instance
from boss_agent_cli.commands._output_path import validate_output_path_or_emit
from boss_agent_cli.services.chat_export import render_export
from boss_agent_cli.services.friend_list_pages import friend_list_page_items
from boss_agent_cli.services.output_path import prepare_output_path
from boss_agent_cli.services.text_files import write_text_atomic
from boss_agent_cli.services.chat_snapshot import save_snapshot_and_diff, load_snapshot
from boss_agent_cli.services.chat_utils import (
	RELATION_LABELS, FROM_FILTER, MSG_STATUS_LABELS,
	contact_identity, int_code, safe_local_time, sanitize_csv_cell, escape_md_cell,
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
	help="输出文件路径，仅在 --export 时生效（只给 -o 会返回 INVALID_PARAM，显式空串无效）；省略时保存到配置的 export_dir，未配置时使用 <data-dir>/chat-export")
@click.pass_context
@handle_auth_errors("chat")
def chat_cmd(ctx: click.Context, page: int, from_who: str | None, days: int | None, export_fmt: str | None, output_path: str | None) -> None:
	"""查看沟通列表（支持按发起方、时间筛选，支持导出）"""
	if not require_compliance_allowed(ctx, "chat"):
		ctx.exit(1)

	# `-o` 只在导出分支里生效：没配 `--export` 时路径被静默忽略却仍返回
	# ok:true，调用方以为文件已经写出（Issue #1502）。入口直接拒绝。
	if output_path is not None and export_fmt is None:
		list_cmd = boss_command_for_ctx(ctx, "chat", include_data_dir=True)
		handle_error_output(
			ctx,
			"chat",
			code="INVALID_PARAM",
			message="-o / --output 需要配合 --export 使用；不导出时请省略 -o",
			recovery_action=(
				f"加上 --export md（或 html/csv/json）后再指定 -o，例如 {list_cmd} --export md -o <文件路径>；"
				"只想查看沟通列表时省略 -o"
			),
			hints={"next_actions": [list_cmd]},
		)
		return

	data_dir = ctx.obj["data_dir"]
	# 先确定导出路径，再做只读预检，避免因无效的本地路径消耗请求预算或更新快照。
	if export_fmt is not None:
		if output_path is None:
			export_dir = ctx.obj.get("config", {}).get("export_dir")
			if export_dir is not None and not isinstance(export_dir, str):
				handle_error_output(
					ctx,
					"chat",
					code="CONFIG_INVALID",
					message=f"export_dir 必须是目录路径字符串或 null，收到 {export_dir!r}",
					recoverable=True,
					recovery_action=boss_command_for_ctx(ctx, "config reset export_dir", include_data_dir=True),
				)
				return
			today = datetime.date.today().isoformat()
			export_dir = export_dir or os.path.join(data_dir, "chat-export")
			output_path = os.path.join(export_dir, f"沟通列表-{today}.{export_fmt}")
		# 显式空串应报参数错误（Issue #1501）。
		elif not output_path.strip():
			chat_export_cmd = boss_command_for_ctx(ctx, f"chat --export {export_fmt}", include_data_dir=True)
			handle_error_output(
				ctx,
				"chat",
				code="INVALID_PARAM",
				message="-o / --output 不能为空；省略 -o 才会自动保存到默认导出目录",
				recovery_action=f"给出完整文件路径后重试，例如 {chat_export_cmd} -o <文件路径>",
				hints={"next_actions": [chat_export_cmd]},
			)
			return
		validated = validate_output_path_or_emit(ctx, "chat", output_path)
		if validated is None:
			return
		output_path = validated

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
		platform_data = platform.unwrap_data(resp)
		if not isinstance(platform_data, dict):
			platform_data = {}
		# 与 services/friend_list_pages 同一口径：元素级漂移只跳过坏元素（Issue #1141），
		# result 是真值非列表时回退 friendList（Issue #1351）。
		items = friend_list_page_items(platform_data)

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
			# lastMessageInfo 是对象；平台字段漂移成非空字符串 / 数组 / 数字时
			# `.get` 会抛 AttributeError，状态的缺失语义本就是「未知」，按缺失处理
			# （Issue #1317）。
			last_message_info = item.get("lastMessageInfo")
			if not isinstance(last_message_info, dict):
				last_message_info = {}
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
			# 快照里的 last_ts 只认真正的整数：旧格式 / 手工改过的快照可能是
			# 字符串，与 float 比较会抛 TypeError 打断整次导出（Issue #1317）。
			# 与 chat_snapshot._unread_count 的 unread 同口径，漂移值按 0（未知）处理。
			last_ts = friend.get("last_ts")
			if type(last_ts) is not int:
				last_ts = 0
			return (
				(not cutoff_ts or not last_ts or last_ts >= cutoff_ts)
				and (relation_label is None or friend.get("initiated_by", "未知") == relation_label)
			)

		friends = [friend for friend in all_friends if _matches_filter(friend)]

		raw_has_more = platform_data.get("hasMore")
		has_more = raw_has_more if isinstance(raw_has_more, bool) else None
		# 只有第 1 页且平台明确没有下一页，才能证明本次看到完整沟通列表；
		# 部分页或未知 hasMore 不能作为“联系人已消失”的证据。页面还必须带有
		# 可识别的列表容器：容器缺失 / 类型漂移时不能把「读不出任何元素」当成
		# 完整空列表，否则会用完整采集清空当日快照并把旧联系人全报为已消失
		# （Issue #1914）。
		page_containers_valid = any(
			isinstance(platform_data.get(key), list) for key in ("result", "friendList")
		)
		collection_complete = page == 1 and has_more is False and page_containers_valid

		# ── 导出模式 ──────────────────────────────────────────────
		if export_fmt is not None and output_path is not None:
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

			# 实际写出时才创建父目录。
			target = prepare_output_path(output_path)
			write_text_atomic(target, content, newline="")
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
	dt = safe_local_time(ts_ms, tz=datetime.timezone.utc)
	if dt is None:
		# 越界 / 非有限时间戳只是展示信息：按「无时间」输出，不让整条命令
		# 落成可重试的 NETWORK_ERROR（Issue #1295）。
		return "-"
	dt = dt.astimezone()
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
