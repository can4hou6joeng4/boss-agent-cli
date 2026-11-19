"""审计批次：stats HTML 代理字符归一、chat 导出身份口径、快照守卫 identity（Issue #2109–#2111）。"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner

from boss_agent_cli.commands import stats as stats_module
from boss_agent_cli.main import cli
from boss_agent_cli.services import chat_export, chat_snapshot
from boss_agent_cli.services.text_files import sanitize_surrogates


# ── #2109：HTML 产物先归一孤立代理字符再计算字节数 ─────────────────


def test_sanitize_surrogates_escapes_dangling_surrogates() -> None:
	cleaned = sanitize_surrogates("路径 /tmp/\udcff/报表")
	cleaned.encode("utf-8")  # 归一后必须能严格编码
	assert "\\udcff" in cleaned


def test_sanitize_surrogates_keeps_encodable_text_unchanged() -> None:
	assert sanitize_surrogates("普通文本 /tmp/data") == "普通文本 /tmp/data"


def test_stats_html_output_sanitizes_dangling_surrogates_in_hints(
	tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
	# 模拟 Linux 上经 surrogateescape 解码的 --data-dir 路径进入 hints。
	monkeypatch.setattr(
		stats_module,
		"boss_command_for_ctx",
		lambda *args, **kwargs: "boss --data-dir /tmp/\udcff stats pipeline",
	)
	out = tmp_path / "report.html"
	result = CliRunner().invoke(
		cli,
		["--data-dir", str(tmp_path), "--json", "stats", "--format", "html", "-o", str(out)],
	)
	assert result.exit_code == 0, result.output
	envelope = json.loads(result.output)
	assert envelope["ok"] is True
	body = out.read_text(encoding="utf-8")
	assert "\\udcff" in body
	# 文件已写出且 bytes 与实际内容一致，不能出现「写盘成功却报失败」。
	assert envelope["data"]["bytes"] == len(body.encode("utf-8"))


# ── #2110：身份用原始值计算，展示归一不得绕过 #2064 ───────────────


def test_chat_export_identity_uses_raw_values_before_text_conversion() -> None:
	data = chat_export.prepare_render_data(
		[
			{"uid": True, "security_id": "sec-1", "initiated_by": "我主动"},
			{"uid": {"a": 1}, "security_id": "sec-2", "initiated_by": "我主动"},
		],
		None,
		{"is_first": True},
	)
	keys = [entry[1] for entry in data["id_map"]]
	assert keys == ["sec-1", "sec-2"]


def test_chat_export_is_new_marks_match_raw_identity() -> None:
	friend = {"uid": True, "security_id": "sec-1", "initiated_by": "我主动"}
	data = chat_export.prepare_render_data(
		[friend],
		None,
		{"is_first": False, "added": [dict(friend)], "removed": []},
	)
	row = data["sections"][0]["rows"][0]
	assert row["key"] == "sec-1"
	assert row["is_new"] is True


def test_chat_export_identity_keeps_string_uid() -> None:
	data = chat_export.prepare_render_data(
		[{"uid": "10001", "security_id": "sec-x", "initiated_by": "我主动"}],
		None,
		{"is_first": True},
	)
	assert data["id_map"][0][1] == "10001"


# ── #2111：load_snapshot 的「可用」与合并路径同口径，需带 identity ──


def test_prev_snapshot_of_identityless_dicts_is_not_a_baseline(tmp_path: Path) -> None:
	prev = tmp_path / "2026-01-01.json"
	prev.write_text(json.dumps([{}]), encoding="utf-8")

	result = chat_snapshot.save_snapshot_and_diff(
		str(tmp_path),
		[{"uid": "u1", "security_id": "s1", "name": "A"}],
		MagicMock(),
		complete=True,
	)

	assert result["is_first"] is True
	assert result["added"] == []


def test_truly_empty_prev_snapshot_remains_valid_empty_baseline(tmp_path: Path) -> None:
	prev = tmp_path / "2026-01-01.json"
	prev.write_text("[]", encoding="utf-8")

	result = chat_snapshot.save_snapshot_and_diff(
		str(tmp_path),
		[{"uid": "u1", "security_id": "s1", "name": "A"}],
		MagicMock(),
		complete=True,
	)

	assert result["is_first"] is False
	assert len(result["added"]) == 1


def test_prev_snapshot_with_identity_still_diffs_as_before(tmp_path: Path) -> None:
	prev = tmp_path / "2026-01-01.json"
	prev.write_text(
		json.dumps([{"uid": "u1", "security_id": "s1", "name": "A"}]),
		encoding="utf-8",
	)

	result = chat_snapshot.save_snapshot_and_diff(
		str(tmp_path),
		[{"uid": "u1", "security_id": "s1", "name": "A"}, {"uid": "u2", "security_id": "s2", "name": "B"}],
		MagicMock(),
		complete=True,
	)

	assert result["is_first"] is False
	assert [item["uid"] for item in result["added"]] == ["u2"]
