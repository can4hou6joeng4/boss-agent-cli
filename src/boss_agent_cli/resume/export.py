"""Resume export to HTML and PDF.

HTML export: renders to a self-contained HTML file.
PDF export: renders HTML then uses patchright (headless Chromium) to generate PDF.

两种导出都先写同目录临时文件再原子替换：中途被中断不会留下半截文件，
也不会毁掉上一次的完整导出。
"""

import os
import tempfile
from pathlib import Path

from boss_agent_cli.resume.models import ResumeData
from boss_agent_cli.resume.templates import render_resume_html
from boss_agent_cli.services.text_files import write_text_atomic


def export_html(resume: ResumeData, output_path: Path) -> Path:
	"""Export resume as a self-contained HTML file.

	Creates parent directories if they don't exist.
	Returns the output path.
	"""
	html = render_resume_html(resume)
	write_text_atomic(output_path, html)
	return output_path


def export_pdf(resume: ResumeData, output_path: Path) -> Path:
	"""Export resume as a PDF file using patchright (headless Chromium).

	Creates parent directories if they don't exist.
	Returns the output path.
	"""
	from patchright.sync_api import sync_playwright

	html = render_resume_html(resume)
	output_path.parent.mkdir(parents=True, exist_ok=True)
	# Chromium 直接写目标路径时，中断会留下半截 PDF；先写临时文件再替换。
	fd, temporary_name = tempfile.mkstemp(prefix=f".{output_path.name}.", suffix=".pdf", dir=output_path.parent)
	os.close(fd)
	temporary = Path(temporary_name)
	try:
		with sync_playwright() as p:
			browser = p.chromium.launch(headless=True)
			try:
				page = browser.new_page()
				page.set_content(html, wait_until="networkidle")
				page.pdf(
					path=str(temporary),
					format="A4",
					print_background=True,
					margin={"top": "10mm", "bottom": "10mm", "left": "10mm", "right": "10mm"},
				)
			finally:
				browser.close()
		os.replace(temporary, output_path)
	finally:
		temporary.unlink(missing_ok=True)
	return output_path
