"""Chat export — 渲染沟通列表为 MD/CSV/JSON/HTML 格式。"""

import csv
import datetime
import html as _html
import io
import json
from typing import Any

from boss_agent_cli.commands.chat_utils import sanitize_csv_cell, escape_md_cell, GROUP_ORDER


def prepare_render_data(
	friends: list[dict[str, Any]],
	from_who: str | None,
	diff_result: dict[str, Any],
) -> dict[str, Any]:
	"""公共数据准备：分组、统计、diff 标记、渲染顺序。供 MD/HTML 共用。"""
	now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

	# 按 relationType 分组
	groups: dict[str, list[dict[str, Any]]] = {}
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
	render_order = list(GROUP_ORDER)
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


def render_export(
	friends: list[dict[str, Any]],
	fmt: str,
	from_who: str | None,
	days: int | None,
	diff_result: dict[str, Any],
) -> str:
	"""将沟通列表渲染为指定格式的字符串。"""
	if fmt == "json":
		return json.dumps(friends, ensure_ascii=False, indent=2)
	if fmt == "csv":
		return _render_csv(friends)
	if fmt == "html":
		return _render_html(friends, from_who, days, diff_result)
	return _render_markdown(friends, from_who, days, diff_result)


def _render_csv(friends: list[dict[str, Any]]) -> str:
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
		safe_row = {k: sanitize_csv_cell(str(v)) for k, v in item.items()}
		writer.writerow(safe_row)
	return buf.getvalue()


def _render_markdown(
	friends: list[dict[str, Any]],
	from_who: str | None,
	days: int | None,
	diff_result: dict[str, Any],
) -> str:
	"""渲染为分组 Markdown 格式，含摘要和 diff 标记。"""
	rd = prepare_render_data(friends, from_who, diff_result)

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
				f"| {prefix}{row['ref']} | {escape_md_cell(row['brand_name'])} "
				f"| {escape_md_cell(row['name'])} "
				f"| {escape_md_cell(row['title'])} "
				f"| {escape_md_cell(row['last_time'])} | {unread_str} "
				f"| {escape_md_cell(row['msg_status'])} "
				f"| {escape_md_cell(msg)} |"
			)
		lines.append("")

	if rd["removed"]:
		lines.append("## 已消失（较上次）")
		lines.append("")
		lines.append("| 公司 | 联系人 | 上次时间 |")
		lines.append("|------|--------|----------|")
		for item in rd["removed"]:
			lines.append(
				f"| {escape_md_cell(item.get('brand_name') or '-')} "
				f"| {escape_md_cell(item.get('name') or '-')} "
				f"| {escape_md_cell(item.get('last_time') or '-')} |"
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
	friends: list[dict[str, Any]],
	from_who: str | None,
	days: int | None,
	diff_result: dict[str, Any],
) -> str:
	"""渲染为 HTML 格式，含分组、diff 标记和 security_id 映射。"""
	esc = _html.escape
	rd = prepare_render_data(friends, from_who, diff_result)

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
