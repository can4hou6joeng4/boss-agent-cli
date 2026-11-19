"""沟通列表终止条件、职位去重与分页钳制（Issue #1878 #1879 #1880）。"""

from boss_agent_cli.services.contact_lookup import find_friend
from boss_agent_cli.services.friend_list_pages import collect_friend_list_page
from boss_agent_cli.services.job_identity import deduplicate_job_result
from boss_agent_cli.services.pagination import MAX_TRUSTED_PAGE, list_pagination


class _FakePlatform:
	def __init__(self, pages: dict[int, dict]) -> None:
		self.pages = pages
		self.calls: list[int] = []

	def friend_list(self, page: int = 1) -> dict:
		self.calls.append(page)
		return {"page": page}

	def is_success(self, resp: dict) -> bool:
		return True

	def unwrap_data(self, resp: dict) -> dict:
		return self.pages[resp["page"]]


# ── Issue #1878：全坏元素页不是列表结束 ──────────────────────────────


def _three_pages_with_bad_middle() -> dict[int, dict]:
	return {
		1: {"result": [{"uid": 10001, "securityId": "s1"}], "hasMore": True},
		2: {"result": [None, 5], "hasMore": True},
		3: {"result": [{"uid": 99999, "securityId": "s99"}], "hasMore": False},
	}


def test_collect_skips_all_bad_page_instead_of_ending() -> None:
	platform = _FakePlatform(_three_pages_with_bad_middle())

	items, error, pages_scanned, truncated = collect_friend_list_page(platform)

	assert error is None
	assert [item["uid"] for item in items] == [10001, 99999]
	assert pages_scanned == 3
	assert truncated is False
	assert platform.calls == [1, 2, 3]


def test_collect_stops_when_bad_page_is_the_last_page() -> None:
	platform = _FakePlatform({
		1: {"result": [{"uid": 10001}], "hasMore": True},
		2: {"result": [None], "hasMore": False},
	})

	items, error, pages_scanned, truncated = collect_friend_list_page(platform)

	assert [item["uid"] for item in items] == [10001]
	assert pages_scanned == 2
	assert truncated is False


def test_collect_treats_truly_empty_page_as_end() -> None:
	platform = _FakePlatform({
		1: {"result": [{"uid": 10001}], "hasMore": True},
		2: {"result": [], "hasMore": True},
	})

	items, _error, pages_scanned, _truncated = collect_friend_list_page(platform)

	assert [item["uid"] for item in items] == [10001]
	assert pages_scanned == 2


def test_find_friend_reaches_target_after_all_bad_page() -> None:
	platform = _FakePlatform(_three_pages_with_bad_middle())

	friend, error = find_friend(platform, 99999)

	assert error is None
	assert friend is not None
	assert friend["uid"] == 99999
	assert platform.calls == [1, 2, 3]


def test_find_friend_does_not_claim_missing_while_pages_remain() -> None:
	"""坏元素页之后平台说没有更多时才允许返回未找到。"""
	platform = _FakePlatform({
		1: {"result": [None, 5], "hasMore": False},
	})

	friend, error = find_friend(platform, 99999)

	assert (friend, error) == (None, None)
	assert platform.calls == [1]


# ── Issue #1879：非字符串 job_id 不能遮蔽 encryptJobId ───────────────


def test_truthy_non_string_job_id_does_not_shadow_encrypt_job_id() -> None:
	for drifted in (123, {"id": 123}, ["id"]):
		items = [
			{"job_id": drifted, "encryptJobId": "jobA"},
			{"encryptJobId": "jobA"},
		]

		assert deduplicate_job_result(items) == [items[0]]


def test_valid_job_id_still_dedupes() -> None:
	items = [
		{"job_id": "jobA", "encryptJobId": "jobB"},
		{"encryptJobId": "jobA"},
	]

	assert deduplicate_job_result(items) == [items[0]]


def test_items_without_usable_id_are_kept() -> None:
	items = [{"job_id": 123}, {"encryptJobId": ""}, {}]

	assert deduplicate_job_result(items) == items


# ── Issue #1880：回退请求页同样钳制 ──────────────────────────────────


def test_untrusted_request_page_is_clamped_for_next_page_hint() -> None:
	huge_page = int("7" * 4300)

	pagination = list_pagination({"hasMore": True}, page=huge_page)

	assert pagination["page"] == MAX_TRUSTED_PAGE
	assert isinstance(str(pagination["page"] + 1), str)


def test_non_integer_request_page_falls_back_to_one() -> None:
	assert list_pagination({}, page="9")["page"] == 1
	assert list_pagination({}, page=True)["page"] == 1


def test_normal_request_page_is_preserved() -> None:
	assert list_pagination({}, page=3)["page"] == 3
	assert list_pagination({"page": 5}, page=3)["page"] == 5
	assert list_pagination({"page": 10**30}, page=7)["page"] == 7
