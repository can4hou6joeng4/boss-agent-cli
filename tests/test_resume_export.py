"""Tests for resume export (HTML + PDF)."""

import json
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from boss_agent_cli.main import cli
from boss_agent_cli.resume.export import export_html, export_pdf
from boss_agent_cli.resume.models import (
	PersonalInfoSection,
	ResumeData,
)


def _make_resume(**kwargs) -> ResumeData:
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


def _invoke(runner, tmp_path, args):
	return runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", "resume"] + args)


# ── export_html ──────────────────────────────────────────────


def test_export_html_creates_file(tmp_path):
	"""export_html writes an HTML file to the specified path."""
	resume = _make_resume()
	out = tmp_path / "resume.html"
	result = export_html(resume, out)
	assert result == out
	assert out.exists()
	content = out.read_text(encoding="utf-8")
	assert "<html" in content
	assert "Test Resume" in content


def test_export_html_creates_parent_dirs(tmp_path):
	"""export_html creates parent directories if they don't exist."""
	resume = _make_resume()
	out = tmp_path / "deep" / "nested" / "dir" / "resume.html"
	result = export_html(resume, out)
	assert result == out
	assert out.exists()


def test_export_html_overwrites_existing(tmp_path):
	"""export_html overwrites an existing file."""
	resume = _make_resume(title="Version 1")
	out = tmp_path / "resume.html"
	export_html(resume, out)

	resume2 = _make_resume(title="Version 2")
	export_html(resume2, out)
	content = out.read_text(encoding="utf-8")
	assert "Version 2" in content
	assert "Version 1" not in content


# ── export_pdf ───────────────────────────────────────────────


def test_export_pdf_mock_patchright(tmp_path):
	"""export_pdf calls patchright correctly (mocked)."""
	resume = _make_resume()
	out = tmp_path / "resume.pdf"

	mock_page = MagicMock()
	mock_browser = MagicMock()
	mock_browser.new_page.return_value = mock_page
	mock_chromium = MagicMock()
	mock_chromium.launch.return_value = mock_browser
	mock_playwright = MagicMock()
	mock_playwright.chromium = mock_chromium

	mock_context_manager = MagicMock()
	mock_context_manager.__enter__ = MagicMock(return_value=mock_playwright)
	mock_context_manager.__exit__ = MagicMock(return_value=False)

	with patch("patchright.sync_api.sync_playwright", return_value=mock_context_manager):
		result = export_pdf(resume, out)

	assert result == out
	mock_chromium.launch.assert_called_once_with(headless=True)
	mock_browser.new_page.assert_called_once()
	mock_page.set_content.assert_called_once()
	mock_page.pdf.assert_called_once()
	mock_browser.close.assert_called_once()


def test_export_pdf_call_params(tmp_path):
	"""export_pdf passes correct PDF parameters."""
	resume = _make_resume()
	out = tmp_path / "output.pdf"

	mock_page = MagicMock()
	mock_browser = MagicMock()
	mock_browser.new_page.return_value = mock_page
	mock_chromium = MagicMock()
	mock_chromium.launch.return_value = mock_browser
	mock_playwright = MagicMock()
	mock_playwright.chromium = mock_chromium

	mock_context_manager = MagicMock()
	mock_context_manager.__enter__ = MagicMock(return_value=mock_playwright)
	mock_context_manager.__exit__ = MagicMock(return_value=False)

	with patch("patchright.sync_api.sync_playwright", return_value=mock_context_manager):
		export_pdf(resume, out)

	pdf_call = mock_page.pdf.call_args
	assert pdf_call.kwargs["format"] == "A4"
	assert pdf_call.kwargs["print_background"] is True
	assert pdf_call.kwargs["margin"]["top"] == "10mm"
	assert pdf_call.kwargs["margin"]["bottom"] == "10mm"
	assert pdf_call.kwargs["margin"]["left"] == "10mm"
	assert pdf_call.kwargs["margin"]["right"] == "10mm"
	assert pdf_call.kwargs["path"] == str(out)


def test_export_pdf_creates_parent_dirs(tmp_path):
	"""export_pdf creates parent directories."""
	resume = _make_resume()
	out = tmp_path / "sub" / "dir" / "resume.pdf"

	mock_page = MagicMock()
	mock_browser = MagicMock()
	mock_browser.new_page.return_value = mock_page
	mock_chromium = MagicMock()
	mock_chromium.launch.return_value = mock_browser
	mock_playwright = MagicMock()
	mock_playwright.chromium = mock_chromium

	mock_context_manager = MagicMock()
	mock_context_manager.__enter__ = MagicMock(return_value=mock_playwright)
	mock_context_manager.__exit__ = MagicMock(return_value=False)

	with patch("patchright.sync_api.sync_playwright", return_value=mock_context_manager):
		export_pdf(resume, out)

	assert out.parent.exists()


# ── CLI integration: --format html ───────────────────────────


def test_cli_export_html(tmp_path):
	"""boss resume export <name> --format html creates an HTML file."""
	runner = CliRunner()
	# Init a resume first
	_invoke(runner, tmp_path, ["init", "--name", "htmltest", "--template", "default"])
	out_file = tmp_path / "test_output.html"
	result = _invoke(runner, tmp_path, ["export", "htmltest", "--format", "html", "-o", str(out_file)])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["ok"] is True
	assert parsed["data"]["format"] == "html"
	assert out_file.exists()


def test_cli_export_html_expands_tilde_in_output_path(tmp_path, monkeypatch):
	"""`~` 不经 shell 时（MCP 子进程、引号包裹）也必须展开到用户目录。"""
	monkeypatch.setenv("HOME", str(tmp_path))
	monkeypatch.setenv("USERPROFILE", str(tmp_path))
	runner = CliRunner()
	_invoke(runner, tmp_path, ["init", "--name", "tildetest", "--template", "default"])
	result = _invoke(runner, tmp_path, ["export", "tildetest", "--format", "html", "-o", "~/out/resume.html"])
	assert result.exit_code == 0
	assert (tmp_path / "out" / "resume.html").exists()


def test_cli_export_html_default_path(tmp_path, monkeypatch):
	"""When no -o is given, export defaults to <name>.html (in cwd, isolated here)."""
	monkeypatch.chdir(tmp_path)
	runner = CliRunner()
	_invoke(runner, tmp_path, ["init", "--name", "autopath", "--template", "default"])
	result = _invoke(runner, tmp_path, ["export", "autopath", "--format", "html"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["ok"] is True
	assert parsed["data"]["format"] == "html"
	assert (tmp_path / "autopath.html").exists()


# ── CLI integration: --format pdf chromium unavailable ───────


def test_cli_export_pdf_chromium_unavailable(tmp_path):
	"""When patchright/chromium is not available, export returns EXPORT_FAILED."""
	runner = CliRunner()
	_invoke(runner, tmp_path, ["init", "--name", "pdffail", "--template", "default"])

	with patch(
		"patchright.sync_api.sync_playwright",
		side_effect=Exception("Chromium not installed"),
	):
		result = _invoke(runner, tmp_path, ["export", "pdffail", "--format", "pdf"])
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["ok"] is False
	assert parsed["error"]["code"] == "EXPORT_FAILED"
	# 导出用 patchright 渲染，恢复指引必须指向 patchright 而不是 playwright
	from boss_agent_cli.schema.error_codes import ERROR_CODES

	action = parsed["error"]["recovery_action"]
	assert action == ERROR_CODES["EXPORT_FAILED"]["recovery_action"]
	assert "playwright install" not in action


def test_cli_export_not_found(tmp_path):
	"""Exporting a non-existent resume returns RESUME_NOT_FOUND."""
	runner = CliRunner()
	result = _invoke(runner, tmp_path, ["export", "ghost", "--format", "html"])
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["error"]["code"] == "RESUME_NOT_FOUND"


def test_cli_export_json_corrupt_resume_reports_file_invalid(tmp_path):
	"""文件在但读不出来时不能报成 LOCAL_IO_ERROR：磁盘/权限指引会把人带偏。

	`store.export_json` 内部的 `get()` 返回 None 后抛 `FileNotFoundError`，
	顶层 OSError 网兜会把它说成「本地文件读写失败」。文件确实存在，也不能报成
	RESUME_NOT_FOUND：`resume list` 会隐藏坏文件，照提示只会空转。现在统一报
	RESUME_FILE_INVALID，并给出修复 / 删除路径。
	"""
	resumes = tmp_path / "resumes"
	resumes.mkdir(parents=True)
	(resumes / "broken.json").write_text("{", encoding="utf-8")

	runner = CliRunner()
	result = _invoke(runner, tmp_path, ["export", "broken", "--format", "json"])

	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["error"]["code"] == "RESUME_FILE_INVALID"
