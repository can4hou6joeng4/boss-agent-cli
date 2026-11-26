"""其余列表类向导 goal 的分页证据（Issue #2672）。"""

from __future__ import annotations

from typing import Any

import pytest

from boss_agent_cli.output import Logger
from boss_agent_cli.services.pagination import MAX_TRUSTED_PAGE
from boss_agent_cli.wizard.actions import ActionContext, DEFAULT_ACTIONS

_FRIEND_PAGE = {
	"hasMore": True,
	"friendList": [
		{"friendId": 101, "uid": "u-101", "name": "甲"},
		{"friendId": 102, "uid": "u-102", "name": "乙"},
	],
}

_JOB_PAGE = {"hasMore": False, "data": [{"encJobId": "j1"}]}
_GEEK_PAGE = {"hasMore": "true", "geekList": [{"geekId": "g1"}]}


class _RecruiterPlatform:
	def __init__(self, friend_payload: Any = None, job_payload: Any = None, geek_payload: Any = None) -> None:
		self._friend_payload = _FRIEND_PAGE if friend_payload is None else friend_payload
		self._job_payload = _JOB_PAGE if job_payload is None else job_payload
		self._geek_payload = _GEEK_PAGE if geek_payload is None else geek_payload
		self.friend_calls: list[int] = []
		self.job_calls: list[int] = []

	def __enter__(self) -> "_RecruiterPlatform":
		return self

	def __exit__(self, *args: object) -> None:
		return None

	def friend_list(self, page: int = 1, label_id: int = 0, job_id: Any = None) -> dict[str, Any]:
		self.friend_calls.append(page)
		return {"code": 0, "zpData": self._friend_payload}

	def search_geeks(self, query: str = "", **kwargs: Any) -> dict[str, Any]:
		return {"code": 0, "zpData": self._geek_payload}

	def list_jobs(self, page: int = 1) -> dict[str, Any]:
		self.job_calls.append(page)
		return {"code": 0, "zpData": self._job_payload}

	def last_messages(self, friend_ids: list[int]) -> dict[str, Any]:
		return {"code": 0, "zpData": {"messages": [{"friendId": 101, "lastMsg": "你好"}]}}

	def is_success(self, response: dict[str, Any]) -> bool:
		return response.get("code") == 0

	def unwrap_data(self, response: dict[str, Any]) -> Any:
		return response.get("zpData") or {}

	def parse_error(self, response: dict[str, Any]) -> tuple[str, str]:
		return "UNKNOWN", ""


class _CandidatePlatform:
	def __init__(self, history_payload: Any = None) -> None:
		self._history_payload = (
			{"hasMore": False, "messageList": [{"msgId": 1, "text": "hi"}]}
			if history_payload is None
			else history_payload
		)

	def __enter__(self) -> "_CandidatePlatform":
		return self

	def __exit__(self, *args: object) -> None:
		return None

	def friend_list(self, page: int = 1) -> dict[str, Any]:
		return {
			"code": 0,
			"zpData": {
				"hasMore": False,
				"friendList": [{"uid": "u-1", "securityId": "sec-1", "name": "甲"}],
			},
		}

	def chat_history(self, gid: str, security_id: str, page: int = 1, count: int = 20) -> dict[str, Any]:
		return {"code": 0, "zpData": self._history_payload}

	def is_success(self, response: dict[str, Any]) -> bool:
		return response.get("code") == 0

	def unwrap_data(self, response: dict[str, Any]) -> Any:
		return response.get("zpData") or {}

	def parse_error(self, response: dict[str, Any]) -> tuple[str, str]:
		return "UNKNOWN", ""


def _recruiter_context(tmp_path, platform: _RecruiterPlatform) -> ActionContext:
	return ActionContext(
		data_dir=tmp_path,
		platform="zhipin",
		role="recruiter",
		logger=Logger(),
		recruiter_factory=lambda *args: platform,
	)


def _candidate_context(tmp_path, platform: _CandidatePlatform) -> ActionContext:
	return ActionContext(
		data_dir=tmp_path,
		platform="zhipin",
		role="candidate",
		logger=Logger(),
		candidate_factory=lambda *args: platform,
	)


def _run(context: ActionContext, action: str, inputs: dict[str, Any]) -> dict[str, Any]:
	return dict(DEFAULT_ACTIONS[action](context, inputs, {}).data)


def test_applications_reports_page_and_platform_has_more(tmp_path):
	platform = _RecruiterPlatform()

	data = _run(_recruiter_context(tmp_path, platform), "recruiter_applications", {"page": 2})

	assert platform.friend_calls == [2]
	assert data["pagination"] == {
		"page": 2,
		"has_more": True,
		"has_more_source": "platform_hasMore",
		"total": 2,
		"total_kind": "returned_page_count",
	}


def test_recruiter_chat_shares_applications_pagination(tmp_path):
	platform = _RecruiterPlatform()

	data = _run(_recruiter_context(tmp_path, platform), "recruiter_chat", {"page": 3})

	assert platform.friend_calls == [3]
	assert data["pagination"]["page"] == 3
	assert data["pagination"]["has_more"] is True


def test_candidates_drifted_has_more_is_unknown(tmp_path):
	platform = _RecruiterPlatform()

	data = _run(_recruiter_context(tmp_path, platform), "recruiter_candidates", {"page": 4})

	assert data["pagination"]["page"] == 4
	assert data["pagination"]["has_more"] is None
	assert data["pagination"]["has_more_source"] == "unknown"


def test_jobs_list_forwards_page_and_reports_pagination(tmp_path):
	platform = _RecruiterPlatform()

	data = _run(_recruiter_context(tmp_path, platform), "recruiter_jobs_list", {"page": 3})

	# 向导此前完全不读 page，传第 2 页仍返回第一页（Issue #2672）。
	assert platform.job_calls == [3]
	assert data["pagination"]["page"] == 3
	assert data["pagination"]["has_more"] is False
	assert data["pagination"]["total"] == 1


def test_last_messages_pagination_comes_from_friend_list_page(tmp_path):
	platform = _RecruiterPlatform()

	data = _run(_recruiter_context(tmp_path, platform), "recruiter_last_messages", {"page": 2})

	assert platform.friend_calls == [2]
	assert data["pagination"] == {
		"page": 2,
		"has_more": True,
		"has_more_source": "platform_hasMore",
		"total": 2,
		"total_kind": "returned_page_count",
	}


def test_last_messages_empty_page_still_reports_pagination(tmp_path):
	platform = _RecruiterPlatform(friend_payload={"hasMore": False, "friendList": []})

	data = _run(_recruiter_context(tmp_path, platform), "recruiter_last_messages", {})

	assert data["friend_ids"] == []
	assert data["pagination"]["has_more"] is False
	assert data["pagination"]["total"] == 0


def test_candidate_chat_history_reports_pagination(tmp_path):
	platform = _CandidatePlatform()

	data = _run(_candidate_context(tmp_path, platform), "candidate_chat_history", {"gid": "u-1", "page": 2, "count": 30})

	assert data["pagination"] == {
		"page": 2,
		"has_more": False,
		"has_more_source": "platform_hasMore",
		"total": 1,
		"total_kind": "returned_page_count",
	}


@pytest.mark.parametrize("payload", [{"hasMore": None, "messageList": []}, {"messageList": [{"msgId": 1}]}])
def test_candidate_chat_history_missing_has_more_is_unknown(tmp_path, payload):
	data = _run(_candidate_context(tmp_path, _CandidatePlatform(payload)), "candidate_chat_history", {"gid": "u-1"})

	assert data["pagination"]["has_more"] is None
	assert data["pagination"]["has_more_source"] == "unknown"


def test_recruiter_chat_prefers_platform_page(tmp_path):
	"""平台纠正页码时向导与 CLI 信封一致采信平台值（Issue #2900）。"""
	payload = {
		"page": 7,
		"hasMore": True,
		"friendList": [{"friendId": 101, "name": "甲"}, {"friendId": 102, "name": "乙"}],
	}
	platform = _RecruiterPlatform(friend_payload=payload)

	data = _run(_recruiter_context(tmp_path, platform), "recruiter_chat", {"page": 3})

	assert platform.friend_calls == [3]
	assert data["pagination"] == {
		"page": 7,
		"has_more": True,
		"has_more_source": "platform_hasMore",
		"total": 2,
		"total_kind": "returned_page_count",
	}


@pytest.mark.parametrize("platform_page", [0, -1, True, "3", 10**9])
def test_recruiter_chat_falls_back_to_request_page_on_drifted_platform_page(tmp_path, platform_page):
	"""平台 page 不是可信整数时回显请求页，不能把它原样写进证据。"""
	platform = _RecruiterPlatform(
		friend_payload={"page": platform_page, "hasMore": False, "friendList": []},
	)

	data = _run(_recruiter_context(tmp_path, platform), "recruiter_chat", {"page": 4})

	assert data["pagination"]["page"] == 4
	assert data["pagination"]["has_more"] is False


def test_recruiter_chat_clamps_untrusted_request_page(tmp_path):
	"""没有平台 page 时回显的请求页也要钳制，避免 page + 1 拼出无界整数。"""
	platform = _RecruiterPlatform(friend_payload={"hasMore": False, "friendList": []})

	data = _run(_recruiter_context(tmp_path, platform), "recruiter_chat", {"page": 2_000_000})

	assert data["pagination"]["page"] == MAX_TRUSTED_PAGE


def test_last_messages_total_counts_extracted_contact_rows(tmp_path):
	"""非对象元素不计入 total，与 CLI 的 friend_items 过滤口径一致（Issue #2900）。"""
	payload = {"friendList": [{"friendId": 101, "name": "甲"}, "drifted"], "hasMore": False}
	platform = _RecruiterPlatform(friend_payload=payload)

	data = _run(_recruiter_context(tmp_path, platform), "recruiter_last_messages", {"page": 2})

	assert data["friend_ids"] == [101]
	assert data["pagination"]["total"] == 1
	assert data["pagination"]["has_more"] is False
