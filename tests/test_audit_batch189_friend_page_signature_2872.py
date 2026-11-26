"""整页判重口径与行去重口径必须一致（Issue #2872）。

同一联系人可以在沟通列表里占多行（每行对应一个职位）。旧实现的行去重键是
``(uid, encryptJobId)``，整页签名却只看 ``uid``：同联系人换职位的下一页被当成
重复页，后续页根本不会被请求，结果还被标记为完整。这里用全量打桩的假平台固定
三种情形。
"""

from __future__ import annotations

from typing import Any

from boss_agent_cli.services.friend_list_pages import collect_friend_list_page


class _PagePlatform:
	def __init__(self, pages: dict[int, dict[str, Any]]) -> None:
		self.pages = pages
		self.calls: list[int] = []

	def friend_list(self, *, page: int) -> dict[str, Any]:
		self.calls.append(page)
		assert page in self.pages, f"不应请求第 {page} 页"
		return {"zpData": self.pages[page]}

	@staticmethod
	def is_success(resp: dict[str, Any]) -> bool:
		return resp.get("code") in (None, 0)

	@staticmethod
	def unwrap_data(resp: dict[str, Any]) -> dict[str, Any]:
		return resp.get("zpData") or {}


def _row(uid: str, job_id: str) -> dict[str, Any]:
	return {"uid": uid, "encryptJobId": job_id, "name": "联系人"}


def test_same_contact_different_jobs_across_pages_are_kept() -> None:
	"""三页各一行、前两页 uid 相同但职位不同：三行都要保留，第 3 页必须被请求。"""
	platform = _PagePlatform({
		1: {"result": [_row("contact-a", "job-a")], "hasMore": True},
		2: {"result": [_row("contact-a", "job-b")], "hasMore": True},
		3: {"result": [_row("contact-b", "job-c")], "hasMore": False},
	})

	items, error, pages_scanned, truncated = collect_friend_list_page(platform, max_pages=5)

	assert error is None
	assert platform.calls == [1, 2, 3]
	assert [(item["uid"], item["encryptJobId"]) for item in items] == [
		("contact-a", "job-a"),
		("contact-a", "job-b"),
		("contact-b", "job-c"),
	]
	assert pages_scanned == 3
	assert truncated is False


def test_fully_repeated_page_still_stops_early() -> None:
	"""整页行身份完全一致时仍要早停：修复不能把重复页当成新页无限翻。"""
	first_page = {"result": [_row("contact-a", "job-a"), _row("contact-b", "job-b")], "hasMore": True}
	platform = _PagePlatform({1: first_page, 2: first_page})

	items, error, pages_scanned, truncated = collect_friend_list_page(platform, max_pages=5)

	assert error is None
	assert platform.calls == [1, 2]
	assert [(item["uid"], item["encryptJobId"]) for item in items] == [
		("contact-a", "job-a"),
		("contact-b", "job-b"),
	]
	assert pages_scanned == 2
	assert truncated is False


def test_same_contact_missing_job_id_on_second_page_is_not_duplicate() -> None:
	"""职位 ID 缺失会构成不同的行身份，不能仅凭 uid 相同就丢掉该页。"""
	platform = _PagePlatform({
		1: {"result": [_row("contact-a", "job-a")], "hasMore": True},
		2: {"result": [{"uid": "contact-a", "name": "联系人"}], "hasMore": False},
	})

	items, error, _pages_scanned, truncated = collect_friend_list_page(platform, max_pages=5)

	assert error is None
	assert platform.calls == [1, 2]
	assert [(item["uid"], item.get("encryptJobId")) for item in items] == [
		("contact-a", "job-a"),
		("contact-a", None),
	]
	assert truncated is False
