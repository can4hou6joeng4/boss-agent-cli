"""MCP 联系人查找请求数纳入宿主超时预算（Issue #3001）。"""

from types import SimpleNamespace

import pytest

from boss_agent_cli import mcp_server
from boss_agent_cli.services import contact_lookup


@pytest.mark.parametrize("command", ["chatmsg", "chat-summary", "mark", "exchange"])
def test_contact_scan_receives_bounded_budget_for_all_pages(command):
	max_pages = getattr(contact_lookup, "MAX_FRIEND_LOOKUP_PAGES", 50)
	argv = (command, "fixture-uid")
	assert mcp_server._estimated_requests(argv) == min(max_pages + 1, mcp_server._MAX_ESTIMATED_REQUESTS)
	# 四页列表 + 一次后继请求，正常节流不能再被默认 120s 提前中断。
	assert mcp_server._cli_timeout(argv) >= 5 * mcp_server._REQUEST_BUDGET_SECONDS + mcp_server._REQUEST_MARGIN_SECONDS
	assert mcp_server._cli_timeout(argv) <= mcp_server._MAX_CLI_TIMEOUT_SECONDS


@pytest.mark.parametrize("argv", [
	("chatmsg", "fixture-access", "--friend-uid", "fixture-uid"),
	("chatmsg", "fixture-access", "--friend-uid=fixture-uid"),
])
def test_direct_friend_uid_keeps_one_request_budget(argv):
	assert mcp_server._estimated_requests(argv) == 1
	assert mcp_server._cli_timeout(argv) == mcp_server._CLI_TIMEOUT_SECONDS


def test_friend_uid_literal_position_does_not_skip_lookup_budget():
	argv = ("chatmsg", "--", "--friend-uid=fixture-uid")
	assert mcp_server._cli_timeout(argv) > mcp_server._CLI_TIMEOUT_SECONDS


def test_default_lookup_page_limit_matches_budget_source():
	pages = []

	def friend_list(*, page):
		pages.append(page)
		return {"result": [{"uid": f"fixture-{page}"}], "hasMore": True}

	platform = SimpleNamespace(friend_list=friend_list, is_success=lambda r: True, unwrap_data=lambda r: r)
	with pytest.raises(contact_lookup.FriendLookupLimitExceeded):
		contact_lookup.find_friend(platform, "missing-fixture")
	assert len(pages) == getattr(contact_lookup, "MAX_FRIEND_LOOKUP_PAGES", 50)
