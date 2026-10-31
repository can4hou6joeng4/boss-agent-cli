"""scripts/update_contributors.py：只在贡献者或布局变化时改写 CONTRIBUTORS.svg。"""

from __future__ import annotations

import importlib
import sys
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _module(monkeypatch):
	monkeypatch.syspath_prepend(str(ROOT / "scripts"))
	monkeypatch.delitem(sys.modules, "update_contributors", raising=False)
	return importlib.import_module("update_contributors")


def _svg(avatar: str, name: str = "alice") -> str:
	data = f"data:image/png;base64,{avatar}"
	return f'<svg><image xlink:href="{data}" href="{data}" /><text>{name}</text></svg>\n'


def test_import_makes_no_network_calls(monkeypatch):
	def refuse(*args, **kwargs):
		raise AssertionError("导入脚本不应发网络请求")

	monkeypatch.setattr(urllib.request, "urlopen", refuse)
	module = _module(monkeypatch)
	assert callable(module.main)


def test_avatar_bytes_alone_keep_existing_file(tmp_path, monkeypatch):
	"""头像 CDN 给出另一种编码时不改写，避免每次合并都刷出新的贡献者 PR。"""
	module = _module(monkeypatch)
	out = tmp_path / "CONTRIBUTORS.svg"
	out.write_text(_svg("AAAA"), encoding="utf-8")

	assert module.write_if_changed(_svg("BBBB"), str(out)) is False
	assert "AAAA" in out.read_text(encoding="utf-8")


@pytest.mark.parametrize("change", ["name", "contributor"])
def test_layout_change_rewrites_file(tmp_path, monkeypatch, change):
	module = _module(monkeypatch)
	out = tmp_path / "CONTRIBUTORS.svg"
	out.write_text(_svg("AAAA"), encoding="utf-8")
	new_svg = _svg("BBBB", name="bob") if change == "name" else _svg("AAAA") + _svg("CCCC", name="carol")

	assert module.write_if_changed(new_svg, str(out)) is True
	assert out.read_text(encoding="utf-8") == new_svg


def test_missing_file_is_written(tmp_path, monkeypatch):
	module = _module(monkeypatch)
	out = tmp_path / "CONTRIBUTORS.svg"

	assert module.write_if_changed(_svg("AAAA"), str(out)) is True
	assert out.exists()


def test_checked_in_svg_layout_ignores_only_embedded_images(monkeypatch):
	module = _module(monkeypatch)
	svg = (ROOT / "CONTRIBUTORS.svg").read_text(encoding="utf-8")

	stripped = module.layout(svg)
	assert "base64," not in stripped
	assert stripped.count("<image ") == svg.count("<image ")
	assert stripped.count("clip-path=") == svg.count("clip-path=")
