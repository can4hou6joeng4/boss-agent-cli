"""导出单元格与快照合并边界回归（Issue #2015–#2018）。"""

from __future__ import annotations

import datetime
import json
from pathlib import Path
from unittest.mock import MagicMock

from boss_agent_cli.services import chat_snapshot
from boss_agent_cli.services.chat_export import _text_cell
from boss_agent_cli.services.job_export import _cell_text, render_html_export


def _snapshot_path(directory: Path, days_ago: int) -> Path:
	day = datetime.date.today() - datetime.timedelta(days=days_ago)
	return directory / f"{day.isoformat()}.json"


def _meta_path(snapshot: Path) -> Path:
	return Path(str(snapshot)[: -len(".json")] + ".meta")


def _friend(uid: str) -> dict[str, object]:
	return {"uid": uid, "security_id": f"sid-{uid}", "name": f"联系人{uid}"}


# ── #2015：非空但零个可用对象的当日数组不是完整基线 ──────────────────


def test_nonempty_array_without_usable_items_is_not_complete_baseline(tmp_path: Path) -> None:
	today = _snapshot_path(tmp_path, 0)
	today.write_text(json.dumps([1, "x", None]), encoding="utf-8")
	_meta_path(today).write_text(json.dumps({"complete": True}), encoding="utf-8")

	chat_snapshot.save_snapshot_and_diff(str(tmp_path), [_friend("u9")], MagicMock(), complete=False)

	assert json.loads(_meta_path(today).read_text(encoding="utf-8")) == {"complete": False}


def test_truly_empty_today_array_keeps_complete_marker(tmp_path: Path) -> None:
	today = _snapshot_path(tmp_path, 0)
	today.write_text("[]", encoding="utf-8")
	_meta_path(today).write_text(json.dumps({"complete": True}), encoding="utf-8")

	chat_snapshot.save_snapshot_and_diff(str(tmp_path), [_friend("u9")], MagicMock(), complete=False)

	assert json.loads(_meta_path(today).read_text(encoding="utf-8")) == {"complete": True}


def test_usable_entries_still_merge_and_keep_marker(tmp_path: Path) -> None:
	today = _snapshot_path(tmp_path, 0)
	today.write_text(json.dumps([_friend("u1")]), encoding="utf-8")
	_meta_path(today).write_text(json.dumps({"complete": True}), encoding="utf-8")

	chat_snapshot.save_snapshot_and_diff(str(tmp_path), [_friend("u2")], MagicMock(), complete=False)

	stored = json.loads(today.read_text(encoding="utf-8"))
	assert [item["uid"] for item in stored] == ["u1", "u2"]
	assert json.loads(_meta_path(today).read_text(encoding="utf-8")) == {"complete": True}


# ── #2016：深嵌套容器按缺失处理，不让整份导出失败 ────────────────────


def _deep_container() -> list:
	value: list = []
	for _ in range(3000):
		value = [value]
	return value


def test_cell_text_treats_unserializable_container_as_missing() -> None:
	assert _cell_text(_deep_container()) == ""
	assert _text_cell(_deep_container()) == ""


def test_html_export_survives_unserializable_container() -> None:
	html = render_html_export([{"title": _deep_container()}])

	assert "<html" in html


# ── #2017：孤立代理项在渲染层归一，stdout 严格编码不再崩 ─────────────


def test_html_export_normalizes_lone_surrogates() -> None:
	html = render_html_export([{"title": "\ud800"}])

	assert html.encode("utf-8")


def test_cell_text_normalizes_lone_surrogates() -> None:
	assert _cell_text("\ud800").encode("utf-8")
	assert _text_cell("\ud800").encode("utf-8")


# ── #2018：布尔单元格使用 JSON 字面量 ────────────────────────────────


def test_bool_cells_use_json_literals() -> None:
	assert _cell_text(True) == "true"
	assert _cell_text(False) == "false"
	assert _text_cell(True) == "true"
	assert _text_cell(False) == "false"


def test_html_export_does_not_render_python_bool_literals() -> None:
	html = render_html_export([{"title": False, "skills": [True]}])

	assert "False" not in html
	assert "True" not in html
	assert "false" in html and "true" in html
