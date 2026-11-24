"""向导 recommendations / communication 的分页证据（Issue #2670）。"""

from __future__ import annotations

from typing import Any

import pytest

from boss_agent_cli.output import Logger
from boss_agent_cli.wizard.actions import ActionContext, DEFAULT_ACTIONS

_FRIENDS = [
	{"uid": "u1", "name": "甲", "relationType": 1},
	{"uid": "u2", "name": "乙", "relationType": 2},
]


class _Platform:
	def __init__(self, payload: Any) -> None:
		self._payload = payload
		self.calls: list[int] = []

	def __enter__(self) -> "_Platform":
		return self

	def __exit__(self, *args: object) -> None:
		return None

	def recommend_jobs(self, page: int = 1) -> dict[str, Any]:
		self.calls.append(page)
		return {"code": 0, "zpData": self._payload}

	def friend_list(self, page: int = 1) -> dict[str, Any]:
		self.calls.append(page)
		return {"code": 0, "zpData": self._payload}

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


def _run(tmp_path, platform: _Platform, action: str, inputs: dict[str, Any]) -> dict[str, Any]:
	return dict(DEFAULT_ACTIONS[action](_context(tmp_path, lambda *a, **k: platform), inputs, {}).data)


def test_recommend_has_more_true_reports_platform_evidence(tmp_path):
	platform = _Platform({"hasMore": True, "jobList": [{"jobName": "A"}]})

	data = _run(tmp_path, platform, "candidate_recommend", {"page": 2})

	assert data["pagination"] == {
		"page": 2,
		"has_more": True,
		"has_more_source": "platform_hasMore",
		"total": 1,
		"total_kind": "returned_page_count",
	}
	assert platform.calls == [2]


def test_recommend_has_more_false_is_not_confused_with_unknown(tmp_path):
	platform = _Platform({"hasMore": False, "jobList": [{"jobName": "A"}]})

	data = _run(tmp_path, platform, "candidate_recommend", {})

	assert data["pagination"]["has_more"] is False
	assert data["pagination"]["has_more_source"] == "platform_hasMore"


@pytest.mark.parametrize("payload", [{"jobList": [{"jobName": "A"}]}, {"hasMore": "true", "jobList": []}, {"hasMore": 1, "jobList": []}])
def test_recommend_missing_or_drifted_has_more_is_unknown(tmp_path, payload):
	platform = _Platform(payload)

	data = _run(tmp_path, platform, "candidate_recommend", {})

	assert data["pagination"]["has_more"] is None
	assert data["pagination"]["has_more_source"] == "unknown"


def test_chat_reports_page_and_current_page_total(tmp_path):
	platform = _Platform({"hasMore": True, "friendList": list(_FRIENDS)})

	data = _run(tmp_path, platform, "candidate_chat", {"page": 3})

	assert data["pagination"] == {
		"page": 3,
		"has_more": True,
		"has_more_source": "platform_hasMore",
		"total": 2,
		"total_kind": "returned_page_count",
	}


def test_chat_pagination_total_reflects_from_filter(tmp_path):
	platform = _Platform(
		{
			"hasMore": True,
			"friendList": [*_FRIENDS, {"uid": "u3", "relationType": 3}],
		}
	)

	data = _run(tmp_path, platform, "candidate_chat", {"from": "boss"})

	assert [item["uid"] for item in data["items"]] == ["u1"]
	assert data["pagination"]["total"] == 1
	assert data["filters"] == {"from": "boss", "days": None}


def test_chat_payload_not_mapping_keeps_unknown_has_more(tmp_path):
	platform = _Platform(list(_FRIENDS))

	data = _run(tmp_path, platform, "candidate_chat", {})

	assert [item["uid"] for item in data["items"]] == ["u1", "u2"]
	assert data["pagination"]["has_more"] is None
	assert data["pagination"]["has_more_source"] == "unknown"
	assert data["pagination"]["total"] == 2
