from types import SimpleNamespace

import pytest

from boss_agent_cli.commands.contact_lookup import FriendLookupLimitExceeded, find_friend_by_security_id


def _platform_from_pages(pages, *, success=lambda response: True):
	calls = []

	def friend_list(*, page):
		calls.append(page)
		return pages[len(calls) - 1]

	platform = SimpleNamespace(
		friend_list=friend_list,
		is_success=success,
		unwrap_data=lambda response: response.get("zpData") if "zpData" in response else response.get("data"),
	)
	return platform, calls


def test_find_friend_by_security_id_returns_none_after_terminal_second_page():
	pages = [
		{"zpData": {"result": [{"securityId": "sec_other"}], "friendList": [{"securityId": "sec_other"}]}},
		{"zpData": {"result": [], "friendList": [], "hasMore": False}},
	]
	index = {"value": 0}

	def friend_list(*, page):
		response = pages[index["value"]]
		index["value"] += 1
		return response

	platform = SimpleNamespace(
		friend_list=friend_list,
		is_success=lambda response: response.get("code", 0) in (0, 200),
		unwrap_data=lambda response: response.get("zpData") if "zpData" in response else response.get("data"),
	)

	friend_item, error_response = find_friend_by_security_id(platform, "sec_missing")

	assert friend_item is None
	assert error_response is None


def test_find_friend_by_security_id_raises_when_pagination_cap_reached_without_terminal_signal():
	def friend_list(*, page):
		return {"zpData": {"result": [{"securityId": f"sec_{page}"}], "friendList": [{"securityId": f"sec_{page}"}], "hasMore": True}}

	platform = SimpleNamespace(
		friend_list=friend_list,
		is_success=lambda response: True,
		unwrap_data=lambda response: response["zpData"],
	)

	with pytest.raises(FriendLookupLimitExceeded):
		find_friend_by_security_id(platform, "sec_missing", max_pages=2)


def test_find_friend_by_security_id_returns_matching_item_and_stops_pagination():
	pages = [
		{
			"zpData": {
				"result": [
					{"securityId": "sec_other", "name": "其他候选人"},
					{"securityId": "sec_target", "name": "目标候选人", "uid": "uid-1"},
				],
				"hasMore": True,
			},
		},
		{"zpData": {"result": [{"securityId": "sec_late"}], "hasMore": False}},
	]
	platform, calls = _platform_from_pages(pages)

	friend_item, error_response = find_friend_by_security_id(platform, "sec_target")

	assert friend_item == {"securityId": "sec_target", "name": "目标候选人", "uid": "uid-1"}
	assert error_response is None
	assert calls == [1]


def test_find_friend_by_security_id_returns_raw_error_response_without_unwrapping():
	error_response = {"code": 500, "message": "server busy", "zpData": {"result": [{"securityId": "sec_target"}]}}
	platform, calls = _platform_from_pages([error_response], success=lambda response: response.get("code") == 0)

	friend_item, returned_error = find_friend_by_security_id(platform, "sec_target")

	assert friend_item is None
	assert returned_error is error_response
	assert calls == [1]


def test_find_friend_by_security_id_terminates_on_repeated_page_signature():
	pages = [
		{"data": {"friendList": [{"securityId": "sec_a"}], "hasMore": True}},
		{"data": {"friendList": [{"securityId": "sec_a"}], "hasMore": True}},
	]
	platform, calls = _platform_from_pages(pages)

	friend_item, error_response = find_friend_by_security_id(platform, "sec_missing", start_page=3, max_pages=5)

	assert friend_item is None
	assert error_response is None
	assert calls == [3, 4]


def test_find_friend_by_security_id_prefers_result_over_friend_list_when_both_exist():
	pages = [
		{
			"zpData": {
				"result": [{"securityId": "sec_result", "source": "result"}],
				"friendList": [{"securityId": "sec_friend", "source": "friendList"}],
				"hasMore": False,
			},
		},
	]
	platform, calls = _platform_from_pages(pages)

	friend_item, error_response = find_friend_by_security_id(platform, "sec_friend")

	assert friend_item is None
	assert error_response is None
	assert calls == [1]
