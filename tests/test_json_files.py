"""本地 JSON 状态原子写入：中断时保留原文件，不留下临时文件。"""

import json
import os

import pytest

from boss_agent_cli.services.json_files import write_json_atomic


def test_writes_json_content(tmp_path):
	path = tmp_path / "state.json"
	write_json_atomic(path, {"名字": "张三", "count": 2})

	assert json.loads(path.read_text(encoding="utf-8")) == {"名字": "张三", "count": 2}
	# ensure_ascii=False：本地状态里的中文保持可读
	assert "张三" in path.read_text(encoding="utf-8")


def test_creates_parent_directories(tmp_path):
	path = tmp_path / "a" / "b" / "state.json"
	write_json_atomic(path, [])
	assert path.exists()


def test_trailing_newline_option(tmp_path):
	path = tmp_path / "state.json"
	write_json_atomic(path, {"a": 1}, trailing_newline=True)
	assert path.read_text(encoding="utf-8").endswith("\n")


def test_failed_replace_keeps_previous_file(tmp_path, monkeypatch):
	"""写到一半失败（磁盘满、替换失败）时原文件必须完好，否则配置/简历直接变成半截 JSON。"""
	path = tmp_path / "state.json"
	write_json_atomic(path, {"version": 1})
	original = path.read_bytes()

	def fail_replace(source, destination):
		raise OSError("disk full")

	monkeypatch.setattr(os, "replace", fail_replace)
	with pytest.raises(OSError):
		write_json_atomic(path, {"version": 2})

	assert path.read_bytes() == original
	assert [item.name for item in tmp_path.iterdir()] == ["state.json"]


def test_unserializable_value_keeps_previous_file(tmp_path):
	path = tmp_path / "state.json"
	write_json_atomic(path, {"version": 1})

	with pytest.raises(TypeError):
		write_json_atomic(path, {"bad": object()})

	assert json.loads(path.read_text(encoding="utf-8")) == {"version": 1}
	assert [item.name for item in tmp_path.iterdir()] == ["state.json"]
