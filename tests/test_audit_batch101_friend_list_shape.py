"""第二十一轮审计批次：沟通列表容器漂移不再翻满上限（Issue #2415）。"""

from __future__ import annotations

import pytest

from boss_agent_cli.services.contact_lookup import FriendListShapeError, find_friend
from boss_agent_cli.services.friend_list_pages import (
	collect_friend_list_page,
	friend_list_page_has_content,
)

FRIEND = {"uid": "u1", "name": "甲", "securityId": "s1"}


class _FakePlatform:
	def __init__(self, *pages: object) -> None:
		self._pages = list(pages)
		self.friend_calls = 0

	def friend_list(self, page: int = 1) -> dict[str, object]:
		index = min(page - 1, len(self._pages) - 1)
		self.friend_calls += 1
		return {"code": 0, "zpData": self._pages[index]}

	def is_success(self, response: dict[str, object]) -> bool:
		return response.get("code") == 0

	def unwrap_data(self, response: dict[str, object]) -> object:
		return response.get("zpData")


# ── find_friend：无可识别 list 容器时首屏即报错 ─────────────────────


@pytest.mark.parametrize(
	"payload",
	[
		{"result": "abc", "hasMore": True},
		{"result": {"a": 1}, "hasMore": True},
		{"result": 5, "hasMore": True},
		{"friendList": "oops", "hasMore": True},
	],
)
def test_drifted_list_container_fails_fast(payload: dict[str, object]) -> None:
	platform = _FakePlatform(payload)

	with pytest.raises(FriendListShapeError):
		find_friend(platform, "u1")

	assert platform.friend_calls == 1, "无效页面不能连续翻页放大平台请求"


def test_empty_container_with_junk_sibling_is_a_terminal_empty_page() -> None:
	platform = _FakePlatform({"result": [], "friendList": "oops", "hasMore": True})

	assert find_friend(platform, "u1") == (None, None)
	assert platform.friend_calls == 1


def test_non_empty_container_with_bad_elements_still_paginates() -> None:
	# Issue #1878 的既有契约：容器非空但元素全被跳过时继续翻页。
	platform = _FakePlatform(
		{"result": [None], "hasMore": True},
		{"result": [FRIEND], "hasMore": False},
	)

	assert find_friend(platform, "u1") == (FRIEND, None)
	assert platform.friend_calls == 2


# ── friend_list_page_has_content：只有非空 list 容器算内容 ───────────


def test_has_content_only_counts_list_containers() -> None:
	assert friend_list_page_has_content({"result": "abc"}) is False
	assert friend_list_page_has_content({"result": {"a": 1}}) is False
	assert friend_list_page_has_content({"result": [], "friendList": "oops"}) is False
	assert friend_list_page_has_content({"result": [None]}) is True
	assert friend_list_page_has_content({"friendList": [FRIEND]}) is True


# ── 聚合路径：junk 兄弟键不再触发翻页 ───────────────────────────────


def test_collect_stops_on_junk_sibling_without_pagination() -> None:
	platform = _FakePlatform({"result": [], "friendList": "oops", "hasMore": True})

	items, error, pages, truncated = collect_friend_list_page(platform)

	assert (items, error, pages, truncated) == ([], None, 1, False)
	assert platform.friend_calls == 1
