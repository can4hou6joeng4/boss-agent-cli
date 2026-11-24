"""向导 communication 的 from / days 筛选与 CLI 共用判定（Issue #2667）。"""

from __future__ import annotations

import time
from typing import Any

import pytest

from boss_agent_cli.output import Logger
from boss_agent_cli.services.chat_utils import matches_chat_filters
from boss_agent_cli.wizard.actions import ActionContext, DEFAULT_ACTIONS
from boss_agent_cli.wizard.models import WorkflowInputError
from boss_agent_cli.wizard.runner import WorkflowActionError

_NOW_MS = int(time.time() * 1000)
_DAY_MS = 86_400_000

_FRIENDS = [
	{"uid": "boss-recent", "relationType": 1, "lastTS": _NOW_MS - 3_600_000},
	{"uid": "me-old", "relationType": 2, "lastTS": _NOW_MS - 10 * _DAY_MS},
	{"uid": "applied", "relationType": 3, "lastTS": _NOW_MS - 2 * _DAY_MS},
	{"uid": "boss-unknown-time", "relationType": 1},
]


class _Platform:
	def __init__(self, friends: list[dict[str, Any]] | None = None) -> None:
		self._friends = _FRIENDS if friends is None else friends
		self.calls: list[int] = []

	def __enter__(self) -> "_Platform":
		return self

	def __exit__(self, *args: object) -> None:
		return None

	def friend_list(self, page: int = 1) -> dict[str, Any]:
		self.calls.append(page)
		return {"code": 0, "zpData": {"hasMore": False, "friendList": self._friends}}

	def is_success(self, response: dict[str, Any]) -> bool:
		return response.get("code") == 0

	def unwrap_data(self, response: dict[str, Any]) -> Any:
		return response.get("zpData") or {}

	def parse_error(self, response: dict[str, Any]) -> tuple[str, str]:
		return "UNKNOWN", ""


def _context(tmp_path, factory) -> ActionContext:
	return ActionContext(
		data_dir=tmp_path,
		platform="zhipin",
		role="candidate",
		logger=Logger(),
		candidate_factory=factory,
	)


def _run(tmp_path, platform: _Platform, inputs: dict[str, Any]) -> dict[str, Any]:
	return dict(DEFAULT_ACTIONS["candidate_chat"](_context(tmp_path, lambda *a, **k: platform), inputs, {}).data)


def test_chat_default_keeps_all_items_without_filters_key(tmp_path):
	platform = _Platform()

	data = _run(tmp_path, platform, {})

	assert [item["uid"] for item in data["items"]] == [item["uid"] for item in _FRIENDS]
	assert "filters" not in data
	assert platform.calls == [1]


def test_chat_from_boss_keeps_only_boss_initiated(tmp_path):
	platform = _Platform()

	data = _run(tmp_path, platform, {"from": "boss"})

	assert [item["uid"] for item in data["items"]] == ["boss-recent", "boss-unknown-time"]
	assert data["filters"] == {"from": "boss", "days": None}


def test_chat_from_me_excludes_boss_and_applied(tmp_path):
	platform = _Platform()

	data = _run(tmp_path, platform, {"from": "me"})

	assert [item["uid"] for item in data["items"]] == ["me-old"]


def test_chat_days_filters_by_last_ts_and_keeps_unknown_time(tmp_path):
	platform = _Platform()

	data = _run(tmp_path, platform, {"days": 1})

	# 1 小时前命中；10 天 / 2 天前排除；缺少 lastTS 与 CLI 一样按未知放行。
	assert [item["uid"] for item in data["items"]] == ["boss-recent", "boss-unknown-time"]
	assert data["filters"] == {"from": None, "days": 1}


def test_chat_filters_apply_to_current_page_only(tmp_path):
	platform = _Platform()

	_run(tmp_path, platform, {"from": "boss", "days": 1, "page": 2})

	assert platform.calls == [2]


@pytest.mark.parametrize("value", ["boss ", "BOSS", "player", ""])
def test_chat_rejects_unknown_from_before_platform(tmp_path, value):
	def must_not_open(*args, **kwargs):
		raise AssertionError("非法 from 不应打开平台")

	with pytest.raises(WorkflowActionError) as exc_info:
		DEFAULT_ACTIONS["candidate_chat"](_context(tmp_path, must_not_open), {"from": value}, {})

	assert exc_info.value.code == "INVALID_PARAM"


def test_chat_rejects_non_string_from_before_platform(tmp_path):
	def must_not_open(*args, **kwargs):
		raise AssertionError("非字符串 from 不应打开平台")

	with pytest.raises(WorkflowInputError):
		DEFAULT_ACTIONS["candidate_chat"](_context(tmp_path, must_not_open), {"from": 1}, {})


@pytest.mark.parametrize("days", [-1, 36501, "abc", True])
def test_chat_rejects_invalid_days_before_platform(tmp_path, days):
	def must_not_open(*args, **kwargs):
		raise AssertionError("非法 days 不应打开平台")

	with pytest.raises(WorkflowInputError):
		DEFAULT_ACTIONS["candidate_chat"](_context(tmp_path, must_not_open), {"days": days}, {})


def test_shared_filter_predicate_treats_drifted_timestamps_as_unknown():
	cutoff = _NOW_MS - _DAY_MS

	# 字符串 / 布尔时间戳按未知 0 处理：与 CLI 一样放行，不与阈值比较。
	assert matches_chat_filters(
		initiated_by="对方主动", last_ts="1700000000000", relation_label=None, cutoff_ts=cutoff
	) is True
	assert matches_chat_filters(
		initiated_by="对方主动", last_ts=True, relation_label=None, cutoff_ts=cutoff
	) is True
	assert matches_chat_filters(
		initiated_by="我主动", last_ts=_NOW_MS, relation_label="对方主动", cutoff_ts=None
	) is False
	assert matches_chat_filters(
		initiated_by="对方主动", last_ts=cutoff - 1, relation_label=None, cutoff_ts=cutoff
	) is False
