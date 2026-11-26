"""Issue #2883：联系人查找与下游写路径的令牌 / 分页口径一致性。"""

from types import SimpleNamespace

from boss_agent_cli.services.contact_lookup import find_friend, friend_security_id


def _platform_from_pages(pages):
	calls = []

	def friend_list(*, page):
		calls.append(page)
		return pages[len(calls) - 1]

	platform = SimpleNamespace(
		friend_list=friend_list,
		is_success=lambda response: True,
		unwrap_data=lambda response: response["zpData"],
	)
	return platform, calls


def _page(items, *, has_more=False):
	return {"zpData": {"result": items, "hasMore": has_more}}


def test_integer_security_id_is_not_treated_as_a_writable_token():
	"""漂移成整数的 securityId 不能被当成命中：下游只认非空字符串。"""
	item = {"uid": 1, "securityId": 12345}
	platform, calls = _platform_from_pages([_page([item])])

	friend, error = find_friend(platform, "12345")

	assert friend is None
	assert error is None
	assert calls == [1]
	assert friend_security_id(item) == ""


def test_snake_case_security_id_matches_documented_behavior():
	"""docstring 与 friend_security_id 都支持 snake_case，查找也必须支持。"""
	item = {"uid": 1001, "security_id": "sec_target"}
	platform, _ = _platform_from_pages([_page([item])])

	friend, error = find_friend(platform, "sec_target")

	assert friend == item
	assert error is None


def test_same_contact_on_different_jobs_does_not_stop_paging():
	"""同一 uid 的不同职位行不是重复页，目标在第 3 页时不能提前退出。"""
	platform, calls = _platform_from_pages([
		_page([{"uid": 1, "encryptJobId": "j1", "securityId": "sid_1"}], has_more=True),
		_page([{"uid": 1, "encryptJobId": "j2", "securityId": "sid_2"}], has_more=True),
		_page([{"uid": 2, "securityId": "sid_3"}]),
	])

	friend, error = find_friend(platform, "2")

	assert friend == {"uid": 2, "securityId": "sid_3"}
	assert error is None
	assert calls == [1, 2, 3]


def test_genuine_repeated_page_signature_still_stops():
	"""uid 与职位 ID 都相同的重复页仍要早停，防止无界翻页。"""
	platform, calls = _platform_from_pages([
		_page([{"uid": 1, "encryptJobId": "j1", "securityId": "sid_a"}], has_more=True),
		_page([{"uid": 1, "encryptJobId": "j1", "securityId": "sid_b"}], has_more=True),
		_page([{"uid": 2, "securityId": "sid_target"}]),
	])

	friend, error = find_friend(platform, "2")

	assert friend is None
	assert error is None
	assert calls == [1, 2]
