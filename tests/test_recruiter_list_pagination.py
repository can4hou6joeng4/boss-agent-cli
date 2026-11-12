"""招聘者列表命令的信封分页证据（applications / candidates / recommendations / chat）。"""
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from boss_agent_cli.main import cli
from boss_agent_cli.services.pagination import UNKNOWN_HAS_MORE_OPERATOR_ACTION, list_pagination, rows_from


def _hint(command: str) -> str:
	"""默认数据目录下的招聘者后继命令。"""
	return f"boss --data-dir {(Path.home() / '.boss-agent').resolve()} {command}"


def _platform(mock_cls):
	instance = mock_cls.return_value
	instance.__enter__ = lambda self: self
	instance.__exit__ = lambda self, *a: None
	instance.unwrap_data.side_effect = lambda response: response.get("zpData", response.get("data"))
	instance.is_success.side_effect = lambda response: response.get("code", 0) in (0, 200)
	return instance


def _page_cases():
	return [(True, True), (False, False), ("missing", None)]


def _assert_pagination(payload: dict, *, expected: bool | None, total: int) -> None:
	pagination = payload["pagination"]
	assert pagination["page"] == 2
	assert pagination["has_more"] is expected
	assert pagination["has_more_source"] == ("platform_hasMore" if expected is not None else "unknown")
	assert pagination["total"] == total
	assert pagination["total_kind"] == "returned_page_count"


def _next_pages(payload: dict, marker: str) -> list[str]:
	return [action for action in payload["hints"]["next_actions"] if marker in action]


@pytest.mark.parametrize(("platform_has_more", "expected"), _page_cases())
@patch("boss_agent_cli.commands.recruiter.applications.get_recruiter_platform_instance")
@patch("boss_agent_cli.commands.recruiter.applications.AuthManager")
def test_applications_pagination_evidence(mock_auth_cls, mock_platform_cls, platform_has_more, expected):
	platform = _platform(mock_platform_cls)
	data = {"friendList": [{"name": "候选人C"}]}
	if platform_has_more != "missing":
		data["hasMore"] = platform_has_more
	platform.friend_list.return_value = {"code": 200, "data": data}
	result = CliRunner().invoke(
		cli, ["--json", "hr", "applications", "--page", "2", "--job-id", "jobx", "--label-id", "2"],
	)
	assert result.exit_code == 0
	payload = json.loads(result.output)
	_assert_pagination(payload, expected=expected, total=1)
	next_pages = _next_pages(payload, "hr applications --page")
	if expected is True:
		assert next_pages == [_hint("hr applications --page 3 --job-id jobx --label-id 2")]
		assert "operator_actions" not in payload["hints"]
	else:
		assert next_pages == []
		if expected is None:
			assert payload["hints"]["operator_actions"] == [UNKNOWN_HAS_MORE_OPERATOR_ACTION]
		else:
			assert "operator_actions" not in payload["hints"]


@pytest.mark.parametrize(("platform_has_more", "expected"), _page_cases())
@patch("boss_agent_cli.commands.recruiter.candidates.get_recruiter_platform_instance")
@patch("boss_agent_cli.commands.recruiter.candidates.AuthManager")
def test_candidates_pagination_evidence_and_filters(mock_auth_cls, mock_platform_cls, platform_has_more, expected):
	platform = _platform(mock_platform_cls)
	data = {"geekList": [{"name": "候选人A"}]}
	if platform_has_more != "missing":
		data["hasMore"] = platform_has_more
	platform.search_geeks.return_value = {"code": 200, "data": data}
	result = CliRunner().invoke(
		cli,
		[
			"--json", "hr", "candidates", "python",
			"--page", "2", "--city", "101020100", "--degree", "201,201", "--select",
		],
	)
	assert result.exit_code == 0
	payload = json.loads(result.output)
	_assert_pagination(payload, expected=expected, total=1)
	next_pages = _next_pages(payload, "hr candidates python --page")
	if expected is True:
		assert next_pages == [
			_hint("hr candidates python --page 3 --city 101020100 --degree 201,201 --select")
		]
	else:
		assert next_pages == []
	if expected is None:
		assert payload["hints"]["operator_actions"] == [UNKNOWN_HAS_MORE_OPERATOR_ACTION]


@pytest.mark.parametrize(("platform_has_more", "expected"), _page_cases())
@patch("boss_agent_cli.commands.recruiter.recommendations.get_recruiter_platform_instance")
@patch("boss_agent_cli.commands.recruiter.recommendations.AuthManager")
def test_recommendations_pagination_evidence(mock_auth_cls, mock_platform_cls, platform_has_more, expected):
	platform = _platform(mock_platform_cls)
	data = {"geekList": [{"geekName": "测试候选人"}]}
	if platform_has_more != "missing":
		data["hasMore"] = platform_has_more
	platform.recommend_geeks.return_value = {"code": 200, "data": data}
	result = CliRunner().invoke(
		cli, ["--json", "hr", "recommendations", "--job-id", "job-enc", "--page", "2"],
	)
	assert result.exit_code == 0
	payload = json.loads(result.output)
	_assert_pagination(payload, expected=expected, total=1)
	next_pages = _next_pages(payload, "hr recommendations --job-id job-enc --page")
	if expected is True:
		assert next_pages == [_hint("hr recommendations --job-id job-enc --page 3")]
	else:
		assert next_pages == []
	if expected is None:
		assert payload["hints"]["operator_actions"][-1] == UNKNOWN_HAS_MORE_OPERATOR_ACTION


@pytest.mark.parametrize(("platform_has_more", "expected"), _page_cases())
@patch("boss_agent_cli.commands.recruiter.chat.get_recruiter_platform_instance")
@patch("boss_agent_cli.commands.recruiter.chat.AuthManager")
def test_recruiter_chat_pagination_evidence(mock_auth_cls, mock_platform_cls, platform_has_more, expected):
	platform = _platform(mock_platform_cls)
	data = {"friendList": [{"friendId": 12345, "name": "候选人B", "friendSource": 0}]}
	if platform_has_more != "missing":
		data["hasMore"] = platform_has_more
	platform.friend_list.return_value = {"code": 200, "data": data}
	result = CliRunner().invoke(
		cli,
		[
			"--json", "hr", "chat",
			"--page", "2", "--job-id", "job-enc", "--label-id", "2", "--summary-limit", "0",
		],
	)
	assert result.exit_code == 0
	payload = json.loads(result.output)
	_assert_pagination(payload, expected=expected, total=1)
	next_pages = _next_pages(payload, "hr chat --page 3")
	if expected is True:
		assert next_pages == [
			_hint("hr chat --page 3 --job-id job-enc --label-id 2 --summary-limit 0")
		]
	else:
		assert next_pages == []
	if expected is None:
		assert payload["hints"]["operator_actions"] == [UNKNOWN_HAS_MORE_OPERATOR_ACTION]


@pytest.mark.parametrize(("platform_has_more", "expected"), _page_cases())
@patch("boss_agent_cli.commands.recruiter.chat.get_recruiter_platform_instance")
@patch("boss_agent_cli.commands.recruiter.chat.AuthManager")
def test_recruiter_last_messages_pagination_evidence(mock_auth_cls, mock_platform_cls, platform_has_more, expected):
	platform = _platform(mock_platform_cls)
	data = {"friendList": [{"friendId": 12345, "name": "候选人B"}]}
	if platform_has_more != "missing":
		data["hasMore"] = platform_has_more
	platform.friend_list.return_value = {"code": 200, "data": data}
	platform.last_messages.return_value = {"code": 200, "data": {"lastMessageList": []}}
	result = CliRunner().invoke(
		cli, ["--json", "hr", "last-messages", "--page", "2", "--job-id", "job-enc", "--label-id", "2", "--limit", "5"],
	)
	assert result.exit_code == 0
	payload = json.loads(result.output)
	_assert_pagination(payload, expected=expected, total=1)
	next_pages = _next_pages(payload, "hr last-messages --page")
	if expected is True:
		assert next_pages == [
			_hint("hr last-messages --page 3 --job-id job-enc --label-id 2 --limit 5")
		]
	else:
		assert next_pages == []
	if expected is None:
		assert payload["hints"]["operator_actions"] == [UNKNOWN_HAS_MORE_OPERATOR_ACTION]


@patch("boss_agent_cli.commands.recruiter.chat.get_recruiter_platform_instance")
@patch("boss_agent_cli.commands.recruiter.chat.AuthManager")
def test_recruiter_last_messages_with_explicit_friend_ids_has_no_pagination(mock_auth_cls, mock_platform_cls):
	"""--friend-id 直查没有列表页，不能凭空造分页证据。"""
	platform = _platform(mock_platform_cls)
	platform.last_messages.return_value = {"code": 200, "data": {"lastMessageList": []}}
	result = CliRunner().invoke(cli, ["--json", "hr", "last-messages", "--friend-id", "12345"])
	assert result.exit_code == 0
	payload = json.loads(result.output)
	assert payload["pagination"] is None
	assert _next_pages(payload, "hr last-messages --page") == []
	assert "operator_actions" not in payload["hints"]
	platform.friend_list.assert_not_called()


def test_list_pagination_ignores_malformed_platform_fields():
	pagination = list_pagination(
		{"page": 0, "hasMore": "true", "friendList": []}, page=4, rows=rows_from({"friendList": []}, "friendList"),
	)
	assert pagination == {
		"page": 4,
		"has_more": None,
		"has_more_source": "unknown",
		"total": 0,
		"total_kind": "returned_page_count",
	}


def test_list_pagination_handles_non_mapping_payload():
	assert list_pagination(["unexpected"], page=3) == {
		"page": 3,
		"has_more": None,
		"has_more_source": "unknown",
		"total": None,
		"total_kind": "returned_page_count",
	}
