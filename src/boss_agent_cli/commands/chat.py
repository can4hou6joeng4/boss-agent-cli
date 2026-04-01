import csv
import datetime
import html as _html
import io
import json
import os
import time

import click

from boss_agent_cli.api.client import BossClient
from boss_agent_cli.auth.manager import AuthManager, AuthRequired, TokenRefreshFailed
from boss_agent_cli.display import handle_error_output, handle_output, render_simple_list

# relationType 映射：API 返回值 → 可读标签
_RELATION_LABELS = {1: "对方主动", 2: "我主动", 3: "投递"}
_FROM_FILTER = {"boss": 1, "me": 2}
_MSG_STATUS_LABELS = {1: "未读", 2: "已读"}

# 已知分组渲染顺序
_GROUP_ORDER = ["对方主动", "我主动", "投递"]


# ── 安全工具 ──────────────────────────────────────────────────────


def _sanitize_csv_cell(value: str) -> str:
	"""防止 CSV 公式注入：以 =+@- 开头的值前置单引号；过滤 Tab 和回车。"""
	if not isinstance(value, str):
		return str(value)
	value = value.replace("\t", " ").replace("\r", "")
	if value and value[0] in ("=", "+", "-", "@"):
		return f"'{value}"
	return value


def _escape_md_cell(value: str) -> str:
	"""转义 Markdown 表格中的危险字符。"""
	if not isinstance(value, str):
		return str(value)
	return value.replace("|", "\\|").replace("\n", " ").replace("\r", "")


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
def chat_cmd(ctx, page, from_who, days, export_fmt, output_path):
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

	try:
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
			diff_result = _save_snapshot_and_diff(snapshot_dir, friends, logger)

			content = _render_export(friends, export_fmt, from_who, days, diff_result)

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

		def _render(data):
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
	except AuthRequired:
		handle_error_output(
			ctx, "chat",
			code="AUTH_REQUIRED",
			message="登录态已失效，请重新登录",
			recoverable=True, recovery_action="boss login",
		)
	except TokenRefreshFailed:
		handle_error_output(
			ctx, "chat",
			code="TOKEN_REFRESH_FAILED",
			message="Token 刷新失败，请重新登录",
			recoverable=True, recovery_action="boss login",
		)
	except Exception as e:
		handle_error_output(
			ctx, "chat",
			code="NETWORK_ERROR",
			message=f"获取沟通列表失败: {e}",
			recoverable=True, recovery_action="重试",
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


# ── L3: 快照 + Diff ──────────────────────────────────────────────


def _save_snapshot_and_diff(
	snapshot_dir: str, friends: list[dict], logger
) -> dict:
	"""保存当日 JSON 快照（按 security_id 合并）并与上次对比。"""
	os.makedirs(snapshot_dir, exist_ok=True)
	today = datetime.date.today().isoformat()
	snapshot_path = os.path.join(snapshot_dir, f"{today}.json")

	# 合并已有的当日快照（解决分页覆盖问题）
	existing = {}
	if os.path.exists(snapshot_path):
		try:
			with open(snapshot_path, encoding="utf-8") as f:
				prev_today = json.load(f)
			if isinstance(prev_today, list):
				for item in prev_today:
					if isinstance(item, dict) and item.get("security_id"):
						existing[item["security_id"]] = item
		except (json.JSONDecodeError, OSError):
			pass

	# 用当前数据更新（新数据优先）
	for item in friends:
		sid = item.get("security_id")
		if sid:
			existing[sid] = item

	merged = list(existing.values())

	# 保存合并后的快照
	with open(snapshot_path, "w", encoding="utf-8") as f:
		json.dump(merged, f, ensure_ascii=False, indent=2)

	# 查找上一次快照
	prev_path = _find_previous_snapshot(snapshot_dir, today)
	if prev_path is None:
		return {"is_first": True, "added": [], "removed": [], "new_unread": []}

	prev_friends = _load_snapshot(prev_path, logger)
	if prev_friends is None:
		return {"is_first": True, "added": [], "removed": [], "new_unread": []}

	# 用 security_id 做 key 对比
	curr_map = {item["security_id"]: item for item in merged if item.get("security_id")}
	prev_map = {item["security_id"]: item for item in prev_friends if item.get("security_id")}
	curr_ids = set(curr_map)
	prev_ids = set(prev_map)

	added = [curr_map[sid] for sid in (curr_ids - prev_ids)]
	removed = [prev_map[sid] for sid in (prev_ids - curr_ids)]

	# 新增未读：检测任何未读增量
	new_unread = []
	for sid in (curr_ids & prev_ids):
		prev_unread = prev_map[sid].get("unread", 0) or 0
		curr_unread = curr_map[sid].get("unread", 0) or 0
		if curr_unread > prev_unread:
			new_unread.append(curr_map[sid])

	return {
		"is_first": False,
		"prev_date": os.path.basename(prev_path).replace(".json", ""),
		"added": added,
		"removed": removed,
		"new_unread": new_unread,
	}


def _load_snapshot(path: str, logger) -> list[dict] | None:
	"""加载并校验快照文件，返回 None 表示不可用。"""
	try:
		with open(path, encoding="utf-8") as f:
			data = json.load(f)
	except (json.JSONDecodeError, OSError):
		logger.warning(f"无法读取快照: {path}")
		return None

	if not isinstance(data, list):
		logger.warning(f"快照格式异常（非数组）: {path}")
		return None

	# 过滤掉非 dict 项
	return [item for item in data if isinstance(item, dict)]


def _find_previous_snapshot(snapshot_dir: str, today: str) -> str | None:
	"""找到 today 之前最近的一份快照文件路径。"""
	snapshots = []
	for fname in os.listdir(snapshot_dir):
		if fname.endswith(".json") and fname[:10] != today:
			snapshots.append(fname)
	if not snapshots:
		return None
	snapshots.sort(reverse=True)
	return os.path.join(snapshot_dir, snapshots[0])


# ── L2: 导出渲染 ─────────────────────────────────────────────────


def _prepare_render_data(
	friends: list[dict],
	from_who: str | None,
	diff_result: dict,
) -> dict:
	"""公共数据准备：分组、统计、diff 标记、渲染顺序。供 MD/HTML 共用。"""
	now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

	# 按 relationType 分组
	groups: dict[str, list[dict]] = {}
	for item in friends:
		key = item.get("initiated_by", "未知")
		groups.setdefault(key, []).append(item)

	total = len(friends)
	counts = {k: len(v) for k, v in groups.items()}
	count_parts = [f"{k} {v}" for k, v in counts.items()]

	me_items = groups.get("我主动", [])
	me_read = sum(1 for x in me_items if x.get("msg_status") == "已读")
	me_unread = len(me_items) - me_read

	added_ids = {item.get("security_id") for item in diff_result.get("added", [])}

	# diff 摘要
	diff_summary = None
	if not diff_result.get("is_first", True):
		prev_date = diff_result.get("prev_date", "?")
		parts = []
		added_count = len(diff_result.get("added", []))
		removed_count = len(diff_result.get("removed", []))
		new_unread_count = len(diff_result.get("new_unread", []))
		if added_count:
			parts.append(f"新增 {added_count} 条")
		if removed_count:
			parts.append(f"消失 {removed_count} 条")
		if new_unread_count:
			parts.append(f"新消息 {new_unread_count} 条")
		diff_summary = {
			"prev_date": prev_date,
			"change": "，".join(parts) if parts else "无变化",
		}

	# 构建渲染顺序
	render_order = list(_GROUP_ORDER)
	for key in groups:
		if key not in render_order:
			render_order.append(key)

	# 按顺序生成分组数据 + 全局编号
	sections = []
	id_map: list[tuple[str, str, str]] = []
	global_idx = 0

	for group_key in render_order:
		group_items = groups.get(group_key)
		if group_items is None:
			continue
		if from_who == "boss" and group_key != "对方主动":
			continue
		if from_who == "me" and group_key != "我主动":
			continue

		subtitle = f"{group_key}（{len(group_items)} 条"
		if group_key == "我主动":
			subtitle += f" · 已读 {me_read} / 未读 {me_unread}"
		subtitle += "）"

		rows = []
		for item in group_items:
			global_idx += 1
			sid = item.get("security_id", "")
			is_new = sid in added_ids
			ref = f"S{global_idx}"
			msg = str(item.get("last_msg") or "-")
			unread = item.get("unread") or 0
			rows.append({
				"ref": ref, "is_new": is_new, "sid": sid,
				"brand_name": item.get("brand_name") or "-",
				"name": item.get("name") or "-",
				"title": item.get("title") or "-",
				"last_time": item.get("last_time") or "-",
				"unread": unread,
				"msg_status": item.get("msg_status") or "-",
				"last_msg": msg,
			})
			id_map.append((ref, sid, f"{item.get('brand_name') or '-'} {item.get('name') or '-'}"))

		sections.append({"subtitle": subtitle, "rows": rows})

	return {
		"now_str": now_str,
		"total": total,
		"count_parts": count_parts,
		"diff_summary": diff_summary,
		"sections": sections,
		"id_map": id_map,
		"removed": diff_result.get("removed", []),
	}


def _render_export(
	friends: list[dict],
	fmt: str,
	from_who: str | None,
	days: int | None,
	diff_result: dict,
) -> str:
	"""将沟通列表渲染为指定格式的字符串。"""
	if fmt == "json":
		return json.dumps(friends, ensure_ascii=False, indent=2)
	if fmt == "csv":
		return _render_csv(friends)
	if fmt == "html":
		return _render_html(friends, from_who, days, diff_result)
	return _render_markdown(friends, from_who, days, diff_result)


def _render_csv(friends: list[dict]) -> str:
	"""渲染为 CSV 格式（含公式注入防护）。"""
	if not friends:
		return ""
	fields = [
		"name", "title", "brand_name", "initiated_by",
		"msg_status", "unread", "last_msg", "last_time",
		"security_id", "encrypt_job_id",
	]
	buf = io.StringIO(newline="")
	writer = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore")
	writer.writeheader()
	for item in friends:
		safe_row = {k: _sanitize_csv_cell(str(v)) for k, v in item.items()}
		writer.writerow(safe_row)
	return buf.getvalue()


def _render_markdown(
	friends: list[dict],
	from_who: str | None,
	days: int | None,
	diff_result: dict,
) -> str:
	"""渲染为分组 Markdown 格式，含摘要和 diff 标记。"""
	rd = _prepare_render_data(friends, from_who, diff_result)

	lines = [
		"# BOSS 直聘沟通列表",
		"",
		f"> 生成时间：{rd['now_str']}  ",
		f"> 总计：{rd['total']} 条（{' / '.join(rd['count_parts'])}）",
	]

	if rd["diff_summary"]:
		ds = rd["diff_summary"]
		lines.append(f"> 较上次（{ds['prev_date']}）变化：{ds['change']}")

	lines.append("")

	for section in rd["sections"]:
		lines.append(f"## {section['subtitle']}")
		lines.append("")
		lines.append("| # | 公司 | 联系人 | 职称 | 时间 | 未读 | 已读 | 最近消息 |")
		lines.append("|---|------|--------|------|------|------|------|----------|")

		for row in section["rows"]:
			prefix = "NEW " if row["is_new"] else ""
			msg = row["last_msg"]
			if len(msg) > 40:
				msg = msg[:40] + "…"
			unread_str = str(row["unread"]) if row["unread"] > 0 else ""
			lines.append(
				f"| {prefix}{row['ref']} | {_escape_md_cell(row['brand_name'])} "
				f"| {_escape_md_cell(row['name'])} "
				f"| {_escape_md_cell(row['title'])} "
				f"| {_escape_md_cell(row['last_time'])} | {unread_str} "
				f"| {_escape_md_cell(row['msg_status'])} "
				f"| {_escape_md_cell(msg)} |"
			)
		lines.append("")

	if rd["removed"]:
		lines.append("## 已消失（较上次）")
		lines.append("")
		lines.append("| 公司 | 联系人 | 上次时间 |")
		lines.append("|------|--------|----------|")
		for item in rd["removed"]:
			lines.append(
				f"| {_escape_md_cell(item.get('brand_name') or '-')} "
				f"| {_escape_md_cell(item.get('name') or '-')} "
				f"| {_escape_md_cell(item.get('last_time') or '-')} |"
			)
		lines.append("")

	if rd["id_map"]:
		lines.append("<details>")
		lines.append("<summary>security_id 映射表（点击展开）</summary>")
		lines.append("")
		lines.append("| 编号 | 公司/联系人 | security_id |")
		lines.append("|------|------------|-------------|")
		for ref, sid, label in rd["id_map"]:
			lines.append(f"| {ref} | {label} | {sid} |")
		lines.append("")
		lines.append("</details>")
		lines.append("")

	return "\n".join(lines) + "\n"


def _render_html(
	friends: list[dict],
	from_who: str | None,
	days: int | None,
	diff_result: dict,
) -> str:
	"""渲染为 HTML 格式，含分组、diff 标记和 security_id 映射。"""
	esc = _html.escape
	rd = _prepare_render_data(friends, from_who, diff_result)

	# diff 摘要
	diff_html = ""
	if rd["diff_summary"]:
		ds = rd["diff_summary"]
		diff_html = f"<div class='diff'>较上次（{esc(ds['prev_date'])}）变化：{esc(ds['change'])}</div>"

	sections_html = []
	for section in rd["sections"]:
		rows = []
		for row in section["rows"]:
			badge = '<span class="badge-new">NEW</span> ' if row["is_new"] else ""
			msg = row["last_msg"]
			if len(msg) > 60:
				msg = msg[:60] + "…"
			unread_str = f'<span class="unread">{row["unread"]}</span>' if row["unread"] > 0 else ""
			rows.append(
				f"<tr>"
				f"<td>{badge}{row['ref']}</td>"
				f"<td class='company'>{esc(row['brand_name'])}</td>"
				f"<td>{esc(row['name'])}</td>"
				f"<td class='dim'>{esc(row['title'])}</td>"
				f"<td class='dim'>{esc(row['last_time'])}</td>"
				f"<td>{unread_str}</td>"
				f"<td class='dim'>{esc(row['msg_status'])}</td>"
				f"<td class='msg'>{esc(msg)}</td>"
				f"</tr>"
			)
		sections_html.append(f"""
		<h2>{esc(section['subtitle'])}</h2>
		<table>
			<thead><tr>
				<th>#</th><th>公司</th><th>联系人</th><th>职称</th>
				<th>时间</th><th>未读</th><th>已读</th><th>最近消息</th>
			</tr></thead>
			<tbody>{''.join(rows)}</tbody>
		</table>""")

	# 消失的条目
	removed_html = ""
	if rd["removed"]:
		rrows = []
		for item in rd["removed"]:
			rrows.append(
				f"<tr>"
				f"<td>{esc(item.get('brand_name') or '-')}</td>"
				f"<td>{esc(item.get('name') or '-')}</td>"
				f"<td class='dim'>{esc(item.get('last_time') or '-')}</td>"
				f"</tr>"
			)
		removed_html = f"""
		<h2>已消失（较上次）</h2>
		<table>
			<thead><tr><th>公司</th><th>联系人</th><th>上次时间</th></tr></thead>
			<tbody>{''.join(rrows)}</tbody>
		</table>"""

	# security_id 映射表
	map_rows = []
	for ref, sid, label in rd["id_map"]:
		map_rows.append(f"<tr><td>{ref}</td><td>{esc(label)}</td><td class='sid'>{esc(sid)}</td></tr>")
	map_html = f"""
	<details>
		<summary>security_id 映射表（点击展开）</summary>
		<table>
			<thead><tr><th>编号</th><th>公司/联系人</th><th>security_id</th></tr></thead>
			<tbody>{''.join(map_rows)}</tbody>
		</table>
	</details>""" if map_rows else ""

	return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>BOSS 直聘沟通列表 · {esc(rd['now_str'])}</title>
<style>
  :root {{ --green: #00b38a; --bg: #f8f9fa; }}
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ font-family: -apple-system, "PingFang SC", "Helvetica Neue", sans-serif;
         background: var(--bg); color: #333; line-height: 1.6; padding: 20px; max-width: 960px; margin: 0 auto; }}
  h1 {{ text-align: center; font-size: 20px; margin-bottom: 4px; }}
  .sub {{ text-align: center; color: #888; font-size: 13px; margin-bottom: 6px; }}
  .diff {{ text-align: center; color: #e65100; font-size: 12px; margin-bottom: 16px; }}
  h2 {{ font-size: 15px; color: #1a1a1a; margin: 20px 0 8px; padding-left: 8px;
       border-left: 3px solid var(--green); }}
  table {{ width: 100%; border-collapse: collapse; font-size: 13px; margin-bottom: 12px; }}
  th {{ background: #f0f0f0; font-weight: 600; text-align: left; padding: 6px 8px; white-space: nowrap; }}
  td {{ padding: 6px 8px; border-bottom: 1px solid #eee; }}
  tr:hover {{ background: #f5faf8; }}
  .company {{ color: var(--green); font-weight: 600; }}
  .dim {{ color: #888; }}
  .msg {{ max-width: 300px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: #555; }}
  .unread {{ background: #ff4d4f; color: #fff; border-radius: 8px; padding: 1px 6px; font-size: 11px; }}
  .badge-new {{ background: var(--green); color: #fff; border-radius: 4px; padding: 1px 5px; font-size: 10px; font-weight: 600; }}
  .sid {{ font-size: 10px; color: #999; word-break: break-all; max-width: 300px; }}
  details {{ margin-top: 16px; }}
  summary {{ cursor: pointer; color: #666; font-size: 13px; }}
  @media (max-width: 700px) {{
    table {{ font-size: 12px; }}
    .msg {{ max-width: 160px; }}
  }}
</style></head><body>
<h1>BOSS 直聘沟通列表</h1>
<div class="sub">生成时间：{esc(rd['now_str'])} · 总计：{rd['total']} 条（{esc(' / '.join(rd['count_parts']))}）</div>
{diff_html}
{''.join(sections_html)}
{removed_html}
{map_html}
</body></html>
"""
