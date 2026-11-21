"""职位导出：脱敏与 json / csv / html 文件写入（export 命令与 wizard 共用）。"""

import csv
import html as _html
import io
import math
from typing import Any

from boss_agent_cli.api.models import field_list, safe_text
from boss_agent_cli.services.chat_utils import sanitize_csv_cell
from boss_agent_cli.services.text_files import sanitize_surrogates, write_text_atomic
from boss_agent_cli.services.json_files import strict_json_text


def prepare_export_items(items: list[dict[str, Any]], *, include_private: bool) -> list[dict[str, Any]]:
	# 非对象条目不进入导出：``dict(item)`` / ``item.get`` 会抛 TypeError /
	# AttributeError，把本地形状漂移升级成错误信封（Issue #2162）。CLI 调用方
	# 目前都用 dict_items 过滤，这里是公共边界的纵深防御。
	usable = [item for item in items if isinstance(item, dict)]
	if include_private:
		return usable
	return [redact_export_item(item) for item in usable]


def redact_export_item(item: dict[str, Any]) -> dict[str, Any]:
	redacted = dict(item)
	for key in ("job_id", "job_url", "security_id", "lid", "boss_name"):
		if key in redacted:
			redacted[key] = "[REDACTED]"
	return redacted


def public_html_export_item_from_api(raw: dict[str, Any]) -> dict[str, Any]:
	return {
		"title": raw.get("jobName", ""),
		"company": raw.get("brandName", ""),
		"city": raw.get("cityName", ""),
		"experience": raw.get("jobExperience", ""),
		"education": raw.get("jobDegree", ""),
		"skills": field_list(raw, "skills"),
		"welfare": field_list(raw, "welfareList"),
	}


def _sanitize_surrogates(text: str) -> str:
	"""兼容旧调用点：实现已收敛到 services.text_files（Issue #2017、#2109）。"""
	return sanitize_surrogates(text)


def _cell_text(value: Any) -> str:
	"""单元格文本：null / 非有限数 → 空串，容器 → JSON 文本，标量 → str。

	旧的 ``str()`` 兜底会把元素级 null 导出成字面量 ``None``、把 dict / list
	导出成 Python repr，同一份数据的 JSON 分支却把非有限数归一为 null、
	HTML 顶层 null 渲染为空（Issue #1851 #1853 #1916）。顶层单元格与列表元素
	共用这一口径。
	"""
	if value is None:
		return ""
	if isinstance(value, bool):
		# JSON 分支是 true / false：str(True) 会让 CSV / HTML 出现 Python
		# 字面量（Issue #2018）。
		return "true" if value else "false"
	if isinstance(value, (dict, list, tuple)):
		# 容器先递归归一非有限数，否则嵌套 Infinity / NaN 会写成非标准 JSON
		# 字面量，与 JSON 分支的 null 口径不一致（Issue #1977）。深嵌套无法
		# 序列化时按缺失处理，单个坏单元格不能中止整份导出（Issue #2016）。
		try:
			return _sanitize_surrogates(strict_json_text(value, indent=None))
		except OSError:
			return ""
	if isinstance(value, float) and not math.isfinite(value):
		return ""
	# 标量兜底走 safe_text：超过 4300 位的整数在裸 str() 直接抛 ValueError，
	# 会中止整份导出；与容器分支 / job_card 同口径按缺失处理（Issue #2114）。
	return _sanitize_surrogates(safe_text(value))


def _join_cells(value: list[Any]) -> str:
	"""列表单元格按文本拼接：空元素跳过，容器序列化为 JSON 文本。"""
	return ", ".join(text for item in value if (text := _cell_text(item)))


def write_export_file(items: list[dict[str, Any]], fmt: str, path: str) -> None:
	if fmt == "json":
		# 导出产物的单个数值漂移不应中止整份文件：超长整数按 null 输出，
		# 与 CSV / HTML 通道的降级口径一致（Issue #2387）。
		write_text_atomic(path, strict_json_text(items, null_oversized_ints=True))
	elif fmt == "csv":
		if not items:
			write_text_atomic(path, "")
			return
		fields = [
			"title",
			"company",
			"salary",
			"city",
			"district",
			"employment_type",
			"raw_job_type",
			"days_per_week",
			"least_month",
			"experience",
			"education",
			"skills",
			"welfare",
			"industry",
			"scale",
			"boss_name",
			"boss_title",
			"job_id",
			"security_id",
		]
		buffer = io.StringIO()
		writer = csv.DictWriter(buffer, fieldnames=fields, extrasaction="ignore")
		writer.writeheader()
		for item in items:
			if not isinstance(item, dict):
				# 非对象条目跳过：``dict(item)`` 与 ``item.get`` 会让整份
				# 导出落成 TypeError / AttributeError（Issue #2162）。
				continue
			row = dict(item)
			if isinstance(row.get("skills"), list):
				row["skills"] = _join_cells(row["skills"])
			if isinstance(row.get("welfare"), list):
				row["welfare"] = _join_cells(row["welfare"])
			# CSV 公式注入防护；与 chat 导出一致（剥 Tab/CR，Issue #876）。
			# null / 非有限数写成空单元格、容器转 JSON 文本，与 HTML / JSON
			# 路径同口径（Issue #1354 #1793 #1916）。
			row = {k: sanitize_csv_cell(_cell_text(v)) for k, v in row.items()}
			writer.writerow(row)
		# csv 模块按行写出 ``\r\n``；文本模式默认会再翻译一次换行，Windows 上
		# 落盘变成 ``\r\r\n``（Issue #1401）。与 chat 导出同口径关掉翻译。
		write_text_atomic(path, buffer.getvalue(), newline="")
	else:
		# 共享 service 不能对未知格式静默不写文件（Issue #1853）；调用方
		# （export / wizard）已在入口限制为 json / csv，这里兜底防回归。
		raise ValueError(f"不支持的导出格式: {fmt}")


# 向后兼容别名：旧测试与调用方按模块内私有名取用。
_sanitize_csv_cell = sanitize_csv_cell


def render_html_export(items: list[dict[str, Any]]) -> str:
	"""将搜索结果渲染为自包含 HTML 表格字符串（export 的 -o 与 stdout 共用）。"""

	def esc(value: Any) -> str:
		# 平台字段类型漂移（数字 / 对象）时 html.escape 直接抛 AttributeError，
		# 整次 HTML 导出落成 INTERNAL_ERROR；csv 已用 _cell_text 兜底，这里统一
		# 同口径（Issue #1296）。null 字段是「缺失」，不能渲染成字面量 "None"，
		# 容器序列化为 JSON 文本而不是 Python repr（Issue #1354 #1916）。
		return _html.escape(_cell_text(value))

	if not items:
		return "<html><body><p>无数据</p></body></html>"

	# 非对象条目没有可展示字段：先过滤，总数与渲染行数保持同源，不能让
	# 「共 N 条」把被跳过的坏条目也算进去（Issue #2162、#2345）。
	usable = [item for item in items if isinstance(item, dict)]
	rows = []
	index = 0
	for item in usable:
		index += 1
		skills = item.get("skills", [])
		if isinstance(skills, list):
			# 空元素不渲染成空标签；容器元素走 JSON 文本（Issue #1916）。
			skills_html = " ".join(
				f'<span class="tag sk">{esc(s)}</span>' for s in skills if _cell_text(s)
			)
		elif skills is None:
			# null 是缺失，不能渲染成字面量 "None"（Issue #1853）。
			skills_html = ""
		else:
			skills_html = esc(skills)
		welfare = item.get("welfare", [])
		if isinstance(welfare, list):
			welfare_html = " ".join(
				f'<span class="tag wf">{esc(w)}</span>' for w in welfare if _cell_text(w)
			)
		elif welfare is None:
			welfare_html = ""
		else:
			welfare_html = esc(welfare)
		rows.append(
			f"<tr>"
			f"<td>{index}</td>"
			f"<td class='title'>{esc(item.get('title', ''))}</td>"
			f"<td class='company'>{esc(item.get('company', ''))}</td>"
			f"<td>{esc(item.get('city', ''))}</td>"
			f"<td>{esc(item.get('experience', ''))}</td>"
			f"<td>{esc(item.get('education', ''))}</td>"
			f"<td>{skills_html}</td>"
			f"<td>{welfare_html}</td>"
			f"</tr>"
		)

	html_content = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>BOSS 直聘搜索结果导出</title>
<style>
  :root {{ --green: #00b38a; --bg: #f8f9fa; }}
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ font-family: -apple-system, "PingFang SC", "Helvetica Neue", sans-serif;
         background: var(--bg); color: #333; line-height: 1.6; padding: 20px; max-width: 1100px; margin: 0 auto; }}
  h1 {{ text-align: center; font-size: 20px; margin-bottom: 4px; }}
  .sub {{ text-align: center; color: #888; font-size: 13px; margin-bottom: 16px; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
  th {{ background: #f0f0f0; font-weight: 600; text-align: left; padding: 6px 8px; white-space: nowrap; }}
  td {{ padding: 6px 8px; border-bottom: 1px solid #eee; vertical-align: top; }}
  tr:hover {{ background: #f5faf8; }}
  .title {{ font-weight: 600; }}
  .company {{ color: var(--green); font-weight: 600; }}
  .dim {{ color: #888; }}
  .tag {{ display: inline-block; padding: 1px 6px; border-radius: 4px; font-size: 11px; margin: 1px; }}
  .sk {{ background: #e8f5e9; color: #2e7d32; }}
  .wf {{ background: #fff3e0; color: #e65100; }}
</style></head><body>
<h1>BOSS 直聘搜索结果</h1>
<div class="sub">共 {len(usable)} 条</div>
<table>
  <thead><tr>
    <th>#</th><th>岗位</th><th>公司</th><th>城市</th>
    <th>经验</th><th>学历</th><th>技能</th><th>福利</th>
  </tr></thead>
  <tbody>{"".join(rows)}</tbody>
</table>
</body></html>"""
	return html_content


def write_html_export(items: list[dict[str, Any]], path: str) -> None:
	"""将搜索结果导出为 HTML 表格文件。"""
	write_text_atomic(path, render_html_export(items))
