"""招聘者聊天页动作的 MCP 预算覆盖成功路径固定请求（Issue #3010）。

`hr reply` / `hr resume --exchange` / `hr request-resume` 成功路径各占
4 个请求段（好友详情、动作前历史快照、browser-action 窗口、动作后历史回读）；
`hr location` 为好友详情 + 发送工作地点 2 次。默认 120s 低于这些路径的
节流均值，写请求可能已发出而宿主已超时。这里用真实 mock 调用次数验证
估算上界，不复制实现公式。
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from click.testing import CliRunner
import pytest

from boss_agent_cli import mcp_server
from boss_agent_cli.api.recruiter_client import BossRecruiterClient
from boss_agent_cli.main import cli
from boss_agent_cli.mcp_args import _build_args

FRIEND_ID = 12345


def _chat_client() -> BossRecruiterClient:
	client = object.__new__(BossRecruiterClient)
	client.friend_detail = MagicMock(return_value={
		"code": 0,
		"zpData": {"friendList": [{"uid": FRIEND_ID, "securityId": "test-security", "jobId": "test-job"}]},
	})
	client._chat_history_snapshot = MagicMock(return_value=([], None))
	client._run_chat_frontend_action = MagicMock(return_value=({"ok": True}, []))
	client._exchange_history_evidence = MagicMock(return_value={"checked": True, "matched_count": 1})
	client._check_cdp_risk_lock = MagicMock()
	client._browser_request = MagicMock()
	return client


def _request_segments(client: BossRecruiterClient) -> int:
	return (
		client.friend_detail.call_count
		+ client._chat_history_snapshot.call_count
		+ client._run_chat_frontend_action.call_count
		+ client._exchange_history_evidence.call_count
	)


_CHAT_ACTION_ARGVS = (
	("hr", "reply", str(FRIEND_ID), "hello"),
	("hr", "resume", "--exchange", "--friend-id", str(FRIEND_ID)),
	("hr", "request-resume", str(FRIEND_ID)),
)


@pytest.mark.parametrize(
	"call,argv",
	[
		(lambda client: client.send_message_by_friend(FRIEND_ID, "hello"), _CHAT_ACTION_ARGVS[0]),
		(
			lambda client: client.exchange_request_by_friend(FRIEND_ID, exchange_type=1),
			_CHAT_ACTION_ARGVS[1],
		),
		(
			lambda client: client.exchange_request_by_friend(FRIEND_ID, exchange_type=4),
			_CHAT_ACTION_ARGVS[2],
		),
	],
	ids=["reply", "exchange-phone", "request-resume"],
)
def test_chat_action_budget_covers_first_read_hit(call, argv):
	# 首次回读即命中：4 个请求段；估算是最坏上界，必须覆盖它。
	client = _chat_client()
	assert call(client)["code"] == 0
	actual = _request_segments(client)
	assert actual == 4
	for candidate in _CHAT_ACTION_ARGVS:
		assert mcp_server._estimated_requests(candidate) >= actual
	assert mcp_server._estimated_requests(argv) == mcp_server._HR_CHAT_ACTION_REQUESTS


@pytest.mark.parametrize(
	"call,argv",
	[
		(lambda client: client.send_message_by_friend(FRIEND_ID, "hello"), _CHAT_ACTION_ARGVS[0]),
		(
			lambda client: client.exchange_request_by_friend(FRIEND_ID, exchange_type=1),
			_CHAT_ACTION_ARGVS[1],
		),
		(
			lambda client: client.exchange_request_by_friend(FRIEND_ID, exchange_type=4),
			_CHAT_ACTION_ARGVS[2],
		),
	],
	ids=["reply", "exchange-phone", "request-resume"],
)
def test_chat_action_budget_covers_confirmation_retries(call, argv):
	# 首次回读未命中，按客户端重试间隔再读 3 次后命中：最坏 7 个请求段。
	client = _chat_client()
	client._exchange_history_evidence = MagicMock(
		side_effect=[{"checked": True, "matched_count": 0}] * 3 + [{"checked": True, "matched_count": 1}]
	)
	with patch("boss_agent_cli.api.recruiter_client.cancellable_sleep"):
		assert call(client)["code"] == 0
	actual = _request_segments(client)
	assert actual == 7
	for candidate in _CHAT_ACTION_ARGVS:
		assert mcp_server._estimated_requests(candidate) >= actual
	assert mcp_server._estimated_requests(argv) == actual
	assert mcp_server._cli_timeout(argv) == (
		actual * mcp_server._REQUEST_BUDGET_SECONDS + mcp_server._REQUEST_MARGIN_SECONDS
	)


def _location_platform() -> MagicMock:
	platform = MagicMock()
	platform.friend_detail.return_value = {
		"code": 0,
		"zpData": {"friendList": [{"uid": FRIEND_ID, "securityId": "test-security", "jobId": 5566}]},
	}
	platform.send_job_location.return_value = {"code": 0, "zpData": {}}
	platform.is_success.side_effect = lambda response: response.get("code") == 0
	return platform


def _invoke_location(tmp_path: Path, *args: str, platform: MagicMock):
	with patch("boss_agent_cli.commands.recruiter.location.AuthManager"), patch(
		"boss_agent_cli.commands.recruiter.location.get_recruiter_platform_instance"
	) as factory:
		factory.return_value.__enter__.return_value = platform
		return CliRunner().invoke(
			cli, ["--data-dir", str(tmp_path), "--json", "hr", "location", str(FRIEND_ID), *args]
		)


def test_location_budget_covers_friend_detail_and_send(tmp_path):
	platform = _location_platform()
	result = _invoke_location(tmp_path, "--yes", platform=platform)
	assert result.exit_code == 0, result.output
	assert json.loads(result.output)["ok"] is True
	actual = platform.friend_detail.call_count + platform.send_job_location.call_count
	assert actual == 2
	argv = ("hr", "location", str(FRIEND_ID), "--yes")
	assert mcp_server._estimated_requests(argv) == actual
	assert mcp_server._cli_timeout(argv) == actual * mcp_server._REQUEST_BUDGET_SECONDS + mcp_server._REQUEST_MARGIN_SECONDS


def test_location_dry_run_keeps_default_timeout(tmp_path):
	platform = _location_platform()
	result = _invoke_location(tmp_path, "--dry-run", platform=platform)
	assert result.exit_code == 0, result.output
	platform.friend_detail.assert_not_called()
	platform.send_job_location.assert_not_called()
	argv = ("hr", "location", str(FRIEND_ID), "--dry-run")
	assert mcp_server._estimated_requests(argv) is None
	assert mcp_server._cli_timeout(argv) == mcp_server._CLI_TIMEOUT_SECONDS


@pytest.mark.parametrize(
	"argv",
	[
		("hr", "resume", "geek-id", "--job-id", "job-id"),
		("hr", "resume", "--friend-id", str(FRIEND_ID)),
	],
)
def test_plain_resume_keeps_default_timeout(argv):
	# 非 --exchange 形态只查一次在线简历；给出 --friend-id 但漏 --exchange 仍不进入
	# 聊天页动作（Issue #3010）。
	assert mcp_server._estimated_requests(argv) is None
	assert mcp_server._cli_timeout(argv) == mcp_server._CLI_TIMEOUT_SECONDS


@pytest.mark.parametrize(
	"tool,arguments,expected",
	[
		("hr_reply", {"friend_id": FRIEND_ID, "message": "hello"}, 7),
		("hr_exchange", {"friend_id": FRIEND_ID}, 7),
		("hr_request_resume", {"friend_id": FRIEND_ID}, 7),
		("hr_location", {"friend_id": FRIEND_ID, "yes": True}, 2),
	],
)
def test_mapped_tool_argv_gets_same_budget(tool, arguments, expected):
	argv = tuple(_build_args(tool, arguments))
	assert argv[0] == "hr"
	assert mcp_server._estimated_requests(argv) == expected
