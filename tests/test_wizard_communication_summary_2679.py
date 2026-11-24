"""向导 communication 的 summary_limit 与摘要合并（Issue #2679）。"""

from __future__ import annotations

from typing import Any

import pytest

from boss_agent_cli.output import Logger
from boss_agent_cli.wizard.actions import ActionContext, DEFAULT_ACTIONS
from boss_agent_cli.wizard.models import WorkflowInputError


class _RecruiterPlatform:
	def __init__(self, friend_items: list[dict[str, Any]] | None = None) -> None:
		self._friend_items = (
			[{"friendId": 101, "friendSource": 0}, {"friendId": 102, "friendSource": 0}]
			if friend_items is None
			else friend_items
		)
		self.friend_calls: list[dict[str, Any]] = []
		self.message_calls: list[list[int]] = []

	def __enter__(self) -> "_RecruiterPlatform":
		return self

	def __exit__(self, *args: object) -> None:
		return None

	def friend_list(self, page: int = 1, label_id: int = 0, job_id: Any = None) -> dict[str, Any]:
		self.friend_calls.append({"page": page, "label_id": label_id, "job_id": job_id})
		return {"code": 0, "zpData": {"hasMore": False, "friendList": [dict(item) for item in self._friend_items]}}

	def last_messages(self, friend_ids: list[int], src: int = 0) -> dict[str, Any]:
		self.message_calls.append(list(friend_ids))
		return {
			"code": 0,
			"zpData": {
				"messages": [
					{"friendId": fid, "lastMsg": f"hi-{fid}", "unread": 2, "lastTS": 1791518413490}
					for fid in friend_ids
				]
			},
		}

	def is_success(self, response: dict[str, Any]) -> bool:
		return response.get("code") == 0

	def unwrap_data(self, response: dict[str, Any]) -> Any:
		return response.get("zpData") or {}

	def parse_error(self, response: dict[str, Any]) -> tuple[str, str]:
		return "UNKNOWN", ""


def _context(tmp_path, platform: _RecruiterPlatform, constructed: list[int] | None = None) -> ActionContext:
	def factory(*args: Any) -> _RecruiterPlatform:
		if constructed is not None:
			constructed.append(len(constructed) + 1)
		return platform

	return ActionContext(
		data_dir=tmp_path,
		platform="zhipin",
		role="recruiter",
		logger=Logger(),
		recruiter_factory=factory,
	)


def _run(context: ActionContext, inputs: dict[str, Any]) -> dict[str, Any]:
	return dict(DEFAULT_ACTIONS["recruiter_applications"](context, inputs, {}).data)


def test_default_limit_requests_messages_and_merges_fields(tmp_path):
	platform = _RecruiterPlatform()

	data = _run(_context(tmp_path, platform), {})

	assert len(platform.message_calls) == 1
	assert platform.message_calls[0] == [101, 102]
	first = data["items"][0]
	assert first["unread"] == 2
	assert first["last_msg"] == "hi-101"
	assert first["last_time"] is not None
	assert first["summary_source"] == "last_messages"
	assert data["summary_sources"]["last_messages_requests"] == 1
	assert data["summary_sources"]["counts"] == {"last_messages": 2}


def test_limit_truncates_before_requesting_and_reports_skipped(tmp_path):
	items = [{"friendId": fid, "friendSource": 0} for fid in range(1, 151)]
	platform = _RecruiterPlatform(friend_items=items)

	data = _run(_context(tmp_path, platform), {"summary_limit": 100})

	assert data["summary_sources"]["summary_limit"] == 100
	assert data["summary_sources"]["last_messages_skipped"] == 50
	assert [len(ids) for ids in platform.message_calls] == [50, 50]


def test_summary_limit_zero_skips_all_requests(tmp_path):
	platform = _RecruiterPlatform()

	data = _run(_context(tmp_path, platform), {"summary_limit": 0})

	assert platform.message_calls == []
	assert data["summary_sources"]["last_messages_skipped"] == 2
	assert data["summary_sources"]["counts"] == {"friend_list": 2}
	assert data["items"][0]["summary_source"] == "friend_list"


def test_batches_are_split_at_fifty(tmp_path):
	items = [{"friendId": fid, "friendSource": 0} for fid in range(1, 61)]
	platform = _RecruiterPlatform(friend_items=items)

	data = _run(_context(tmp_path, platform), {})

	assert [len(ids) for ids in platform.message_calls] == [50, 10]
	assert data["summary_sources"]["last_messages_requests"] == 2
	assert "last_messages_skipped" not in data["summary_sources"]


@pytest.mark.parametrize("limit", [-1, 301, "x", True, 1.5])
def test_invalid_summary_limit_is_rejected_before_platform_requests(tmp_path, limit):
	platform = _RecruiterPlatform()
	constructed: list[int] = []

	with pytest.raises(WorkflowInputError):
		_run(_context(tmp_path, platform, constructed), {"summary_limit": limit})

	assert constructed == []
	assert platform.friend_calls == []
	assert platform.message_calls == []


def test_explicit_null_summary_limit_uses_default(tmp_path):
	platform = _RecruiterPlatform()

	data = _run(_context(tmp_path, platform), {"summary_limit": None})

	assert platform.message_calls == [[101, 102]]
	assert data["summary_sources"]["last_messages_requests"] == 1


def test_empty_friend_list_omits_summary_sources_but_keeps_pagination(tmp_path):
	platform = _RecruiterPlatform(friend_items=[])

	data = _run(_context(tmp_path, platform), {})

	assert data["items"] == []
	assert platform.message_calls == []
	assert "summary_sources" not in data
	assert data["pagination"]["has_more"] is False
