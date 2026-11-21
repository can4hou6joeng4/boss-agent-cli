"""批次 95 审计修复：本地 JSON 超长整数与聊天统计同源（Issue #2387、#2388、#2392）。"""

from __future__ import annotations

import errno
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from boss_agent_cli.services import chat_snapshot
from boss_agent_cli.services.chat_export import prepare_render_data, render_export
from boss_agent_cli.services.job_card import text_items
from boss_agent_cli.services.job_export import write_export_file
from boss_agent_cli.services.json_files import strict_json_text, write_json_atomic

_HUGE = 10**5000
_BIG = 2**63


# ── Issue #2388：导出 / 快照按 null 降级，本地状态仍报 EINVAL ─────────


def test_local_state_write_keeps_einval_for_oversized_int(tmp_path: Path) -> None:
	"""本地状态写入保留 Issue #1891 契约：超长整数是本地 IO 错误，不是静默 null。"""
	target = tmp_path / "state.json"
	with pytest.raises(OSError) as excinfo:
		write_json_atomic(target, {"n": _HUGE})

	assert excinfo.value.errno == errno.EINVAL
	assert not target.exists(), "序列化失败不得留下半截文件"


def test_write_json_atomic_can_opt_into_null_for_oversized_int(tmp_path: Path) -> None:
	target = tmp_path / "state.json"
	write_json_atomic(
		target,
		{"n": _HUGE, "items": [_HUGE], "flag": True, "big": _BIG},
		null_oversized_ints=True,
	)

	payload = json.loads(target.read_text(encoding="utf-8"))
	assert payload == {"n": None, "items": [None], "flag": True, "big": _BIG}


def test_strict_json_text_default_raises_and_opt_in_normalizes() -> None:
	with pytest.raises(OSError):
		strict_json_text({"n": _HUGE})
	assert json.loads(strict_json_text({"n": _HUGE}, null_oversized_ints=True))["n"] is None


def test_text_items_huge_int_contract_unchanged() -> None:
	"""展示字段仍按缺失处理（Issue #2065）：不因批次 95 的导出降级变成 "[null]"。"""
	assert text_items({"jobLabels": [[_HUGE]]}, "jobLabels") == []


def test_chat_snapshot_survives_oversized_int(tmp_path: Path) -> None:
	friend = {"uid": "u1", "name": "A", "unread": _HUGE}

	chat_snapshot.save_snapshot_and_diff(str(tmp_path), [friend], MagicMock(), complete=True)

	(snapshot_path,) = tmp_path.glob("*.json")
	payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
	assert payload[0]["unread"] is None
	assert payload[0]["name"] == "A"


# ── Issue #2387：导出 JSON 与 CSV / HTML 的降级口径一致 ─────────────


def test_job_json_export_survives_oversized_int(tmp_path: Path) -> None:
	target = tmp_path / "jobs.json"
	write_export_file([{"title": "T", "salary": _HUGE}], "json", str(target))

	payload = json.loads(target.read_text(encoding="utf-8"))
	assert payload[0]["salary"] is None
	assert payload[0]["title"] == "T"


def test_chat_json_export_survives_oversized_int() -> None:
	text = render_export(
		[{"name": "A", "initiated_by": "对方主动", "unread": _HUGE}],
		"json",
		None,
		None,
		{},
	)

	payload = json.loads(text)
	assert payload[0]["unread"] is None


# ── Issue #2392：from_who 的统计与 sections 同源 ────────────────────


def test_from_who_statistics_match_visible_sections() -> None:
	friends = [
		{"name": "A", "initiated_by": "对方主动"},
		{"name": "B", "initiated_by": "我主动"},
	]

	boss = prepare_render_data(friends, "boss", {})
	assert boss["total"] == 1
	assert boss["count_parts"] == ["对方主动 1"]
	assert [section["subtitle"] for section in boss["sections"]] == ["对方主动（1 条）"]

	me = prepare_render_data(friends, "me", {})
	assert me["total"] == 1
	assert me["count_parts"] == ["我主动 1"]
	assert len(me["sections"]) == 1
	assert me["sections"][0]["subtitle"].startswith("我主动（1 条")

	everything = prepare_render_data(friends, None, {})
	assert everything["total"] == 2
	assert everything["count_parts"] == ["对方主动 1", "我主动 1"]
