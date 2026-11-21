"""批次 95 审计修复：本地 JSON 超长整数与聊天统计同源（Issue #2387、#2388、#2392）。"""

from __future__ import annotations

import json
from pathlib import Path

from boss_agent_cli.services.chat_export import prepare_render_data, render_export
from boss_agent_cli.services.job_export import write_export_file
from boss_agent_cli.services.json_files import strict_json_text, write_json_atomic

_HUGE = 10**5000
_BIG = 2**63


# ── Issue #2388：本地 JSON 写入把超长整数归一为 null ──────────────────


def test_write_json_atomic_normalizes_oversized_int(tmp_path: Path) -> None:
	target = tmp_path / "state.json"
	write_json_atomic(target, {"n": _HUGE, "items": [_HUGE], "flag": True, "big": _BIG})

	payload = json.loads(target.read_text(encoding="utf-8"))
	assert payload == {"n": None, "items": [None], "flag": True, "big": _BIG}


def test_strict_json_text_normalizes_oversized_int() -> None:
	assert json.loads(strict_json_text({"n": _HUGE}))["n"] is None


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
