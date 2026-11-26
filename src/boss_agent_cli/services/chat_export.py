"""Chat export — 渲染沟通列表为 MD/CSV/JSON/HTML 格式。"""

import csv
import datetime
import html as _html
import io
import math
from typing import Any

from boss_agent_cli.api.models import safe_text
from boss_agent_cli.services.chat_utils import sanitize_csv_cell, escape_md_cell, contact_identity, GROUP_ORDER
from boss_agent_cli.services.json_files import (
	degrade_unserializable_records,
	JsonSerializationError,
	strict_json_text,
)
from boss_agent_cli.services.text_files import sanitize_surrogates

_DEGRADED_EXPORT_MESSAGE = "该联系人包含无法序列化的字段（如嵌套过深），已省略问题字段"


def _sanitize_surrogates(text: str) -> str:
	"""兼容旧调用点：实现已收敛到 services.text_files（Issue #2017、#2109）。"""
	return sanitize_surrogates(text)


def _text_cell(value: Any) -> str:
	"""文本格式里的容器值序列化成 JSON 文本，不落 Python repr（Issue #1853）。

	非有限 float 与 JSON 分支同口径按空值处理；容器先递归归一，避免嵌套
	Infinity / NaN 被写成非标准 JSON 字面量（Issue #1976）。null 与标量
	统一转成文本：裸 ``str()`` 对超过 4300 位的整数抛 ``ValueError``，
	会中止整份导出；与 ``job_export._cell_text`` / ``job_card._text_item``
	同口径按缺失处理（Issue #2162）。
	"""
	if value is None:
		return ""
	if isinstance(value, bool):
		# JSON 分支是 true / false：str(True) 会让文本导出出现 Python 字面量
		# （Issue #2018）。
		return "true" if value else "false"
	if isinstance(value, (dict, list, tuple)):
		try:
			return _sanitize_surrogates(strict_json_text(value, indent=None))
		except OSError:
			# 深嵌套 / 超长整数无法序列化：单个坏单元格按缺失处理，不让整份
			# 导出变成可重试的错误信封（Issue #2016）。
			return ""
	if isinstance(value, float) and not math.isfinite(value):
		return ""
	return _sanitize_surrogates(safe_text(value))


def _diff_items(diff_result: dict[str, Any], key: str) -> list[dict[str, Any]]:
	"""diff 列表的防御读取：缺失 / null / 非列表按空处理，非对象元素跳过。

	``save_snapshot_and_diff`` 正常只返回对象列表，但渲染入口是公共边界：
	坏条目不能让整份导出落成 AttributeError / TypeError（Issue #2162）。
	"""
	raw = diff_result.get(key)
	if not isinstance(raw, list):
		return []
	return [item for item in raw if isinstance(item, dict)]


def prepare_render_data(
	friends: list[dict[str, Any]],
	from_who: str | None,
	diff_result: dict[str, Any],
) -> dict[str, Any]:
	"""公共数据准备：分组、统计、diff 标记、渲染顺序。供 MD/HTML 共用。"""
	# 公共入口可能收到 None / 非对象：直接 ``.get`` 会落成 AttributeError，
	# 整份导出变成 INTERNAL_ERROR（Issue #2321）。
	if not isinstance(diff_result, dict):
		diff_result = {}
	now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

	# 非对象元素在渲染入口跳过：公共边界上的坏条目不能让整份导出落成
	# AttributeError（Issue #2162）。CLI 调用方目前都已过滤，这里是纵深防御。
	usable = [item for item in friends if isinstance(item, dict)]

	# 按 relationType 分组。身份用原始值计算并随展示项一起保存：_text_cell 会把
	# 布尔 / 容器 uid 转成看似合法的文本（"true" / JSON），转换后再取身份会
	# 绕过 stable_uid 的 fail-closed，映射表 key 与 added_ids 对不上
	# （Issue #2064、#2110）。
	groups: dict[str, list[tuple[dict[str, Any], str, dict[str, Any]]]] = {}
	for raw_item in usable:
		item = {key: _text_cell(value) for key, value in raw_item.items()}
		key = item.get("initiated_by", "未知")
		groups.setdefault(key, []).append((item, contact_identity(raw_item), raw_item))

	# from_who 决定最终可见分组：总计 / 分组统计必须与 sections 同源，否则
	# 直接传入全部好友再按方向过滤时会出现「总计 2 条」却只渲染 1 行
	# （Issue #2392）。未知 from_who 与 sections 的条件一致，保持全部可见。
	visible_keys = {
		key
		for key in groups
		if not (from_who == "boss" and key != "对方主动") and not (from_who == "me" and key != "我主动")
	}
	total = sum(len(groups[key]) for key in visible_keys)
	counts = {k: len(groups[k]) for k in groups if k in visible_keys}
	count_parts = [f"{k} {v}" for k, v in counts.items()]

	me_items = groups.get("我主动", [])
	me_read = sum(1 for x, _, _ in me_items if x.get("msg_status") == "已读")
	# 状态缺失或漂移时命令层给的是「未知」，不能算进未读（Issue #1794）。
	me_unread = sum(1 for x, _, _ in me_items if x.get("msg_status") == "未读")

	added = _diff_items(diff_result, "added")
	added_ids = {contact_identity(item) for item in added}

	# diff 摘要
	diff_summary = None
	if not diff_result.get("is_first", True):
		prev_date = diff_result.get("prev_date", "?")
		parts = []
		added_count = len(added)
		removed_count = len(_diff_items(diff_result, "removed"))
		new_unread_count = len(_diff_items(diff_result, "new_unread"))
		if added_count:
			parts.append(f"新增 {added_count} 条")
		if removed_count:
			parts.append(f"消失 {removed_count} 条")
		if new_unread_count:
			parts.append(f"新消息 {new_unread_count} 条")
		if diff_result.get("complete") is False:
			# 部分页或筛选结果缺少“谁不在列表里”的证据，不能显示“无变化”
			# 让读者以为已确认联系人仍在（Issue #911）。
			parts.append("本次采集不完整，未判断消失")
		if diff_result.get("baseline_complete") is False:
			# 上一份基线不完整时同样不能宣称新增（Issue #1852）。
			parts.append("上次采集不完整，未判断新增")
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
		for item, identity, raw_item in group_items:
			global_idx += 1
			sid = item.get("security_id", "")
			key = identity
			is_new = key in added_ids
			ref = f"S{global_idx}"
			msg = str(item.get("last_msg") or "-")
			# unread 只认真正的整数（与 chat_snapshot._unread_count 同口径）：
			# 字符串 / 容器会在 ``row["unread"] > 0`` 抛 TypeError（Issue #1853）。
			# 判断必须用原始值：_text_cell 已把标量转成字符串，转换后的 "1"
			# 会被 ``type(...) is int`` 误判为缺失（Issue #2162）。
			unread_value = raw_item.get("unread")
			# 超大整数（>4300 位十进制）在 md/html 的 str()/f-string 里抛裸
			# ValueError，整份导出会落成无信封的 traceback；与 safe_text 同口径
			# 按缺失处理（Issue #2343）。负数没有平台语义，快照 diff 已按 0
			# 处理，导出也必须一致，不能只在 CSV 里写出 "-1"（Issue #2886）。
			unread = (
				unread_value
				if type(unread_value) is int and unread_value >= 0 and safe_text(unread_value)
				else 0
			)
			rows.append({
				"ref": ref, "is_new": is_new, "sid": sid, "key": key,
				"brand_name": item.get("brand_name") or "-",
				"name": item.get("name") or "-",
				"title": item.get("title") or "-",
				"last_time": item.get("last_time") or "-",
				"unread": unread,
				"msg_status": item.get("msg_status") or "-",
				"last_msg": msg,
			})
			id_map.append((ref, key, f"{item.get('brand_name') or '-'} {item.get('name') or '-'}"))

		sections.append({"subtitle": subtitle, "rows": rows})

	# 「已消失」表格与活动联系人 / CSV 行同口径：容器字段序列化为 JSON 文本，
	# 不再在 Markdown / HTML 里落 Python repr（Issue #1913）。非对象元素在
	# 归一阶段丢弃，HTML 通道的 ``item.get`` 不会因坏条目抛 AttributeError
	# （Issue #2162）。
	removed = [{key: _text_cell(value) for key, value in item.items()} for item in _diff_items(diff_result, "removed")]

	return {
		"now_str": now_str,
		"total": total,
		"count_parts": count_parts,
		"diff_summary": diff_summary,
		"sections": sections,
		"id_map": id_map,
		"removed": removed,
	}


def render_export(
	friends: list[dict[str, Any]],
	fmt: str,
	from_who: str | None,
	days: int | None,
	diff_result: dict[str, Any],
) -> str:
	"""将沟通列表渲染为指定格式的字符串。

	返回值必须可按严格 UTF-8 编码：平台 JSON 的 ``\\ud800`` 会绕过文本单元格
	清洗（映射表 key 与 diff 的 ``prev_date`` 直接来自原始平台字段 / 快照），
	md / html 同样要在出口统一归一，与 JSON 分支同口径（Issue #2321 / #2496）。
	"""
	if fmt == "json":
		# 严格 JSON：平台数值漂移（NaN / Infinity）归一为 null，
		# 不让 jq 等严格解析器拒绝整份导出（Issue #1851）。
		# 非对象条目与其他格式一样跳过：不能因为列表里恰好有另一条坏记录
		# 触发降级，就从 null 变成带 export_error 的对象（Issue #2593）。
		records = [item for item in friends if isinstance(item, dict)]
		# 单个联系人的深嵌套字段逐条降级，与职位导出同口径（Issue #2546、
		# #2593）：其余联系人原样写出，坏记录只丢问题字段并标注原因。
		try:
			text = strict_json_text(records, null_oversized_ints=True)
		except JsonSerializationError:
			text = strict_json_text(
				degrade_unserializable_records(records, message=_DEGRADED_EXPORT_MESSAGE),
				null_oversized_ints=True,
			)
		return _sanitize_surrogates(text)
	if fmt == "csv":
		rendered = _render_csv(friends)
	elif fmt == "html":
		rendered = _render_html(friends, from_who, days, diff_result)
	else:
		rendered = _render_markdown(friends, from_who, days, diff_result)
	return _sanitize_surrogates(rendered)


def _render_csv(friends: list[dict[str, Any]]) -> str:
	"""渲染为 CSV 格式（含公式注入防护）。"""
	if not friends:
		return ""
	fields = [
		"name", "title", "brand_name", "initiated_by",
		"msg_status", "unread", "last_msg", "last_time",
		"uid", "security_id", "encrypt_job_id",
	]
	buf = io.StringIO(newline="")
	writer = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore")
	writer.writeheader()
	for item in friends:
		if not isinstance(item, dict):
			# 非对象条目跳过：``item.items()`` 的 AttributeError 会让整份
			# CSV 导出落成错误信封（Issue #2162）。
			continue
		# CSV 直接读原始 item，绕过 prepare_render_data 的未读归一化；负数
		# 不得写成 "-1"（还会被公式防护前置单引号），与快照 diff 的 0 口径
		# 保持一致（Issue #2886）。
		if type(item.get("unread")) is int and item["unread"] < 0:
			item = {**item, "unread": 0}
		# null 写成空单元格，不落成字面量 None（Issue #1793）。
		safe_row = {k: "" if v is None else sanitize_csv_cell(str(_text_cell(v))) for k, v in item.items()}
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
		f"> 总计：{rd['total']} 条（{' / '.join(escape_md_cell(part) for part in rd['count_parts'])}）",
	]

	if rd["diff_summary"]:
		ds = rd["diff_summary"]
		# 快照来自本地旧版本，值可能含换行 / 链接语法：与表格单元格同口径转义，
		# 避免向导出报告注入额外行（Issue #2321）。
		lines.append(
			f"> 较上次（{escape_md_cell(ds['prev_date'])}）变化：{escape_md_cell(ds['change'])}"
		)

	lines.append("")

	for section in rd["sections"]:
		# 分组标题来自平台字段 initiated_by：换行 / 链接语法不转义可向导出报告
		# 插入伪造章节（与 #2321 的 diff 行同口径，Issue #2344）。
		lines.append(f"## {escape_md_cell(section['subtitle'])}")
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
		lines.append("<summary>联系人标识映射表（点击展开）</summary>")
		lines.append("")
		lines.append("| 编号 | 公司/联系人 | 联系人标识（uid 优先） |")
		lines.append("|------|------------|--------------------------|")
		for ref, key, label in rd["id_map"]:
			# label 含平台外部可控的联系人 / 公司名称：不转义时一个 `|` 就能让
			# uid 落错列，换行还能凭空插入伪造的映射行（Issue #895）。
			lines.append(f"| {ref} | {escape_md_cell(label)} | {escape_md_cell(key)} |")
		lines.append("")
		lines.append("</details>")
		lines.append("")
		lines.append("> 优先使用 uid 调用 `boss chatmsg <uid>`；缺少 uid 的旧记录会回退展示可能已失效的 security_id。")

	return "\n".join(lines) + "\n"


def _render_html(
	friends: list[dict[str, Any]],
	from_who: str | None,
	days: int | None,
	diff_result: dict[str, Any],
) -> str:
	"""渲染为 HTML 格式，含分组、diff 标记和联系人标识映射。"""

	def esc(value: Any) -> str:
		# 平台字段类型漂移（数字 / 对象）时 html.escape 直接抛 AttributeError，
		# 整次 HTML 导出落成 INTERNAL_ERROR；md / csv 已用 str() 兜底，这里
		# 统一同口径（Issue #1296）。标量兜底走 safe_text：超过 4300 位的整数
		# 裸 str() 抛 ValueError，与 csv / job_card 同口径按缺失处理（Issue #2162）。
		return _html.escape(safe_text(value))

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

	# 联系人标识映射表
	map_rows = []
	for ref, key, label in rd["id_map"]:
		map_rows.append(f"<tr><td>{ref}</td><td>{esc(label)}</td><td class='sid'>{esc(key)}</td></tr>")
	map_html = f"""
	<details>
		<summary>联系人标识映射表（点击展开）</summary>
		<table>
			<thead><tr><th>编号</th><th>公司/联系人</th><th>联系人标识（uid 优先）</th></tr></thead>
			<tbody>{''.join(map_rows)}</tbody>
		</table>
		<p>优先使用 uid；缺少 uid 的旧记录会回退展示可能已失效的 security_id。</p>
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
