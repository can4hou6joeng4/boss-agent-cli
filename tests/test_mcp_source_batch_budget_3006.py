"""招聘者消息预算覆盖 friendSource 分组后的实际批次（Issue #3006）。"""

from types import SimpleNamespace

import pytest

from boss_agent_cli import mcp_server
from boss_agent_cli.services.recruiter_last_messages import fetch_last_messages


@pytest.mark.parametrize("command,option", [("chat", "--summary-limit"), ("last-messages", "--limit")])
@pytest.mark.parametrize("count", [1, 2, 49, 50, 51, 52, 99, 100, 101, 102, 300])
def test_estimate_covers_every_source_partition(command, option, count):
	# 穷举来源 0/1 的条数分配，以实际服务调用次数验证上界，避免测试复制估算公式。
	ids = list(range(880001, 880001 + count))
	requests = []
	platform = SimpleNamespace(
		last_messages=lambda batch, **kwargs: requests.append((list(batch), kwargs)) or {"code": 0, "zpData": []},
		is_success=lambda response: response["code"] == 0,
		unwrap_data=lambda response: response["zpData"],
	)
	maximum = 0
	for split in range(count + 1):
		requests.clear()
		sources = {fid: int(index >= split) for index, fid in enumerate(ids)}
		_, stats = fetch_last_messages(platform, ids, sources=sources)
		assert stats["requests"] == len(requests)
		assert sum(len(batch) for batch, _ in requests) == count
		maximum = max(maximum, len(requests))
	argv = ("hr", command, option, str(count))
	assert mcp_server._estimated_requests(argv) == 1 + maximum
	assert mcp_server._cli_timeout(argv) >= (1 + maximum) * mcp_server._REQUEST_BUDGET_SECONDS


@pytest.mark.parametrize("command", ["chat", "last-messages"])
def test_default_budget_includes_three_source_batches_and_list(command):
	argv = ("hr", command)
	assert mcp_server._estimated_requests(argv) == 4
	assert mcp_server._cli_timeout(argv) == 290.0


@pytest.mark.parametrize("count,expected", [(1, 1), (2, 1), (50, 1), (51, 2), (100, 2), (300, 6)])
def test_explicit_friend_ids_keep_single_source_budget(count, expected):
	argv = ("hr", "last-messages", "--friend-id", "880001", "--limit", str(count))
	assert mcp_server._estimated_requests(argv) == expected


def test_zero_summary_still_only_budgets_friend_list():
	assert mcp_server._estimated_requests(("hr", "chat", "--summary-limit", "0")) == 1


@pytest.mark.parametrize("command,option", [("chat", "--summary-limit"), ("last-messages", "--limit")])
def test_huge_limits_stay_bounded_without_float_conversion(command, option):
	argv = ("hr", command, option, str(10 ** 400))
	assert mcp_server._estimated_requests(argv) == mcp_server._MAX_ESTIMATED_REQUESTS
	assert mcp_server._cli_timeout(argv) == mcp_server._MAX_CLI_TIMEOUT_SECONDS


@pytest.mark.parametrize("command,option", [("chat", "--summary-limit"), ("last-messages", "--limit")])
def test_invalid_limit_preserves_cli_validation_budget(command, option):
	argv = ("hr", command, option, "invalid")
	assert mcp_server._estimated_requests(argv) is None
	assert mcp_server._cli_timeout(argv) == mcp_server._CLI_TIMEOUT_SECONDS
