"""联系人稳定身份与 job_card 文本归一（Issue #2063–#2065）。"""

from __future__ import annotations

from types import SimpleNamespace

from boss_agent_cli.api.models import field_text
from boss_agent_cli.services.chat_utils import contact_identity
from boss_agent_cli.services.contact_lookup import find_friend
from boss_agent_cli.services.friend_list_pages import collect_friend_list_page
from boss_agent_cli.services.job_card import build_job_from_card, text_items

HUGE = 10**5000


def _platform(pages):
	calls = []

	def friend_list(*, page):
		calls.append(page)
		return pages[len(calls) - 1]

	return SimpleNamespace(
		friend_list=friend_list,
		is_success=lambda response: True,
		unwrap_data=lambda response: response["zpData"],
	), calls


# ── Issue #2063：布尔 / 超大整数 uid 不能造假重复页与误匹配 ──────────


def test_bool_uid_pages_are_not_treated_as_duplicates() -> None:
	pages = [
		{"zpData": {"result": [{"uid": True, "securityId": "a1"}, {"uid": True, "securityId": "a2"}], "hasMore": True}},
		{"zpData": {"result": [{"uid": True, "securityId": "b1"}, {"uid": True, "securityId": "b2"}], "hasMore": True}},
		{"zpData": {"result": [{"uid": "u-target", "securityId": "t1"}], "hasMore": False}},
	]
	platform, calls = _platform(pages)

	items, error, pages_scanned, truncated = collect_friend_list_page(platform)

	assert error is None
	assert truncated is False
	assert pages_scanned == 3
	assert [item["securityId"] for item in items] == ["a1", "a2", "b1", "b2", "t1"]
	assert calls == [1, 2, 3]


def test_find_friend_keeps_paging_through_bool_uid_pages() -> None:
	pages = [
		{"zpData": {"result": [{"uid": True, "securityId": "a1"}], "hasMore": True}},
		{"zpData": {"result": [{"uid": True, "securityId": "b1"}], "hasMore": True}},
		{"zpData": {"result": [{"uid": "u-target", "securityId": "t1"}], "hasMore": False}},
	]
	platform, _calls = _platform(pages)

	item, error = find_friend(platform, "t1")

	assert error is None
	assert item is not None
	assert item["uid"] == "u-target"


def test_huge_int_uid_and_identifier_do_not_raise() -> None:
	pages = [{"zpData": {"result": [{"uid": HUGE, "securityId": "s1"}], "hasMore": False}}]
	platform, _calls = _platform(pages)

	assert find_friend(platform, HUGE) == (None, None)
	platform2, _calls2 = _platform(pages)
	items, error, _pages, _truncated = collect_friend_list_page(platform2)
	assert error is None
	assert items == [{"uid": HUGE, "securityId": "s1"}]


def test_contact_identity_only_accepts_non_empty_string_or_int_uid() -> None:
	assert contact_identity({"uid": "u1"}) == "u1"
	assert contact_identity({"uid": "  u1  "}) == "u1"
	assert contact_identity({"uid": 10001}) == "10001"
	assert contact_identity({"uid": True, "security_id": "s"}) == "s"
	assert contact_identity({"uid": {}, "security_id": "s"}) == "s"
	assert contact_identity({"uid": [1], "security_id": "s"}) == "s"
	assert contact_identity({"uid": None, "security_id": "s"}) == "s"
	assert contact_identity({"uid": "", "security_id": "s"}) == "s"
	assert contact_identity({"uid": 1.5, "security_id": "s"}) == "s"


# ── Issue #2064：contact_identity 对漂移 uid 回退 security_id ─────────


def test_contact_identity_falls_back_for_drifted_uid() -> None:
	a = {"uid": {}, "security_id": "sec-a", "name": "Alice"}
	b = {"uid": {}, "security_id": "sec-b", "name": "Bob"}
	c = {"uid": True, "security_id": "sec-c", "name": "Carol"}
	d = {"uid": True, "security_id": "sec-d", "name": "Dave"}

	assert contact_identity(a) == "sec-a"
	assert contact_identity(b) == "sec-b"
	assert contact_identity(c) == "sec-c"
	assert contact_identity(d) == "sec-d"
	assert len({contact_identity(item): item for item in (a, b, c, d)}) == 4


def test_contact_identity_huge_uid_and_missing_fallback() -> None:
	assert contact_identity({"uid": HUGE, "security_id": "sec-x"}) == "sec-x"
	assert contact_identity({"uid": HUGE, "security_id": True}) == ""
	assert contact_identity({"uid": "u-real", "security_id": "sec-x"}) == "u-real"


# ── Issue #2065：job_card / field_text 对超大整数按缺失处理 ──────────


def test_text_items_handles_huge_ints_without_raising() -> None:
	assert text_items({"jobLabels": [HUGE]}, "jobLabels") == []
	assert text_items({"jobLabels": [[HUGE]]}, "jobLabels") == []
	assert text_items({"jobLabels": ["ok", HUGE]}, "jobLabels") == ["ok"]


def test_build_job_from_card_handles_huge_job_type() -> None:
	job = build_job_from_card({"jobType": HUGE}, security_id="s", greeted=False)

	assert job["employment_type"] == ""
	assert job["raw_job_type"] == HUGE


def test_field_text_guards_huge_ints() -> None:
	assert field_text({"jobName": HUGE}, "jobName") == ""
	assert field_text({"jobName": 4}, "jobName") == "4"
	assert field_text({"jobName": "x"}, "jobName") == "x"
