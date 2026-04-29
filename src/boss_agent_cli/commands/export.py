import csv
import html as _html
import json
from typing import Any

import click

from boss_agent_cli.api.models import JobItem
from boss_agent_cli.auth.manager import AuthManager
from boss_agent_cli.commands._platform import get_platform_instance
from boss_agent_cli.display import handle_auth_errors, handle_error_output, handle_output, render_export_summary, render_job_table


@click.command("export")
@click.argument("query")
@click.option("--city", default=None, help="城市名称")
@click.option("--salary", default=None, help="薪资范围")
@click.option("--count", default=50, type=int, help="导出数量")
@click.option("--format", "fmt", default="csv", type=click.Choice(["html", "csv", "json"]), help="输出格式")
@click.option("--output", "-o", default=None, help="输出文件路径（不指定则输出到 stdout JSON 信封）")
@click.pass_context
@handle_auth_errors("export")
def export_cmd(ctx: click.Context, query: str, city: str | None, salary: str | None, count: int, fmt: str, output: str | None) -> None:
	"""导出搜索结果为 CSV 或 JSON 文件"""
	data_dir = ctx.obj["data_dir"]
	logger = ctx.obj["logger"]

	auth = AuthManager(data_dir, logger=logger, platform=ctx.obj.get("platform", "zhipin"))
	with get_platform_instance(ctx, auth) as platform:
		all_items: list[dict[str, Any]] = []
		page = 1
		max_pages = (count + 14) // 15  # 每页约 15 条

		while len(all_items) < count and page <= max_pages:
			logger.info(f"正在获取第 {page} 页...")
			raw = platform.search_jobs(query, city=city, salary=salary, page=page)
			if not platform.is_success(raw):
				code, message = platform.parse_error(raw)
				handle_error_output(
					ctx, "export",
					code=code,
					message=message or "搜索结果获取失败",
					recoverable=False,
				)
				return
			platform_data = platform.unwrap_data(raw) or {}
			job_list = platform_data.get("jobList", [])
			if not job_list:
				break

			for raw_item in job_list:
				if len(all_items) >= count:
					break
				item = JobItem.from_api(raw_item)
				all_items.append(item.to_dict())

			if not platform_data.get("hasMore", False):
				break
			page += 1

		if output:
			_write_to_file(all_items, fmt, output)
			data = {
				"message": f"已导出 {len(all_items)} 条到 {output}",
				"count": len(all_items),
				"format": fmt,
				"path": output,
			}
			handle_output(
				ctx, "export", data,
				render=lambda d: render_export_summary(d),
				hints={
					"next_actions": [
						"boss search <query> — 继续搜索",
						"boss recommend — 获取个性化推荐",
					],
				},
			)
		else:
			data = {
				"count": len(all_items),
				"format": fmt,
				"jobs": all_items,
			}
			handle_output(
				ctx, "export", data,
				render=lambda d: render_job_table(d.get("jobs", []), "export"),
				hints={
					"next_actions": [
						"boss export <query> -o file.csv — 导出到文件",
					],
				},
			)


def _write_to_file(items: list[dict[str, Any]], fmt: str, path: str) -> None:
	if fmt == "json":
		with open(path, "w", encoding="utf-8") as f:
			json.dump(items, f, ensure_ascii=False, indent=2)
	elif fmt == "csv":
		if not items:
			with open(path, "w") as f:
				f.write("")
			return
		fields = ["title", "company", "salary", "city", "district", "experience",
				"education", "skills", "welfare", "industry", "scale", "boss_name",
				"boss_title", "job_id", "security_id"]
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
	elif fmt == "html":
		_write_html(items, path)


def _sanitize_csv_cell(value: str) -> str:
	"""防止 CSV 公式注入：以 =+@- 开头的值前置单引号。"""
	if isinstance(value, str) and value and value[0] in ("=", "+", "-", "@"):
		return f"'{value}"
	return value


def _write_html(items: list[dict[str, Any]], path: str) -> None:
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
			f"<td class='salary'>{esc(item.get('salary', ''))}</td>"
			f"<td>{esc(item.get('city', ''))}</td>"
			f"<td>{esc(item.get('experience', ''))}</td>"
			f"<td>{esc(item.get('education', ''))}</td>"
			f"<td>{skills_html}</td>"
			f"<td>{welfare_html}</td>"
			f"<td class='dim'>{esc(item.get('boss_name', ''))}</td>"
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
  .salary {{ color: #ff6633; font-weight: 700; white-space: nowrap; }}
  .dim {{ color: #888; }}
  .tag {{ display: inline-block; padding: 1px 6px; border-radius: 4px; font-size: 11px; margin: 1px; }}
  .sk {{ background: #e8f5e9; color: #2e7d32; }}
  .wf {{ background: #fff3e0; color: #e65100; }}
</style></head><body>
<h1>BOSS 直聘搜索结果</h1>
<div class="sub">共 {len(items)} 条</div>
<table>
  <thead><tr>
    <th>#</th><th>岗位</th><th>公司</th><th>薪资</th><th>城市</th>
    <th>经验</th><th>学历</th><th>技能</th><th>福利</th><th>招聘者</th>
  </tr></thead>
  <tbody>{''.join(rows)}</tbody>
</table>
</body></html>"""

	with open(path, "w", encoding="utf-8") as f:
		f.write(html_content)
