"""沟通列表多页聚合的重复页判定。

`securityId` 在每次请求间都会轮换（见 `services/contact_lookup.py` 的说明），
拿它做重复页签名永远命中不了；而条目缺少 `securityId` 时签名恒为空元组，
反而会把第二页当成重复页丢掉。两种情况都只在真实翻页时暴露，这里用假平台
固定住。
"""

from __future__ import annotations

from typing import Any

from boss_agent_cli.services.friend_list_pages import collect_friend_list_items


class _PagePlatform:
	"""按页返回固定页面的假平台。"""

	def __init__(self, pages: list[dict[str, Any]], *, error_page: int | None = None) -> None:
		self.pages = pages
		self.error_page = error_page
		self.calls: list[int] = []

	def friend_list(self, *, page: int) -> dict[str, Any]:
		self.calls.append(page)
		if self.error_page == page:
			return {"code": 500, "message": "server busy"}
		if page > len(self.pages):
			return {"zpData": {"result": [], "hasMore": False}}
		return {"zpData": self.pages[page - 1]}

	@staticmethod
	def is_success(resp: dict[str, Any]) -> bool:
		return resp.get("code") in (None, 0)

	@staticmethod
	def unwrap_data(resp: dict[str, Any]) -> dict[str, Any]:
		return resp.get("zpData") or {}


class _RotatingSecurityIdPlatform:
	"""每页都返回同一条联系人，但 securityId 每次请求都变。"""

	def __init__(self) -> None:
		self.calls: list[int] = []

	def friend_list(self, *, page: int) -> dict[str, Any]:
		self.calls.append(page)
		item = {"uid": 10001, "securityId": f"sid_{len(self.calls)}", "name": "甲"}
		return {"zpData": {"result": [item], "hasMore": True}}

	@staticmethod
	def is_success(resp: dict[str, Any]) -> bool:
		return True

	@staticmethod
	def unwrap_data(resp: dict[str, Any]) -> dict[str, Any]:
		return resp.get("zpData") or {}


def test_rotating_security_id_does_not_defeat_duplicate_page_guard() -> None:
	"""同一批联系人重复返回时，轮换的 securityId 不能挡住去重早停。"""
	platform = _RotatingSecurityIdPlatform()

	items, error = collect_friend_list_items(platform)

	assert error is None
	# 第一页收下，第二页判定为重复后停止；否则会把同一页重复抓满 50 页。
	assert platform.calls == [1, 2]
	assert items == [{"uid": 10001, "securityId": "sid_1", "name": "甲"}]


def test_pages_without_identifiers_are_not_treated_as_duplicates() -> None:
	"""条目既无 uid 也无 securityId 时，签名恒为空，不能据此丢掉下一页。"""
	platform = _PagePlatform([
		{"result": [{"name": "甲"}], "hasMore": True},
		{"result": [{"name": "乙"}], "hasMore": False},
	])

	items, error = collect_friend_list_items(platform)

	assert error is None
	assert platform.calls == [1, 2]
	assert items == [{"name": "甲"}, {"name": "乙"}]


def test_failed_page_returns_raw_error_response() -> None:
	"""任一页失败时整次聚合返回空列表和原始响应，交给调用方出错误信封。"""
	platform = _PagePlatform([
		{"result": [{"uid": 1, "securityId": "sid_1"}], "hasMore": True},
	], error_page=2)

	items, error = collect_friend_list_items(platform)

	assert items == []
	assert error == {"code": 500, "message": "server busy"}
