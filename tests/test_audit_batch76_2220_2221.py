"""第七轮审计批次：Issue #2220–#2221 的回归测试。

- #2220：JSON 序列化边界的 TypeError 归成本地 IO 错误，不再裸穿出命令层。
- #2221：聊天快照服务边界过滤非对象条目，混入 null / 字符串不整次失败。
"""

from __future__ import annotations

import errno
import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from boss_agent_cli.output import Logger
from boss_agent_cli.services import chat_snapshot as snapshot
from boss_agent_cli.services.json_files import strict_json_text, write_json_atomic


# ── #2220：不可序列化的值按 LOCAL_IO_ERROR 报出 ─────────────────────


@pytest.mark.parametrize(
	"value",
	[
		{("a", "b"): 1},
		b"bytes",
		{"x": {1, 2}},
		{1, 2},
	],
)
def test_write_json_atomic_wraps_unserializable_values(tmp_path: Path, value):
	target = tmp_path / "state.json"
	target.write_text('{"keep": true}', encoding="utf-8")

	with pytest.raises(OSError) as excinfo:
		write_json_atomic(target, value)

	assert excinfo.value.errno == errno.EINVAL
	assert "本地状态无法序列化" in str(excinfo.value)
	# 失败不破坏原文件，也不留下临时文件。
	assert target.read_text(encoding="utf-8") == '{"keep": true}'
	assert [path.name for path in tmp_path.iterdir()] == ["state.json"]


@pytest.mark.parametrize("value", [{"x": {1, 2}}, b"bytes", {1, 2}])
def test_strict_json_text_wraps_unserializable_values(value):
	with pytest.raises(OSError) as excinfo:
		strict_json_text(value)

	assert excinfo.value.errno == errno.EINVAL


def test_strict_json_text_still_serializes_supported_values():
	assert strict_json_text({"a": [1, 2], "b": None}) == '{\n  "a": [\n    1,\n    2\n  ],\n  "b": null\n}'


# ── #2221：快照服务边界过滤非对象条目 ───────────────────────────────


def _write_previous(directory: Path, payload: list[object]) -> Path:
	path = directory / f"{date.today() - timedelta(days=1)}.json"
	path.write_text(json.dumps(payload), encoding="utf-8")
	return path


def _today_meta(directory: Path) -> dict[str, object]:
	path = directory / f"{date.today()}.meta"
	return json.loads(path.read_text(encoding="utf-8"))


def test_snapshot_filters_non_object_entries(tmp_path: Path) -> None:
	diff = snapshot.save_snapshot_and_diff(
		str(tmp_path), [{"uid": "100"}, None, "x", 3], Logger(), complete=True
	)

	# 非对象条目被丢弃后不能声称完整（与缺少身份的条目同口径）。
	assert diff["complete"] is False
	assert _today_meta(tmp_path) == {"complete": False}
	assert json.loads((tmp_path / f"{date.today()}.json").read_text(encoding="utf-8")) == [{"uid": "100"}]


def test_snapshot_non_object_entries_suppress_removed(tmp_path: Path) -> None:
	_write_previous(tmp_path, [{"uid": "100"}, {"uid": "200"}])

	diff = snapshot.save_snapshot_and_diff(
		str(tmp_path), [{"uid": "100"}, None], Logger(), complete=True
	)

	# 丢弃过条目时本次观察不完整，不能推断 200 已消失。
	assert diff["removed"] == []
	assert diff["complete"] is False


def test_snapshot_all_objects_keeps_complete(tmp_path: Path) -> None:
	diff = snapshot.save_snapshot_and_diff(
		str(tmp_path), [{"uid": "100"}, {"uid": "200"}], Logger(), complete=True
	)

	assert diff["complete"] is True
	assert _today_meta(tmp_path) == {"complete": True}
