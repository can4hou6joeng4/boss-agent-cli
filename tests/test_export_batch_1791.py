"""聊天 / 职位导出的四项边界（Issue #1791 #1792 #1793 #1794）。"""

from __future__ import annotations

import csv
import datetime
import io
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from boss_agent_cli.api.models import JobItem
from boss_agent_cli.services import chat_snapshot
from boss_agent_cli.services.chat_export import prepare_render_data, render_export
from boss_agent_cli.services.chat_utils import escape_md_cell, sanitize_csv_cell
from boss_agent_cli.services.job_export import prepare_export_items, write_export_file


def _friend(uid: str, **overrides: object) -> dict[str, object]:
	item: dict[str, object] = {
		"name": f"联系人{uid}",
		"title": "招聘经理",
		"brand_name": "示例公司",
		"initiated_by": "我主动",
		"last_msg": "你好",
		"last_time": "11-18",
		"last_ts": 0,
		"msg_status": "已读",
		"uid": uid,
		"security_id": f"sid-{uid}",
		"encrypt_job_id": "job-1",
		"unread": 0,
	}
	item.update(overrides)
	return item


def _snapshot_path(directory: Path, days_ago: int) -> Path:
	day = datetime.date.today() - datetime.timedelta(days=days_ago)
	return directory / f"{day.isoformat()}.json"


def _uids(items: list[dict[str, object]]) -> list[object]:
	return sorted(item["uid"] for item in items)


# ── #1791：完整采集替换当日快照 ─────────────────────────────


def test_complete_rerun_reports_removed_contact_the_same_day(tmp_path):
	_snapshot_path(tmp_path, 1).write_text(json.dumps([_friend("A"), _friend("B")]), encoding="utf-8")
	logger = MagicMock()
	first = chat_snapshot.save_snapshot_and_diff(str(tmp_path), [_friend("A"), _friend("B")], logger, complete=True)
	assert first["removed"] == []
	second = chat_snapshot.save_snapshot_and_diff(str(tmp_path), [_friend("A")], logger, complete=True)
	assert _uids(second["removed"]) == ["B"]
	today = json.loads(_snapshot_path(tmp_path, 0).read_text(encoding="utf-8"))
	assert _uids(today) == ["A"]


def test_incomplete_pages_still_merge_into_todays_snapshot(tmp_path):
	_snapshot_path(tmp_path, 1).write_text(json.dumps([_friend("A"), _friend("B")]), encoding="utf-8")
	logger = MagicMock()
	chat_snapshot.save_snapshot_and_diff(str(tmp_path), [_friend("A")], logger, complete=False)
	result = chat_snapshot.save_snapshot_and_diff(str(tmp_path), [_friend("B")], logger, complete=False)
	assert result["removed"] == []
	today = json.loads(_snapshot_path(tmp_path, 0).read_text(encoding="utf-8"))
	assert _uids(today) == ["A", "B"]


def test_complete_run_still_refuses_an_unreadable_snapshot_path(tmp_path):
	_snapshot_path(tmp_path, 0).mkdir()
	with pytest.raises(OSError):
		chat_snapshot.save_snapshot_and_diff(str(tmp_path), [_friend("A")], MagicMock(), complete=True)


# ── #1792：Markdown 单元格转义覆盖非字符串 ───────────────────


@pytest.mark.parametrize(
	"value",
	[["A|B"], {"k": "x\n| S9 | 伪造行 |"}, ["[点我](https://example.invalid)"], ("<img src=x>",)],
)
def test_escape_md_cell_escapes_non_string_values(value):
	escaped = escape_md_cell(value)
	assert "\n" not in escaped
	stripped = escaped.replace("\\|", "").replace("\\[", "").replace("\\]", "").replace("\\<", "")
	assert "|" not in stripped and "[" not in stripped and "<" not in stripped


def test_markdown_row_keeps_its_column_count_with_drifted_cells():
	markdown = render_export(
		[_friend("1", brand_name=["A|B"], title={"k": "x\n| S9 | 伪造行 |"}, last_time=["[点我](https://example.invalid)"])],
		"md",
		None,
		None,
		{"is_first": True},
	)
	row = next(line for line in markdown.splitlines() if line.startswith("| S1 "))
	assert len(row.replace("\\|", "").split("|")) == 10
	assert "[点我](" not in row


def test_escape_md_cell_leaves_plain_text_alone():
	assert escape_md_cell("示例公司") == "示例公司"
	assert escape_md_cell(3) == "3"


# ── #1793：CSV 的 null 与占位符 ─────────────────────────────


def test_job_csv_writes_missing_values_as_empty_cells(tmp_path):
	target = tmp_path / "jobs.csv"
	item = JobItem.from_api({}).to_dict()
	assert item["raw_job_type"] is None
	write_export_file(prepare_export_items([item], include_private=True), "csv", str(target))
	rows = list(csv.DictReader(io.StringIO(target.read_text(encoding="utf-8-sig"))))
	assert rows[0]["raw_job_type"] == ""
	assert "None" not in rows[0].values()


def test_chat_csv_keeps_placeholder_and_blanks_null():
	text = render_export([_friend("1", name="-", title="-", last_msg="-", last_time=None)], "csv", None, None, {})
	row = next(csv.DictReader(io.StringIO(text)))
	assert row["name"] == "-"
	assert row["title"] == "-"
	assert row["last_msg"] == "-"
	assert row["last_time"] == ""


@pytest.mark.parametrize("value", ["-1+2", "=1+1", "+1", "@SUM(A1)", "--", "-x"])
def test_formula_like_cells_are_still_prefixed(value):
	assert sanitize_csv_cell(value) == f"'{value}"


def test_lone_dash_is_not_a_formula():
	assert sanitize_csv_cell("-") == "-"


# ── #1794：未读计数 ─────────────────────────────────────────


def test_unknown_status_is_not_counted_as_unread():
	data = prepare_render_data(
		[_friend("1", msg_status="未知"), _friend("2", msg_status="已读"), _friend("3", msg_status="未读")],
		None,
		{},
	)
	assert "已读 1 / 未读 1" in data["sections"][0]["subtitle"]
