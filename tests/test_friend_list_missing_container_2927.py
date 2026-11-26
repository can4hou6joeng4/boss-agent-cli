"""Issue #2927：成功响应缺少列表容器不能伪装成完整空页。"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import patch

from click.testing import CliRunner

from boss_agent_cli.main import cli
from boss_agent_cli.services.friend_list_pages import collect_friend_list_page

FRIEND = {"uid": "u-1", "securityId": "s-1", "encryptJobId": "j-1"}


class _Platform:
	def __init__(self, *pages: dict[str, Any]) -> None:
		self._pages = pages
		self.calls: list[int] = []

	def friend_list(self, page: int = 1) -> dict[str, Any]:
		self.calls.append(page)
		return self._pages[min(page - 1, len(self._pages) - 1)]

	def is_success(self, response: dict[str, Any]) -> bool:
		return response.get("code") == 0

	def unwrap_data(self, response: dict[str, Any]) -> Any:
		return response.get("zpData")


def test_missing_container_continues_to_later_page() -> None:
	platform = _Platform(
		{"code": 0, "zpData": {"message": "ok"}},
		{"code": 0, "zpData": {"result": [FRIEND], "hasMore": False}},
	)

	items, error, pages, truncated = collect_friend_list_page(platform, max_pages=5)

	assert (items, error, pages, truncated) == ([FRIEND], None, 2, False)
	assert platform.calls == [1, 2]


def test_missing_container_without_explicit_end_is_truncated() -> None:
	platform = _Platform({"code": 0, "zpData": {"message": "ok"}})

	items, error, pages, truncated = collect_friend_list_page(platform, max_pages=2)

	assert (items, error, pages, truncated) == ([], None, 2, True)
	assert platform.calls == [1, 2]


def test_explicit_empty_container_still_stops() -> None:
	platform = _Platform(
		{"code": 0, "zpData": {"result": []}},
		{"code": 0, "zpData": {"result": [FRIEND]}},
	)

	items, error, pages, truncated = collect_friend_list_page(platform, max_pages=5)

	assert (items, error, pages, truncated) == ([], None, 1, False)
	assert platform.calls == [1]


def test_explicit_has_more_false_with_missing_container_stops() -> None:
	platform = _Platform({"code": 0, "zpData": {"message": "ok", "hasMore": False}})

	items, error, pages, truncated = collect_friend_list_page(platform, max_pages=5)

	assert (items, error, pages, truncated) == ([], None, 1, False)
	assert platform.calls == [1]


@patch("boss_agent_cli.commands.pipeline.get_platform_instance")
@patch("boss_agent_cli.commands.pipeline.AuthManager")
def test_pipeline_envelope_reports_truncation_on_missing_container(mock_auth_cls, mock_client_cls, legacy_args) -> None:
	instance = mock_client_cls.return_value
	instance.__enter__ = lambda self: self
	instance.__exit__ = lambda self, *args: None
	instance.is_success.side_effect = lambda response: response.get("code", 0) == 0
	instance.unwrap_data.side_effect = lambda response: response.get("zpData")
	instance.friend_list.return_value = {"code": 0, "zpData": {"message": "ok"}}
	instance.interview_data.return_value = {"code": 0, "zpData": {"interviewList": []}}

	result = CliRunner().invoke(cli, ["--json", *legacy_args, "pipeline"])

	assert result.exit_code == 0
	envelope = json.loads(result.output)
	assert envelope["ok"] is True
	assert envelope["data"] == []
	assert envelope["pagination"]["pages_scanned"] > 1
	assert envelope["pagination"]["truncated"] is True
