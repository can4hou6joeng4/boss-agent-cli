from types import SimpleNamespace

import pytest

from boss_agent_cli.services.contact_lookup import (
	FriendLookupLimitExceeded,
	find_friend,
	find_friend_by_security_id,
)


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


# ── uid 匹配（security_id 每请求轮换，不能作为唯一匹配依据） ──────────


def test_find_friend_matches_by_uid_when_security_id_rotates():
	"""回归：securityId 每次请求都不同，按 uid 匹配必须仍然命中。

	这是 chatmsg 报 JOB_NOT_FOUND 的根因场景——调用方拿到的 security_id
	与本次 friend_list 返回的值必然不同，只有 uid 能稳定关联。
	"""
	platform, calls = _platform_from_pages([
		{
			"zpData": {
				"result": [
					{"uid": 999, "securityId": "sid_this_request_other", "name": "其他人"},
					{"uid": 10001, "securityId": "sid_this_request_target", "name": "联系人甲"},
				],
				"hasMore": False,
			},
		},
	])

	friend_item, error_response = find_friend(platform, "10001")

	assert friend_item is not None
	assert friend_item["name"] == "联系人甲"
	# 返回的是本次请求的新 securityId，调用方应使用它去请求消息历史
	assert friend_item["securityId"] == "sid_this_request_target"
	assert error_response is None
	assert calls == [1]


def test_find_friend_accepts_uid_as_int_or_str():
	"""uid 在 API 中是整数、在命令行里是字符串，两种入参都应命中。"""
	pages = [{"zpData": {"result": [{"uid": 10001, "securityId": "sid_a"}], "hasMore": False}}]

	for needle in ("10001", 10001):
		platform, _ = _platform_from_pages(pages)
		friend_item, _ = find_friend(platform, needle)
		assert friend_item is not None, f"未命中：{needle!r}"


def test_find_friend_still_matches_by_security_id():
	"""向后兼容：security_id 仍然可匹配（历史脚本不受影响）。"""
	platform, _ = _platform_from_pages([
		{"zpData": {"result": [{"uid": 1, "securityId": "sec_target"}], "hasMore": False}},
	])
	friend_item, _ = find_friend(platform, "sec_target")
	assert friend_item == {"uid": 1, "securityId": "sec_target"}


def test_find_friend_uid_takes_precedence_over_security_id():
	"""当入参同时可能是 uid 与其他人的 security_id 时，uid 优先命中。"""
	platform, _ = _platform_from_pages([
		{
			"zpData": {
				"result": [
					{"uid": 1, "securityId": "10001", "name": "仅 securityId 相同"},
					{"uid": 10001, "securityId": "sid_target", "name": "按 uid 应命中的"},
				],
				"hasMore": False,
			},
		},
	])
	friend_item, _ = find_friend(platform, "10001")
	assert friend_item["name"] == "按 uid 应命中的"


def test_find_friend_empty_identifier_returns_none_without_calling_api():
	"""空标识应立即返回，不触发任何平台请求。"""
	platform, calls = _platform_from_pages([])
	friend_item, error_response = find_friend(platform, "   ")
	assert friend_item is None
	assert error_response is None
	assert calls == []


def test_find_friend_ignores_non_dict_items():
	"""列表里混入非 dict 项时不应抛 AttributeError。"""
	platform, _ = _platform_from_pages([
		{"zpData": {"result": ["garbage", None, {"uid": 7, "securityId": "sec_7"}], "hasMore": False}},
	])
	friend_item, error_response = find_friend(platform, "7")
	assert friend_item == {"uid": 7, "securityId": "sec_7"}
	assert error_response is None
