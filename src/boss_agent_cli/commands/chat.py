import datetime
import os
from typing import Any

import click

from boss_agent_cli.api.client import BossClient
from boss_agent_cli.auth.manager import AuthManager
from boss_agent_cli.commands.chat_export import render_export
from boss_agent_cli.commands.chat_snapshot import save_snapshot_and_diff, load_snapshot
from boss_agent_cli.commands.chat_utils import (
	RELATION_LABELS, FROM_FILTER, MSG_STATUS_LABELS,
	sanitize_csv_cell, escape_md_cell,
)
from boss_agent_cli.display import handle_auth_errors, handle_error_output, handle_output, render_simple_list

# 向后兼容别名（旧测试引用 chat._sanitize_csv_cell 等）
_RELATION_LABELS = RELATION_LABELS
_FROM_FILTER = FROM_FILTER
_MSG_STATUS_LABELS = MSG_STATUS_LABELS
_GROUP_ORDER = ["对方主动", "我主动", "投递"]
_sanitize_csv_cell = sanitize_csv_cell
_escape_md_cell = escape_md_cell


@click.command("chat")
@click.option("--page", default=1, help="页码")
@click.option("--from", "from_who", default=None, type=click.Choice(["boss", "me"]),
	help="筛选发起方：boss=对方主动联系 / me=我主动打招呼")
@click.option("--days", default=None, type=int, help="只显示最近 N 天的记录")
@click.option("--export", "export_fmt", default=None,
	type=click.Choice(["html", "md", "csv", "json"]),
	help="导出格式：html=HTML / md=Markdown / csv=CSV / json=JSON")
@click.option("-o", "--output", "output_path", default=None,
	help="输出文件路径（不指定则自动保存到配置的 export_dir）")
@click.pass_context
@handle_auth_errors("chat")
def chat_cmd(ctx: click.Context, page: int, from_who: str | None, days: int | None, export_fmt: str | None, output_path: str | None) -> None:
	"""查看沟通列表（支持按发起方、时间筛选，支持导出）"""
	data_dir = ctx.obj["data_dir"]
	logger = ctx.obj["logger"]
	delay = ctx.obj["delay"]
	cdp_url = ctx.obj.get("cdp_url")
	auth = AuthManager(data_dir, logger=logger)

	token = auth.check_status()
	if token is None:
		handle_error_output(
			ctx, "chat",
			code="AUTH_REQUIRED",
			message="未登录，请先执行 boss login",
			recoverable=True, recovery_action="boss login",
		)
		return

	with BossClient(auth, delay=delay, cdp_url=cdp_url) as client:
		resp = client.friend_list(page=page)
		zp_data = resp.get("zpData", {})
		items = zp_data.get("result") or zp_data.get("friendList") or []

		# 时间筛选阈值
		cutoff_ts = None
		if days is not None:
			cutoff = datetime.datetime.now() - datetime.timedelta(days=days)
			cutoff_ts = cutoff.timestamp() * 1000

		# 发起方筛选值
		relation_filter = _FROM_FILTER.get(from_who) if from_who else None

		friends = []
		for item in items:
			# 时间筛选
			last_ts = item.get("lastTS", 0)
			if cutoff_ts and last_ts and last_ts < cutoff_ts:
				continue

			# 发起方筛选
			relation_type = item.get("relationType")
			if relation_filter is not None and relation_type != relation_filter:
				continue

			if last_ts:
				last_time_str = _format_ts(last_ts)
			else:
				last_time_str = item.get("lastTime", "-")

			friends.append({
				"name": item.get("name") or "-",
				"title": item.get("title") or "-",
				"brand_name": item.get("brandName") or "-",
				"initiated_by": _RELATION_LABELS.get(relation_type, "未知"),
				"last_msg": item.get("lastMsg") or "-",
				"last_time": last_time_str,
				"last_ts": last_ts,
				"msg_status": _MSG_STATUS_LABELS.get(
					item.get("lastMessageInfo", {}).get("status"), "未知"
				),
				"security_id": item.get("securityId") or "",
				"encrypt_job_id": item.get("encryptJobId") or "",
				"unread": item.get("unreadMsgCount") or 0,
			})

		# ── 导出模式 ──────────────────────────────────────────────
		if export_fmt:
			# L3: 保存 JSON 快照 + diff
			snapshot_dir = os.path.join(data_dir, "chat-history")
			diff_result = save_snapshot_and_diff(snapshot_dir, friends, logger)

			content = render_export(friends, export_fmt, from_who, days, diff_result)

			# 未指定 -o 时，自动生成默认路径（日期命名，同天覆盖）
			if not output_path:
				today = datetime.date.today().isoformat()
				# 优先读 config 中的 export_dir，否则 fallback 到 data_dir/chat-export
				export_dir = ctx.obj.get("config", {}).get("export_dir")
				if export_dir:
					export_dir = os.path.expanduser(export_dir)
				else:
					export_dir = os.path.join(data_dir, "chat-export")
				os.makedirs(export_dir, exist_ok=True)
				output_path = os.path.join(export_dir, f"沟通列表-{today}.{export_fmt}")

			# 路径安全校验：禁止 .. 跳转
			resolved = os.path.realpath(output_path)
			if ".." in os.path.relpath(resolved, os.getcwd()):
				safe_dir = os.path.realpath(export_dir if 'export_dir' in dir() else data_dir)
				if not resolved.startswith(safe_dir) and not resolved.startswith(os.path.realpath(os.getcwd())):
					handle_error_output(
						ctx, "chat", code="INVALID_PARAM",
						message=f"输出路径不安全: {output_path}",
					)
					return

			os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
			with open(output_path, "w", encoding="utf-8", newline="") as f:
				f.write(content)
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
					"boss detail <security_id> — 查看职位详情",
					"boss greet <security_id> <job_id> — 打招呼",
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
					("发起方", "initiated_by", "magenta"),
					("未读", "unread", "red"),
					("已读", "msg_status", "dim"),
					("最近消息", "last_msg", "yellow"),
					("时间", "last_time", "dim"),
				],
			)

		handle_output(
			ctx, "chat", friends,
			render=_render,
			hints={"next_actions": [
				"boss detail <security_id> — 查看职位详情",
				"boss greet <security_id> <job_id> — 打招呼",
			]},
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
