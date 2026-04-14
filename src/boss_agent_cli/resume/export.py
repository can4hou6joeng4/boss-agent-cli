"""Resume export to HTML and PDF.

HTML export: renders to a self-contained HTML file.
PDF export: renders HTML then uses patchright (headless Chromium) to generate PDF.
"""

from pathlib import Path

from boss_agent_cli.resume.models import ResumeData
from boss_agent_cli.resume.templates import render_resume_html


def export_html(resume: ResumeData, output_path: Path) -> Path:
	"""Export resume as a self-contained HTML file.

	Creates parent directories if they don't exist.
	Returns the output path.
	"""
	output_path.parent.mkdir(parents=True, exist_ok=True)
	html = render_resume_html(resume)
	output_path.write_text(html, encoding="utf-8")
	return output_path


def export_pdf(resume: ResumeData, output_path: Path) -> Path:
	"""Export resume as a PDF file using patchright (headless Chromium).

	Creates parent directories if they don't exist.
	Returns the output path.
	"""
	from patchright.sync_api import sync_playwright

	html = render_resume_html(resume)
	output_path.parent.mkdir(parents=True, exist_ok=True)
	with sync_playwright() as p:
		browser = p.chromium.launch(headless=True)
		page = browser.new_page()
		page.set_content(html, wait_until="networkidle")
		page.pdf(
			path=str(output_path),
			format="A4",
			print_background=True,
			margin={"top": "10mm", "bottom": "10mm", "left": "10mm", "right": "10mm"},
		)
		browser.close()
	return output_path
