"""向导 last_messages 的 limit / 分批 / friend_ids 直查（Issue #2675）。"""

from __future__ import annotations

from typing import Any

import pytest

from boss_agent_cli.output import Logger
from boss_agent_cli.wizard.actions import ActionContext, DEFAULT_ACTIONS
from boss_agent_cli.wizard.models import WorkflowInputError
from boss_agent_cli.wizard.runner import WorkflowActionError


class _RecruiterPlatform:
	def __init__(
		self,
		friend_items: list[dict[str, Any]] | None = None,
		message_code: int = 0,
	) -> None:
		self._friend_items = (
			[{"friendId": 101, "friendSource": 0}, {"friendId": 102, "friendSource": 1}]
			if friend_items is None
			else friend_items
		)
		self._message_code = message_code
		self.friend_calls: list[dict[str, Any]] = []
		self.message_calls: list[tuple[list[int], int]] = []

	def __enter__(self) -> "_RecruiterPlatform":
		return self

	def __exit__(self, *args: object) -> None:
		return None

	def friend_list(self, page: int = 1, label_id: int = 0, job_id: Any = None) -> dict[str, Any]:
		self.friend_calls.append({"page": page, "label_id": label_id, "job_id": job_id})
		return {"code": 0, "zpData": {"hasMore": False, "friendList": self._friend_items}}

	def last_messages(self, friend_ids: list[int], src: int = 0) -> dict[str, Any]:
		self.message_calls.append((list(friend_ids), src))
		if self._message_code != 0:
			return {"code": self._message_code, "msg": "boom"}
		return {"code": 0, "zpData": {"messages": [{"friendId": fid, "lastMsg": f"hi-{fid}"} for fid in friend_ids]}}

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
	return dict(DEFAULT_ACTIONS["recruiter_last_messages"](context, inputs, {}).data)


def test_limit_truncates_before_requesting_and_reports_skipped(tmp_path):
	items = [{"friendId": fid, "friendSource": 0} for fid in range(1, 151)]
	platform = _RecruiterPlatform(friend_items=items)

	data = _run(_context(tmp_path, platform), {"limit": 100})

	assert data["friend_ids"] == list(range(1, 101))
	assert data["skipped"] == 50
	assert data["requests"] == 2
	assert [len(ids) for ids, _ in platform.message_calls] == [50, 50]


def test_messages_are_split_into_batches_of_fifty(tmp_path):
	items = [{"friendId": fid, "friendSource": 0} for fid in range(1, 61)]
	platform = _RecruiterPlatform(friend_items=items)

	data = _run(_context(tmp_path, platform), {})

	assert data["requests"] == 2
	assert [len(ids) for ids, _ in platform.message_calls] == [50, 10]
	assert "skipped" not in data
	assert data["total"] == 60


def test_friend_source_one_goes_to_separate_group(tmp_path):
	items = [
		{"friendId": 1, "friendSource": 0},
		{"friendId": 2, "friendSource": 1},
		{"friendId": 3, "friendSource": 0},
	]
	platform = _RecruiterPlatform(friend_items=items)

	_run(_context(tmp_path, platform), {})

	assert platform.message_calls == [([1, 3], 0), ([2], 1)]


def test_explicit_friend_ids_skip_friend_list_and_pagination(tmp_path):
	platform = _RecruiterPlatform()
	constructed: list[int] = []

	data = _run(
		_context(tmp_path, platform, constructed),
		{"friend_ids": [7, 7, 9], "page": 3, "label_id": 2},
	)

	# CLI `--friend-id` 直查不发 friend_list 请求，没有列表页分页证据。
	assert platform.friend_calls == []
	assert platform.message_calls == [([7, 9], 0)]
	assert data["friend_ids"] == [7, 9]
	assert "pagination" not in data
	assert constructed  # 平台实例仍按需构造


def test_single_friend_id_is_accepted_as_one_item(tmp_path):
	platform = _RecruiterPlatform()

	data = _run(_context(tmp_path, platform), {"friend_ids": 7})

	assert data["friend_ids"] == [7]
	assert platform.message_calls == [([7], 0)]


@pytest.mark.parametrize("limit", [0, 301, "x", True, 1.5])
def test_invalid_limit_is_rejected_before_platform_requests(tmp_path, limit):
	platform = _RecruiterPlatform()
	constructed: list[int] = []

	with pytest.raises(WorkflowInputError):
		_run(_context(tmp_path, platform, constructed), {"limit": limit})

	assert constructed == []
	assert platform.friend_calls == []
	assert platform.message_calls == []


@pytest.mark.parametrize("friend_ids", [[0], [-1], [True], ["x"], [1.5], {"a": 1}, [1, 0]])
def test_invalid_friend_ids_are_rejected_before_platform_requests(tmp_path, friend_ids):
	platform = _RecruiterPlatform()
	constructed: list[int] = []

	with pytest.raises(WorkflowInputError):
		_run(_context(tmp_path, platform, constructed), {"friend_ids": friend_ids})

	assert constructed == []
	assert platform.friend_calls == []
	assert platform.message_calls == []


def test_total_failure_raises_platform_error(tmp_path):
	platform = _RecruiterPlatform(message_code=500)

	with pytest.raises(WorkflowActionError) as caught:
		_run(_context(tmp_path, platform), {})

	assert caught.value.code == "NETWORK_ERROR"


def test_empty_friend_list_keeps_pagination_and_skips_messages(tmp_path):
	platform = _RecruiterPlatform(friend_items=[{"uid": "not-a-number"}])

	data = _run(_context(tmp_path, platform), {})

	assert data["friend_ids"] == []
	assert data["messages"] == []
	assert data["pagination"]["has_more"] is False
	assert platform.message_calls == []
