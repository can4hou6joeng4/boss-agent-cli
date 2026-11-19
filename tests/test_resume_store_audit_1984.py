"""resume 存储与导出边界回归（Issue #1984–#1986）。"""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pytest

from boss_agent_cli.resume.export import export_pdf
from boss_agent_cli.resume.models import ResumeData
from boss_agent_cli.resume.store import ResumeImportError, ResumeStore
from boss_agent_cli.services.json_files import write_json_atomic
from boss_agent_cli.services.text_files import write_text_atomic


def test_list_uses_filename_when_stored_name_points_elsewhere(tmp_path: Path) -> None:
	"""复制出来的副本以自身文件名列出，删掉原文件后仍能被 show/delete 点名（#1984）。"""
	store = ResumeStore(tmp_path / "resumes")
	store.save(ResumeData(name="foo", title="后端工程师"))
	original = store._path_for("foo")
	(store._dir / "backup.json").write_bytes(original.read_bytes())

	items, corrupt = store.list_all_with_corrupt()

	assert corrupt == []
	names = sorted(item["name"] for item in items)
	assert names == ["backup", "foo"]
	assert store.get("backup") is not None

	store.delete("foo")
	assert store.get("foo") is None
	assert store.get("backup") is not None
	items, _ = store.list_all_with_corrupt()
	assert [item["name"] for item in items] == ["backup"]


def test_export_envelope_without_name_is_listed_by_filename(tmp_path: Path) -> None:
	"""缺 name 的导出信封不再产生无法点名的空名幽灵行（#1984）。"""
	store = ResumeStore(tmp_path / "resumes")
	(store._dir / "envelope.json").write_text(
		json.dumps({"version": 1, "data": {"title": "t"}, "metadata": {}}), encoding="utf-8"
	)

	items, corrupt = store.list_all_with_corrupt()

	assert corrupt == []
	assert [item["name"] for item in items] == ["envelope"]
	assert store.get("envelope") is not None


def test_long_resume_name_atomic_write_succeeds(tmp_path: Path) -> None:
	"""241 字节名称的简历文件可以正常写入（临时名不再拼完整目标名，#1985）。"""
	store = ResumeStore(tmp_path / "resumes")
	name = "n" * 241

	store.save(ResumeData(name=name, title="t"))

	assert store.get(name) is not None


def test_long_output_filename_atomic_text_write_succeeds(tmp_path: Path) -> None:
	"""250 字节的输出文件名不再因临时名过长而 ENAMETOOLONG（#1985）。"""
	target = tmp_path / ("x" * 250)
	write_text_atomic(target, "body")
	assert target.read_text(encoding="utf-8") == "body"


def test_long_output_filename_atomic_json_write_succeeds(tmp_path: Path) -> None:
	target = tmp_path / ("y" * 250)
	write_json_atomic(target, {"ok": True})
	assert json.loads(target.read_text(encoding="utf-8")) == {"ok": True}


def _install_fake_patchright(monkeypatch) -> None:
	class _Page:
		def route(self, *args, **kwargs) -> None:
			pass

		def set_content(self, *args, **kwargs) -> None:
			pass

		def pdf(self, *, path: str, **kwargs) -> None:
			Path(path).write_bytes(b"%PDF-fake")

	class _Browser:
		def new_page(self):
			return _Page()

		def close(self) -> None:
			pass

	class _Chromium:
		def launch(self, *, headless: bool):
			return _Browser()

	class _Playwright:
		chromium = _Chromium()

	class _Context:
		def __enter__(self):
			return _Playwright()

		def __exit__(self, *args) -> bool:
			return False

	module = types.ModuleType("patchright.sync_api")
	module.sync_playwright = lambda: _Context()  # type: ignore[attr-defined]
	package = types.ModuleType("patchright")
	package.sync_api = module  # type: ignore[attr-defined]
	monkeypatch.setitem(sys.modules, "patchright", package)
	monkeypatch.setitem(sys.modules, "patchright.sync_api", module)


def test_export_pdf_long_output_name_succeeds(tmp_path: Path, monkeypatch) -> None:
	"""长但合法的 PDF 输出名不再触发 ENAMETOOLONG（#1985）。"""
	_install_fake_patchright(monkeypatch)
	target = tmp_path / ("p" * 240 + ".pdf")

	export_pdf(ResumeData(name="n", title="t"), target)

	assert target.read_bytes() == b"%PDF-fake"
	assert list(tmp_path.glob(".boss-pdf-*")) == [], "临时文件必须清理"


def test_import_rejects_name_with_lone_surrogate_as_invalid_param(tmp_path: Path) -> None:
	"""孤立低代理项名称按导入参数错误拒绝，而不是 LOCAL_IO_ERROR（#1986）。"""
	store = ResumeStore(tmp_path / "resumes")
	source = tmp_path / "resume.json"
	source.write_text(json.dumps({"name": "a\udcffb", "title": "t"}), encoding="utf-8")

	with pytest.raises(ResumeImportError) as exc_info:
		store.import_file(source)

	assert "孤立代理项" in str(exc_info.value)
