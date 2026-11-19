"""Tests for resume export (HTML + PDF)."""

import errno
import json
import os
import stat
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
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


def _mock_patchright():
	"""patchright mock：`page.pdf` 按 path 参数落占位文件（真实 Chromium 会写盘）。"""
	mock_page = MagicMock()

	def _write_pdf(*args, **kwargs):
		Path(kwargs["path"]).write_bytes(b"%PDF-1.4 mock")

	mock_page.pdf.side_effect = _write_pdf
	mock_browser = MagicMock()
	mock_browser.new_page.return_value = mock_page
	mock_chromium = MagicMock()
	mock_chromium.launch.return_value = mock_browser
	mock_playwright = MagicMock()
	mock_playwright.chromium = mock_chromium

	mock_context_manager = MagicMock()
	mock_context_manager.__enter__ = MagicMock(return_value=mock_playwright)
	mock_context_manager.__exit__ = MagicMock(return_value=False)
	return mock_page, mock_chromium, mock_browser, mock_context_manager


def test_export_pdf_mock_patchright(tmp_path):
	"""export_pdf calls patchright correctly (mocked)."""
	resume = _make_resume()
	out = tmp_path / "resume.pdf"

	mock_page, mock_chromium, mock_browser, mock_context_manager = _mock_patchright()
	with patch("patchright.sync_api.sync_playwright", return_value=mock_context_manager):
		result = export_pdf(resume, out)

	assert result == out
	assert out.read_bytes() == b"%PDF-1.4 mock"
	assert [p.name for p in tmp_path.iterdir()] == ["resume.pdf"]
	mock_chromium.launch.assert_called_once_with(headless=True)
	mock_browser.new_page.assert_called_once()
	mock_page.set_content.assert_called_once()
	mock_page.pdf.assert_called_once()
	mock_browser.close.assert_called_once()


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="当前平台不支持 FIFO")
def test_export_pdf_refuses_fifo_before_browser(tmp_path):
	out = tmp_path / "resume.pdf"
	os.mkfifo(out)
	with patch("patchright.sync_api.sync_playwright") as playwright:
		with pytest.raises(OSError) as raised:
			export_pdf(_make_resume(), out)

	assert raised.value.filename == str(out)
	playwright.assert_not_called()
	assert stat.S_ISFIFO(out.lstat().st_mode)


def test_export_pdf_call_params(tmp_path):
	"""export_pdf passes correct PDF parameters."""
	resume = _make_resume()
	out = tmp_path / "output.pdf"

	mock_page, _, _, mock_context_manager = _mock_patchright()
	with patch("patchright.sync_api.sync_playwright", return_value=mock_context_manager):
		export_pdf(resume, out)

	pdf_call = mock_page.pdf.call_args
	assert pdf_call.kwargs["format"] == "A4"
	assert pdf_call.kwargs["print_background"] is True
	assert pdf_call.kwargs["margin"]["top"] == "10mm"
	assert pdf_call.kwargs["margin"]["bottom"] == "10mm"
	assert pdf_call.kwargs["margin"]["left"] == "10mm"
	assert pdf_call.kwargs["margin"]["right"] == "10mm"
	# 先写同目录临时文件再原子替换：Chromium 不直接写目标路径。
	pdf_path = Path(pdf_call.kwargs["path"])
	assert pdf_path.parent == out.parent
	# 临时名用固定短前缀，不再拼完整目标名（长文件名会 ENAMETOOLONG，Issue #1985）。
	assert pdf_path.name.startswith(".boss-pdf-")
	assert out.read_bytes() == b"%PDF-1.4 mock"


def test_export_pdf_preserves_existing_on_write_failure(tmp_path):
	"""Chromium 写 PDF 失败时旧文件保留、临时文件不残留。"""
	resume = _make_resume()
	out = tmp_path / "resume.pdf"
	out.write_bytes(b"old complete pdf")

	mock_page = MagicMock()
	mock_page.pdf.side_effect = OSError(errno.ENOSPC, "No space left on device")
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
		try:
			export_pdf(resume, out)
		except OSError:
			pass
		else:  # pragma: no cover - 明确失败才算通过
			raise AssertionError("写入失败应向上抛出")

	assert out.read_bytes() == b"old complete pdf"
	assert [p.name for p in tmp_path.iterdir()] == ["resume.pdf"]


def test_export_html_preserves_existing_on_write_failure(tmp_path):
	"""HTML 写入中途失败时旧简历导出保留。"""
	resume = _make_resume(title="Version 2")
	out = tmp_path / "resume.html"
	out.write_text("上一次的完整简历", encoding="utf-8")

	with patch("os.fsync", side_effect=OSError(errno.ENOSPC, "No space left on device", str(out))):
		try:
			export_html(resume, out)
		except OSError:
			pass
		else:  # pragma: no cover - 明确失败才算通过
			raise AssertionError("写入失败应向上抛出")

	assert out.read_text(encoding="utf-8") == "上一次的完整简历"


def test_export_pdf_creates_parent_dirs(tmp_path):
	"""export_pdf creates parent directories."""
	resume = _make_resume()
	out = tmp_path / "sub" / "dir" / "resume.pdf"

	_, _, _, mock_context_manager = _mock_patchright()
	with patch("patchright.sync_api.sync_playwright", return_value=mock_context_manager):
		export_pdf(resume, out)

	assert out.parent.exists()
	assert out.exists()


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
	# 导出用 patchright 渲染，恢复指引必须指向 patchright 而不是 playwright，
	# 且命令要能在 `uv tool install` 环境里执行（走运行本 CLI 的解释器）。
	from boss_agent_cli.services.browser_runtime import patchright_install_command

	action = parsed["error"]["recovery_action"]
	assert patchright_install_command() in action
	assert "playwright install" not in action


def test_cli_export_pdf_kernel_missing_is_not_export_failed(tmp_path):
	"""PDF 导出命中 patchright 内核缺失时按 BROWSER_KERNEL_MISSING 上报。

	``EXPORT_FAILED`` 是「导出器出错、可以再试一次」的语义；内核缺失是确定性
	失败，重试同一条命令永远同一结果。而且 patchright 的异常文本里带整段安装
	横幅和本机绝对路径，不能塞进 error.message。
	"""
	runner = CliRunner()
	_invoke(runner, tmp_path, ["init", "--name", "pdfkernel", "--template", "default"])

	with patch(
		"patchright.sync_api.sync_playwright",
		side_effect=Exception(
			"BrowserType.launch: Executable doesn't exist at /x/chromium-1/chrome-headless-shell"
		),
	):
		result = _invoke(runner, tmp_path, ["export", "pdfkernel", "--format", "pdf"])

	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["error"]["code"] == "BROWSER_KERNEL_MISSING"
	assert parsed["error"]["recoverable"] is True
	from boss_agent_cli.services.browser_runtime import patchright_install_command

	install = patchright_install_command()
	assert parsed["error"]["recovery_action"] == install
	assert parsed["hints"]["next_actions"][0] == install
	# doctor 必须指向本次 --data-dir，否则自定义目录下诊断的是另一份数据。
	assert str(tmp_path.resolve()) in parsed["hints"]["next_actions"][1]
	assert "chrome-headless-shell" not in parsed["error"]["message"]


def test_cli_export_html_to_directory_is_invalid_param_not_export_failed(tmp_path):
	"""HTML 导出写到目录时是参数错误，不能给「装浏览器内核」的指引。

	HTML 导出只用本地模板渲染，完全不碰 patchright；此前所有异常都归成
	``EXPORT_FAILED`` +「检查 patchright 安装」，把用户引向一个无关的方向。
	目标本身是目录与 `export` / `chat` 的 `-o` 契约一致：`INVALID_PARAM`，
	recoverable=false，提示给出完整文件名（Issue #1723 统一预检）。
	"""
	runner = CliRunner()
	_invoke(runner, tmp_path, ["init", "--name", "htmldir", "--template", "default"])

	result = _invoke(runner, tmp_path, ["export", "htmldir", "--format", "html", "-o", str(tmp_path)])

	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["error"]["code"] == "INVALID_PARAM"
	assert parsed["error"]["recoverable"] is False
	assert "patchright" not in parsed["error"]["recovery_action"]
	assert "完整文件名" in parsed["error"]["recovery_action"]


def test_cli_export_html_render_failure_guidance_is_not_about_browsers(tmp_path):
	"""HTML 渲染失败不能用 patchright 文案：该格式从不启动浏览器（Issue #1031）。

	Issue #1009 把内核缺失与本地 IO 分流后，通用渲染失败仍直接复用错误码
	登记表里拆分前的恢复文案，用户会被告知去装一个与 HTML 导出无关的内核。
	"""
	runner = CliRunner()
	_invoke(runner, tmp_path, ["init", "--name", "htmlrender", "--template", "default"])

	with patch("boss_agent_cli.resume.export.export_html", side_effect=RuntimeError("template render exploded")):
		result = _invoke(runner, tmp_path, ["export", "htmlrender", "--format", "html"])

	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["error"]["code"] == "EXPORT_FAILED"
	assert "patchright" not in parsed["error"]["recovery_action"]
	assert "不使用浏览器内核" in parsed["error"]["recovery_action"]
	assert "template render exploded" in parsed["error"]["message"]


def test_cli_export_json_output_path_prepare_failure_is_enveloped(tmp_path):
	"""建父目录就失败（目标父路径是普通文件）时也要给 LOCAL_IO_ERROR 信封。

	``resume_cmd`` 没有统一异常装饰器：``prepare_output_path`` 在 try 之外抛
	OSError 时会落进顶层网兜，被报成「resume 本地文件读写失败」而不是导出失败。
	"""
	runner = CliRunner()
	_invoke(runner, tmp_path, ["init", "--name", "jsondir", "--template", "default"])
	blocker = tmp_path / "blocker"
	blocker.write_text("not a directory", encoding="utf-8")

	result = _invoke(
		runner,
		tmp_path,
		["export", "jsondir", "--format", "json", "-o", str(blocker / "x.json")],
	)

	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["error"]["code"] == "LOCAL_IO_ERROR"
	assert parsed["error"]["recoverable"] is False


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


# ── PDF 符号链接拒绝（Issue #1434） ─────────────────────────────


def test_export_pdf_refuses_symlink_target(tmp_path):
	"""`-o` 指向符号链接时必须拒绝，不能 os.replace 掉链接本身（与 #1355 口径一致）。"""
	real = tmp_path / "real.pdf"
	real.write_text("old", encoding="utf-8")
	link = tmp_path / "link.pdf"
	link.symlink_to(real)

	resume = _make_resume()
	with pytest.raises(OSError) as caught:
		export_pdf(resume, link)

	assert caught.value.filename == str(link)
	assert real.read_text(encoding="utf-8") == "old"
	assert link.is_symlink()
