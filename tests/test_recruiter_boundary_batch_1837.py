"""招聘者侧的未读计数、会话匹配与展示字段类型漂移（Issue #1837–#1839、#1841、#1842）。"""

import math
from pathlib import Path

import pytest

from boss_agent_cli.commands.recruiter._hints import blank_option_names
from boss_agent_cli.commands.recruiter.chat import _page_summaries
from boss_agent_cli.services.recruiter_chat_summary import (
	clean_unread_by_tab,
	format_chat_time,
	message_text,
)
from boss_agent_cli.commands.recruiter.location import _TargetError, _location_status, _target_from_detail


# ── Issue #1837：NaN / Infinity 未读计数 ─────────────────────────────


def test_clean_unread_by_tab_skips_non_finite_counts() -> None:
	cleaned = clean_unread_by_tab(
		{"1": math.nan, "2": math.inf, "3": -math.inf, "4": 2.0, "5": 3, "6": -1, "7": True, "8": "x"}
	)

	assert cleaned == {"4": 2, "5": 3}


class _SnapshotPlatform:
	def chat_list_snapshot(self) -> dict:
		return {"ok": True, "items": [], "unread_by_tab": {"1": math.nan, "2": 3}}


def test_page_summaries_survive_nan_unread_counts() -> None:
	summaries, meta = _page_summaries(_SnapshotPlatform())

	assert summaries == {}
	assert meta is not None
	assert meta["unread_by_tab"] == {"2": 3}


# ── Issue #1838：uid 为 null 时仍按 friendId 匹配 ────────────────────


def test_target_from_detail_matches_friend_id_when_uid_null() -> None:
	detail = {"zpData": {"friendList": [{"uid": None, "friendId": 123, "securityId": "s", "jobId": 9}]}}

	job_id, security_id, friend = _target_from_detail(detail, 123)

	assert (job_id, security_id) == (9, "s")
	assert friend["friendId"] == 123


@pytest.mark.parametrize(
	("item", "expected"),
	[
		({"friendId": 123}, True),
		({"friendId": "123"}, True),
		({"uid": 123}, True),
		({"uid": None, "friendId": 123}, True),
		({"uid": None}, False),
		({"friendId": "abc"}, False),
		({"friendId": True}, False),
		({}, False),
	],
)
def test_friend_id_matches_identifier_keys(item, expected) -> None:
	from boss_agent_cli.commands.recruiter.location import _friend_id_matches

	assert _friend_id_matches(item, 123) is expected


def test_target_from_detail_missing_friend_still_not_found() -> None:
	detail = {"zpData": {"friendList": [{"uid": 5, "friendId": 5, "securityId": "s", "jobId": 9}]}}

	with pytest.raises(_TargetError):
		_target_from_detail(detail, 123)


# ── Issue #1839：发送记录存在性判断 ─────────────────────────────────


def test_location_status_missing_file_is_none(tmp_path: Path) -> None:
	assert _location_status(tmp_path, 1) is None


def test_location_status_dangling_symlink_is_none(tmp_path: Path) -> None:
	(tmp_path / "cache").mkdir()
	(tmp_path / "cache" / "boss_agent.db").symlink_to(tmp_path / "missing.db")

	assert _location_status(tmp_path, 1) is None


# ── Issue #1841：blank_option_names 非字符串 ─────────────────────────


def test_blank_option_names_accepts_non_string_values() -> None:
	assert blank_option_names(name="x", lid=123, empty="  ", missing=None) == ["--lid", "--empty"]


# ── Issue #1842：展示字段不再 str() 任意类型 ─────────────────────────


def test_format_chat_time_rejects_container_values() -> None:
	assert format_chat_time({"a": 1}) is None
	assert format_chat_time(["x"]) is None
	assert format_chat_time("12:30") == "12:30"
	assert format_chat_time(0) is None


def test_message_text_rejects_container_values() -> None:
	assert message_text({"body": {"text": {"a": 1}}}) is None
	assert message_text({"body": {"text": "hi"}}) == "hi"
	assert message_text({"lastMsg": ["x"]}) is None
