"""Tests for resume HTML template rendering."""

from boss_agent_cli.resume.models import (
	JobIntentionItem,
	JobIntentionSection,
	PersonalInfoItem,
	PersonalInfoSection,
	ResumeData,
	ResumeModule,
)
from boss_agent_cli.resume.templates import render_resume_html


def _make_resume(**kwargs) -> ResumeData:
	"""Create a minimal ResumeData for testing."""
	defaults = {
		"name": "test",
		"title": "Test Resume",
		"center_title": False,
		"personal_info": PersonalInfoSection(items=[], layout="inline"),
		"job_intention": None,
		"modules": [],
		"avatar": "",
	}
	defaults.update(kwargs)
	return ResumeData(**defaults)


# ── basic HTML structure ──────────────────────────────────────


def test_html_basic_structure():
	"""Output contains <html>, <head>, <body> tags."""
	resume = _make_resume()
	html = render_resume_html(resume)
	assert "<html" in html
	assert "<head>" in html
	assert "<body>" in html
	assert "</html>" in html
	assert "<!DOCTYPE html>" in html


def test_html_charset_and_viewport():
	"""Output includes UTF-8 charset and viewport meta."""
	resume = _make_resume()
	html = render_resume_html(resume)
	assert 'charset="UTF-8"' in html
	assert "viewport" in html


# ── title rendering ──────────────────────────────────────────


def test_title_rendered():
	"""Resume title appears in the HTML output."""
	resume = _make_resume(title="Senior Python Developer")
	html = render_resume_html(resume)
	assert "Senior Python Developer" in html


def test_title_centered():
	"""When center_title=True, the header is center-aligned."""
	resume = _make_resume(center_title=True)
	html = render_resume_html(resume)
	assert "center" in html


def test_title_left_aligned():
	"""When center_title=False, the header is left-aligned."""
	resume = _make_resume(center_title=False)
	html = render_resume_html(resume)
	assert "left" in html


# ── personal info ────────────────────────────────────────────


def test_personal_info_rendered():
	"""Personal info items appear in the HTML."""
	items = [
		PersonalInfoItem(label="Email", value="test@example.com"),
		PersonalInfoItem(label="Phone", value="1234567890"),
	]
	resume = _make_resume(personal_info=PersonalInfoSection(items=items, layout="inline"))
	html = render_resume_html(resume)
	assert "Email" in html
	assert "test@example.com" in html
	assert "Phone" in html
	assert "1234567890" in html


def test_personal_info_with_link():
	"""Personal info items with links render as <a> tags."""
	items = [
		PersonalInfoItem(label="GitHub", value="github.com/user", link="https://github.com/user"),
	]
	resume = _make_resume(personal_info=PersonalInfoSection(items=items))
	html = render_resume_html(resume)
	assert "https://github.com/user" in html
	assert "<a " in html


# ── avatar ───────────────────────────────────────────────────


def test_avatar_rendered():
	"""When avatar is set, an <img> tag appears."""
	resume = _make_resume(avatar="https://example.com/photo.jpg")
	html = render_resume_html(resume)
	assert '<img src="https://example.com/photo.jpg"' in html


def test_avatar_not_rendered_when_empty():
	"""When avatar is empty, no <img> tag appears."""
	resume = _make_resume(avatar="")
	html = render_resume_html(resume)
	assert "<img" not in html


# ── job intention ────────────────────────────────────────────


def test_job_intention_rendered():
	"""Job intention section appears when set."""
	ji = JobIntentionSection(
		title="求职意向",
		items=[
			JobIntentionItem(label="期望职位", value="Python 工程师"),
			JobIntentionItem(label="期望城市", value="北京"),
		],
	)
	resume = _make_resume(job_intention=ji)
	html = render_resume_html(resume)
	assert "求职意向" in html
	assert "Python 工程师" in html
	assert "北京" in html
	assert "#f0f7ff" in html  # background color


def test_job_intention_not_rendered_when_none():
	"""No job intention section content when it is None."""
	resume = _make_resume(job_intention=None)
	html = render_resume_html(resume)
	# CSS class definitions exist in <style>, but no actual rendered elements in <body>
	body = html.split("</style>")[-1]
	assert "ji-item" not in body
	assert "ji-label" not in body


# ── richtext and tags modules ────────────────────────────────


def test_richtext_module_rendered():
	"""Richtext rows render their content."""
	mod = ResumeModule(
		id="exp",
		title="工作经历",
		rows=[
			{"type": "richtext", "columns": 1, "content": ["负责后端开发", "维护微服务架构"]},
		],
	)
	resume = _make_resume(modules=[mod])
	html = render_resume_html(resume)
	assert "工作经历" in html
	assert "负责后端开发" in html
	assert "维护微服务架构" in html


def test_tags_module_rendered():
	"""Tags rows render as tag badges."""
	mod = ResumeModule(
		id="skills",
		title="技能标签",
		rows=[
			{"type": "tags", "tags": ["Python", "Go", "Docker"]},
		],
	)
	resume = _make_resume(modules=[mod])
	html = render_resume_html(resume)
	assert "技能标签" in html
	assert "Python" in html
	assert "Go" in html
	assert "Docker" in html
	assert "#e8f0fe" in html  # tag background


def test_richtext_multi_column():
	"""Multi-column richtext renders with inline-block."""
	mod = ResumeModule(
		id="info",
		title="基本信息",
		rows=[
			{"type": "richtext", "columns": 2, "content": ["左列内容", "右列内容"]},
		],
	)
	resume = _make_resume(modules=[mod])
	html = render_resume_html(resume)
	assert "50%" in html
	assert "左列内容" in html
	assert "右列内容" in html


# ── XSS prevention ──────────────────────────────────────────


def test_xss_title_escaped():
	"""Title with HTML tags is escaped."""
	resume = _make_resume(title="<script>alert('xss')</script>")
	html = render_resume_html(resume)
	assert "<script>" not in html
	assert "&lt;script&gt;" in html


def test_xss_personal_info_escaped():
	"""Personal info values with HTML are escaped."""
	items = [PersonalInfoItem(label="Name", value="<b>Evil</b>")]
	resume = _make_resume(personal_info=PersonalInfoSection(items=items))
	html = render_resume_html(resume)
	assert "<b>Evil</b>" not in html
	assert "&lt;b&gt;Evil&lt;/b&gt;" in html


def test_xss_module_content_escaped():
	"""Module content with HTML is escaped."""
	mod = ResumeModule(
		id="test",
		title="Test",
		rows=[{"type": "richtext", "content": ['<img src=x onerror="alert(1)">']}],
	)
	resume = _make_resume(modules=[mod])
	html = render_resume_html(resume)
	assert 'onerror="alert(1)"' not in html
	assert "&lt;img" in html


def test_xss_tags_escaped():
	"""Tags with HTML are escaped."""
	mod = ResumeModule(
		id="skills",
		title="Skills",
		rows=[{"type": "tags", "tags": ['<script>evil</script>']}],
	)
	resume = _make_resume(modules=[mod])
	html = render_resume_html(resume)
	assert "<script>evil</script>" not in html
	assert "&lt;script&gt;" in html


# ── CSS and font ─────────────────────────────────────────────


def test_chinese_font_stack():
	"""HTML includes the Chinese font stack."""
	resume = _make_resume()
	html = render_resume_html(resume)
	assert "PingFang SC" in html
	assert "Microsoft YaHei" in html


def test_a4_page_size():
	"""CSS includes A4 page size for printing."""
	resume = _make_resume()
	html = render_resume_html(resume)
	assert "A4" in html


def test_primary_color():
	"""CSS includes the primary color #2563eb."""
	resume = _make_resume()
	html = render_resume_html(resume)
	assert "#2563eb" in html
