"""Issue #2916：重复页仍有后续时不能把联系人误判为不存在。"""

from __future__ import annotations

import inspect
import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from boss_agent_cli.main import cli
from boss_agent_cli.services.contact_lookup import FriendLookupLimitExceeded, find_friend


class _Platform:
	def __init__(self) -> None:
		self.pages: list[int] = []
		self.writes = 0

	def friend_list(self, *, page: int) -> dict[str, Any]:
		self.pages.append(page)
		uid = "probe-a" if page <= 2 else "probe-target"
		return {
			"zpData": {
				"result": [{"uid": uid, "encryptJobId": "probe-job", "securityId": f"probe-token-{page}"}],
				"hasMore": page < 3,
			}
		}

	def is_success(self, response: dict[str, Any]) -> bool:
		return True

	def unwrap_data(self, response: dict[str, Any]) -> Any:
		return response["zpData"]

	def exchange_contact(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
		self.writes += 1
		return {"ok": True}

	def __enter__(self) -> "_Platform":
		return self

	def __exit__(self, *args: object) -> bool:
		return False


def _page(uid: str, *, has_more: bool) -> dict[str, Any]:
	return {"zpData": {"result": [{"uid": uid, "encryptJobId": "j1", "securityId": "sid"}], "hasMore": has_more}}


def _platform_from_pages(pages: list[dict[str, Any]]) -> tuple[SimpleNamespace, list[int]]:
	calls: list[int] = []

	def friend_list(*, page: int) -> dict[str, Any]:
		calls.append(page)
		return pages[len(calls) - 1]

	platform = SimpleNamespace(
		friend_list=friend_list,
		is_success=lambda response: True,
		unwrap_data=lambda response: response["zpData"],
	)
	return platform, calls


def test_duplicate_page_with_more_raises_incomplete_lookup() -> None:
	"""重复页仍声明有后续时不能返回确定的「未找到」。"""
	platform, calls = _platform_from_pages([_page("probe-a", has_more=True), _page("probe-a", has_more=True), _page("probe-target", has_more=False)])

	with pytest.raises(FriendLookupLimitExceeded, match="重复页"):
		find_friend(platform, "probe-target", max_pages=3)

	assert calls == [1, 2]


def test_duplicate_terminal_page_still_means_not_found() -> None:
	"""重复页显式声明没有后续时仍按遍历完成处理。"""
	platform, calls = _platform_from_pages([_page("probe-a", has_more=True), _page("probe-a", has_more=False)])

	friend, error = find_friend(platform, "probe-target", max_pages=3)

	assert friend is None
	assert error is None
	assert calls == [1, 2]


def test_exchange_reports_incomplete_lookup_instead_of_not_found(tmp_path: Any) -> None:
	"""真实 CLI 入口应给出不可重放的读取错误，而不是 CONTACT_NOT_FOUND。"""
	platform = _Platform()
	callback = inspect.unwrap(cli.commands["exchange"].callback)

	with patch.dict(callback.__globals__, {"AuthManager": MagicMock(), "get_platform_instance": lambda *args: platform}):
		result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "--json", "exchange", "probe-target"])

	envelope = json.loads(result.stdout)
	assert result.exit_code == 1
	assert envelope["error"]["code"] == "NETWORK_ERROR"
	assert "重复页" in envelope["error"]["message"]
	assert platform.pages == [1, 2]
	assert platform.writes == 0
