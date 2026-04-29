from types import SimpleNamespace

import pytest

from boss_agent_cli.commands.contact_lookup import FriendLookupLimitExceeded, find_friend_by_security_id


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
