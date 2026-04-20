"""Resume HTML template rendering.

Renders ResumeData to a self-contained HTML document with inline CSS,
optimized for A4 printing and PDF export.
"""

import html
from typing import Any

from boss_agent_cli.resume.models import ResumeData


def _esc(text: str | None) -> str:
	"""Escape HTML entities to prevent XSS."""
	if text is None:
		return ""
	return html.escape(str(text))


def _render_personal_info(resume: ResumeData) -> str:
	"""Render personal info section."""
	if not resume.personal_info.items:
		return ""
	items_html = []
	for item in resume.personal_info.items:
		label = _esc(item.label)
		value = _esc(item.value)
		if item.link:
			value = f'<a href="{_esc(item.link)}" style="color:#2563eb;text-decoration:none;">{value}</a>'
		items_html.append(f'<span class="info-item"><span class="info-label">{label}:</span> {value}</span>')
	separator = " " if resume.personal_info.layout == "inline" else "<br>"
	return f'<div class="personal-info">{separator.join(items_html)}</div>'


def _render_job_intention(resume: ResumeData) -> str:
	"""Render job intention section if present."""
	ji = resume.job_intention
	if ji is None:
		return ""
	title = _esc(ji.title)
	items_html = []
	for item in ji.items:
		items_html.append(
			f'<div class="ji-item"><span class="ji-label">{_esc(item.label)}:</span> {_esc(item.value)}</div>'
		)
	bg_style = ' style="background:#f0f7ff;padding:12px 16px;border-radius:6px;margin:12px 0;"' if ji.show_background else ""
	return f"""<div class="job-intention"{bg_style}>
<h3 style="color:#2563eb;margin:0 0 8px 0;font-size:14px;">{title}</h3>
{''.join(items_html)}
</div>"""


def _render_row(row: dict[str, Any]) -> str:
	"""Render a single module row (richtext or tags)."""
	row_type = row.get("type", "")
	if row_type == "tags":
		tags = row.get("tags", [])
		if not tags:
			return ""
		tags_html = "".join(
			f'<span class="tag">{_esc(tag)}</span>' for tag in tags
		)
		return f'<div class="tags-row">{tags_html}</div>'
	elif row_type == "richtext":
		content = row.get("content", [])
		columns = row.get("columns", 1)
		if not content:
			return ""
		if columns > 1:
			col_width = f"{100 // columns}%"
			cells = "".join(
				f'<div style="width:{col_width};display:inline-block;vertical-align:top;">{_esc(line)}</div>'
				for line in content
			)
			return f'<div class="richtext-row multi-col">{cells}</div>'
		lines_html = "".join(f"<p>{_esc(line)}</p>" for line in content)
		return f'<div class="richtext-row">{lines_html}</div>'
	else:
		# Unknown row type: render content if available
		content = row.get("content", [])
		if not content:
			return ""
		lines_html = "".join(f"<p>{_esc(str(line))}</p>" for line in content)
		return f'<div class="row">{lines_html}</div>'


def _render_modules(resume: ResumeData) -> str:
	"""Render all resume modules."""
	parts = []
	for mod in resume.modules:
		title = _esc(mod.title)
		rows_html = "".join(_render_row(row) for row in mod.rows)
		parts.append(f"""<div class="module">
<h2 class="module-title">{title}</h2>
<div class="module-content">{rows_html}</div>
</div>""")
	return "\n".join(parts)


def render_resume_html(resume: ResumeData) -> str:
	"""Render a ResumeData object to a complete HTML document.

	Features:
	- All CSS inline, A4 paper size (210mm x 297mm), print-friendly
	- Chinese font stack
	- Primary color #2563eb, tag background #e8f0fe
	- XSS-safe: all user input is HTML-escaped
	"""
	title = _esc(resume.title)
	title_align = "center" if resume.center_title else "left"

	avatar_html = ""
	if resume.avatar:
		avatar_html = f'<img src="{_esc(resume.avatar)}" class="avatar" alt="avatar">'

	personal_info_html = _render_personal_info(resume)
	job_intention_html = _render_job_intention(resume)
	modules_html = _render_modules(resume)

	return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<style>
@page {{
  size: A4;
  margin: 10mm;
}}
* {{
  margin: 0;
  padding: 0;
  box-sizing: border-box;
}}
body {{
  font-family: "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
  font-size: 13px;
  line-height: 1.6;
  color: #333;
  background: #fff;
  width: 210mm;
  min-height: 297mm;
  margin: 0 auto;
  padding: 16mm 14mm;
}}
.header {{
  text-align: {title_align};
  margin-bottom: 16px;
  display: flex;
  align-items: center;
  gap: 16px;
}}
.header-text {{
  flex: 1;
}}
.avatar {{
  width: 72px;
  height: 72px;
  border-radius: 50%;
  object-fit: cover;
}}
h1 {{
  font-size: 22px;
  color: #2563eb;
  margin-bottom: 4px;
}}
.personal-info {{
  color: #555;
  font-size: 12px;
  margin-top: 6px;
}}
.info-item {{
  margin-right: 16px;
}}
.info-label {{
  color: #888;
}}
.job-intention {{
  margin: 12px 0;
  font-size: 12px;
}}
.ji-item {{
  display: inline-block;
  margin-right: 20px;
  margin-top: 4px;
}}
.ji-label {{
  color: #888;
}}
.module {{
  margin-top: 16px;
}}
.module-title {{
  font-size: 15px;
  color: #2563eb;
  border-bottom: 2px solid #2563eb;
  padding-bottom: 4px;
  margin-bottom: 8px;
}}
.module-content p {{
  margin: 2px 0;
}}
.tags-row {{
  margin: 6px 0;
}}
.tag {{
  display: inline-block;
  background: #e8f0fe;
  color: #2563eb;
  padding: 2px 10px;
  border-radius: 12px;
  font-size: 12px;
  margin: 2px 4px 2px 0;
}}
.richtext-row {{
  margin: 4px 0;
}}
.multi-col {{
  display: flex;
  flex-wrap: wrap;
}}
@media print {{
  body {{
    width: auto;
    padding: 0;
  }}
}}
</style>
</head>
<body>
<div class="header">
{avatar_html}
<div class="header-text">
<h1>{title}</h1>
{personal_info_html}
</div>
</div>
{job_intention_html}
{modules_html}
</body>
</html>"""
