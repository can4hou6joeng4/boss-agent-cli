"""chat 快照完整性、已消失渲染与坏页完整性的边界（Issue #1911–#1914）。"""

from __future__ import annotations

import datetime
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from boss_agent_cli.main import cli
from boss_agent_cli.services import chat_snapshot
from boss_agent_cli.services.chat_export import render_export


def _friend(uid: str) -> dict[str, object]:
	return {"uid": uid, "security_id": f"sid-{uid}", "name": f"联系人{uid}", "unread": 0, "msg_status": "已读"}


def _snapshot_path(directory: Path, days_ago: int) -> Path:
	day = datetime.date.today() - datetime.timedelta(days=days_ago)
	return directory / f"{day.isoformat()}.json"


def _meta_path(snapshot: Path) -> Path:
	return Path(str(snapshot)[: -len(".json")] + ".meta")


# ── Issue #1911：完整性标记只认显式 true ────────────────────────────


@pytest.mark.parametrize(
	"marker",
	[
		{"complete": 0},
		{"complete": "false"},
		{"complete": None},
		{"complete": False},
		{},
		[False],
	],
)
def test_non_boolean_marker_is_treated_as_incomplete(tmp_path: Path, marker: object) -> None:
	prev = _snapshot_path(tmp_path, 1)
	prev.write_text(json.dumps([_friend("A")]), encoding="utf-8")
	_meta_path(prev).write_text(json.dumps(marker), encoding="utf-8")

	result = chat_snapshot.save_snapshot_and_diff(
		str(tmp_path), [_friend("A"), _friend("B")], MagicMock(), complete=True
	)

	assert result["baseline_complete"] is False
	assert result["added"] == []


def test_explicit_true_marker_still_reports_added(tmp_path: Path) -> None:
	prev = _snapshot_path(tmp_path, 1)
	prev.write_text(json.dumps([_friend("A")]), encoding="utf-8")
	_meta_path(prev).write_text(json.dumps({"complete": True}), encoding="utf-8")

	result = chat_snapshot.save_snapshot_and_diff(
		str(tmp_path), [_friend("A"), _friend("B")], MagicMock(), complete=True
	)

	assert result["baseline_complete"] is True
	assert [item["uid"] for item in result["added"]] == ["B"]


# ── Issue #1912：坏当日快照不能写成完整基线 ──────────────────────────


def test_corrupt_today_snapshot_with_incomplete_collection_stays_incomplete(tmp_path: Path) -> None:
	today = _snapshot_path(tmp_path, 0)
	today.write_text("not valid json", encoding="utf-8")

	result = chat_snapshot.save_snapshot_and_diff(
		str(tmp_path), [_friend("A")], MagicMock(), complete=False
	)

	assert result["complete"] is False
	assert json.loads(_meta_path(today).read_text(encoding="utf-8")) == {"complete": False}


def test_crash_between_snapshot_and_marker_leaves_conservative_marker(tmp_path: Path, monkeypatch) -> None:
	real_write = chat_snapshot.write_json_atomic

	def flaky_write(path, value):
		if Path(path).name.endswith(".json"):
			raise OSError(28, "No space left on device")
		return real_write(path, value)

	monkeypatch.setattr(chat_snapshot, "write_json_atomic", flaky_write)

	with pytest.raises(OSError):
		chat_snapshot.save_snapshot_and_diff(str(tmp_path), [_friend("A")], MagicMock(), complete=True)

	today = _snapshot_path(tmp_path, 0)
	assert json.loads(_meta_path(today).read_text(encoding="utf-8")) == {"complete": False}


# ── Issue #1913：「已消失」表格的容器字段序列化为 JSON 文本 ──────────


def _removed_diff() -> dict:
	return {
		"is_first": False,
		"prev_date": "2026-11-18",
		"added": [],
		"new_unread": [],
		"removed": [
			{"brand_name": {"k": "v"}, "name": ["alice"], "last_time": 1700000000000},
		],
	}


def test_removed_markdown_cells_serialize_containers_as_json() -> None:
	text = render_export([], "md", None, None, _removed_diff())

	assert '{"k": "v"}' in text
	# Markdown 仍转义方括号：JSON 文本而不是 Python repr。
	assert r'\["alice"\]' in text
	assert "{'k': 'v'}" not in text
	assert "['alice']" not in text


def test_removed_html_cells_do_not_fall_back_to_python_repr() -> None:
	text = render_export([], "html", None, None, _removed_diff())

	assert "[&#x27;alice&#x27;]" not in text
	assert "k" in text


# ── Issue #1914：坏页不能当成完整空列表 ──────────────────────────────


def _ctx_mock(mock_cls):
	instance = mock_cls.return_value
	instance.__enter__ = lambda self: self
	instance.__exit__ = lambda self, *a: None
	instance.unwrap_data.side_effect = lambda response: response.get("zpData")
	instance.is_success.side_effect = lambda response: response.get("code", 0) == 0
	return instance


def _seed_previous_snapshot(tmp_path: Path) -> None:
	snapshot_dir = tmp_path / "chat-history"
	snapshot_dir.mkdir()
	yesterday = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
	prev_item = {
		"name": "旧联系人",
		"title": "-",
		"brand_name": "-",
		"initiated_by": "对方主动",
		"last_msg": "旧消息",
		"last_time": "昨天",
		"last_ts": 1700000000000,
		"msg_status": "已读",
		"uid": "u_prev",
		"security_id": "sid_prev",
		"encrypt_job_id": "job_prev",
		"unread": 0,
	}
	(snapshot_dir / f"{yesterday}.json").write_text(json.dumps([prev_item], ensure_ascii=False), encoding="utf-8")


@patch("boss_agent_cli.commands.chat.get_platform_instance")
@patch("boss_agent_cli.commands.chat.AuthManager")
def test_missing_list_container_is_not_a_complete_empty_page(mock_auth_cls, mock_platform_cls, tmp_path: Path) -> None:
	_seed_previous_snapshot(tmp_path)
	platform = _ctx_mock(mock_platform_cls)
	platform.friend_list.return_value = {"zpData": {"hasMore": False}}

	result = CliRunner().invoke(
		cli,
		["--data-dir", str(tmp_path), "--json", "chat", "--export", "md", "-o", str(tmp_path / "out.md")],
	)

	assert result.exit_code == 0, result.output
	payload = json.loads(result.stdout)
	assert payload["ok"] is True
	assert payload["data"]["diff"]["removed"] == []
	today = _snapshot_path(tmp_path / "chat-history", 0)
	assert json.loads(_meta_path(today).read_text(encoding="utf-8")) == {"complete": False}


@patch("boss_agent_cli.commands.chat.get_platform_instance")
@patch("boss_agent_cli.commands.chat.AuthManager")
def test_present_empty_list_is_a_complete_page(mock_auth_cls, mock_platform_cls, tmp_path: Path) -> None:
	_seed_previous_snapshot(tmp_path)
	platform = _ctx_mock(mock_platform_cls)
	platform.friend_list.return_value = {"zpData": {"result": [], "hasMore": False}}

	result = CliRunner().invoke(
		cli,
		["--data-dir", str(tmp_path), "--json", "chat", "--export", "md", "-o", str(tmp_path / "out.md")],
	)

	assert result.exit_code == 0, result.output
	payload = json.loads(result.stdout)
	assert payload["ok"] is True
	assert [item["uid"] for item in payload["data"]["diff"]["removed"]] == ["u_prev"]
	today = _snapshot_path(tmp_path / "chat-history", 0)
	assert json.loads(_meta_path(today).read_text(encoding="utf-8")) == {"complete": True}
