"""职位导出：脱敏与 json / csv / html 文件写入（export 命令与 wizard 共用）。"""

import csv
import html as _html
import json
from typing import Any


def prepare_export_items(items: list[dict[str, Any]], *, include_private: bool) -> list[dict[str, Any]]:
	if include_private:
		return items
	return [redact_export_item(item) for item in items]


def redact_export_item(item: dict[str, Any]) -> dict[str, Any]:
	redacted = dict(item)
	for key in ("job_id", "security_id", "lid", "boss_name"):
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
		"skills": raw.get("skills", []),
		"welfare": raw.get("welfareList", []),
	}


def write_export_file(items: list[dict[str, Any]], fmt: str, path: str) -> None:
	if fmt == "json":
		with open(path, "w", encoding="utf-8") as f:
			json.dump(items, f, ensure_ascii=False, indent=2)
	elif fmt == "csv":
		if not items:
			with open(path, "w") as f:
				f.write("")
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
		with open(path, "w", encoding="utf-8", newline="") as f:
			writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
			writer.writeheader()
			for item in items:
				row = dict(item)
				if isinstance(row.get("skills"), list):
					row["skills"] = ", ".join(row["skills"])
				if isinstance(row.get("welfare"), list):
					row["welfare"] = ", ".join(row["welfare"])
				# CSV 公式注入防护
				row = {k: _sanitize_csv_cell(str(v)) for k, v in row.items()}
				writer.writerow(row)


def _sanitize_csv_cell(value: str) -> str:
	"""防止 CSV 公式注入：以 =+@- 开头的值前置单引号。"""
	if isinstance(value, str) and value and value[0] in ("=", "+", "-", "@"):
		return f"'{value}"
	return value


def write_html_export(items: list[dict[str, Any]], path: str) -> None:
	"""将搜索结果导出为 HTML 表格。"""
	esc = _html.escape
	if not items:
		with open(path, "w", encoding="utf-8") as f:
			f.write("<html><body><p>无数据</p></body></html>")
		return

	rows = []
	for i, item in enumerate(items, 1):
		skills = item.get("skills", [])
		if isinstance(skills, list):
			skills_html = " ".join(f'<span class="tag sk">{esc(s)}</span>' for s in skills)
		else:
			skills_html = esc(str(skills))
		welfare = item.get("welfare", [])
		if isinstance(welfare, list):
			welfare_html = " ".join(f'<span class="tag wf">{esc(w)}</span>' for w in welfare)
		else:
			welfare_html = esc(str(welfare))
		rows.append(
			f"<tr>"
			f"<td>{i}</td>"
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
<div class="sub">共 {len(items)} 条</div>
<table>
  <thead><tr>
    <th>#</th><th>岗位</th><th>公司</th><th>城市</th>
    <th>经验</th><th>学历</th><th>技能</th><th>福利</th>
  </tr></thead>
  <tbody>{"".join(rows)}</tbody>
</table>
</body></html>"""

	with open(path, "w", encoding="utf-8") as f:
		f.write(html_content)
