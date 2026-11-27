"""招聘者附件简历动作的 MCP 预算覆盖内部固定请求（Issue #3009）。

`hr accept-resume` 固定发 3 次平台请求（历史 + 好友详情 + 同意 POST），
`hr download-resume` 固定发 4 次（历史 + 好友详情 + 预览检查 + 下载）。
默认 120s 只够一次请求，若不估算，健康调用会在预检阶段被宿主掐断。
这里用真实 mock 调用次数验证估算上界，不复制实现公式。
"""

from unittest.mock import MagicMock

import httpx
import pytest

from boss_agent_cli import mcp_server
from boss_agent_cli.api.recruiter_client import BossRecruiterClient
from boss_agent_cli.mcp_args import _build_args


def _client() -> BossRecruiterClient:
	client = object.__new__(BossRecruiterClient)
	client.chat_history = MagicMock(return_value={
		"code": 0,
		"zpData": {"messages": [
			{"mid": 456, "from": {"uid": 123, "source": 0}, "body": {"type": 7, "dialog": {"type": 2, "operated": False}}},
		]},
	})
	client.friend_detail = MagicMock(return_value={
		"code": 0,
		"zpData": {"friendList": [{"uid": 123, "securityId": "test-security", "encryptUid": "test-geek"}]},
	})
	client._request = MagicMock(return_value={"code": 0, "zpData": {"status": 0}})
	client._browser_request = MagicMock()
	client._throttle = MagicMock()
	return client


def _platform_requests(client: BossRecruiterClient, download_requests: int = 0) -> int:
	return client.chat_history.call_count + client.friend_detail.call_count + client._request.call_count + download_requests


def test_accept_resume_budget_covers_precheck_and_post():
	argv = ("hr", "accept-resume", "123", "--message-id", "456")
	client = _client()
	assert client.accept_resume_by_friend(123, 456)["zpData"]["status"] == 0
	actual = _platform_requests(client)
	assert actual == 3
	assert mcp_server._estimated_requests(argv) == actual
	assert mcp_server._cli_timeout(argv) == actual * mcp_server._REQUEST_BUDGET_SECONDS + mcp_server._REQUEST_MARGIN_SECONDS


def test_download_resume_budget_covers_preview_and_binary(tmp_path):
	argv = ("hr", "download-resume", "123", "--message-id", "456", "--output", "resume.pdf")
	client = _client()
	attachment = {
		"type": 12,
		"hyperLink": {
			"hyperLinkType": 1,
			"url": "bosszp://bosszhipin.app/openwith?type=openFile&id=resume-test&authType=1",
		},
	}
	client.chat_history.return_value["zpData"]["messages"][0]["body"] = attachment
	client._request.return_value = {"code": 0, "zpData": {"isResumeVisible": True, "isCanPreview": False, "expired": False}}
	downloads = []

	def handler(request):
		downloads.append(request)
		return httpx.Response(200, content=b"%PDF-1.7\nfixture")

	transport = httpx.Client(transport=httpx.MockTransport(handler))
	client._get_client = MagicMock(return_value=transport)
	with transport:
		assert client.download_resume_by_friend(123, 456, tmp_path / "resume.pdf")["code"] == 0
	actual = _platform_requests(client, len(downloads))
	assert actual == 4
	assert mcp_server._estimated_requests(argv) == actual
	assert mcp_server._cli_timeout(argv) == actual * mcp_server._REQUEST_BUDGET_SECONDS + mcp_server._REQUEST_MARGIN_SECONDS


def test_accept_resume_dry_run_keeps_default_timeout():
	argv = ("hr", "accept-resume", "123", "--message-id", "456", "--dry-run")
	assert mcp_server._estimated_requests(argv) is None
	assert mcp_server._cli_timeout(argv) == mcp_server._CLI_TIMEOUT_SECONDS


@pytest.mark.parametrize(
	"tool,arguments,expected",
	[
		("hr_accept_resume", {"friend_id": 123, "message_id": 456}, 3),
		("hr_download_resume", {"friend_id": 123, "message_id": 456, "output": "resume.pdf"}, 4),
	],
)
def test_mapped_tool_argv_gets_same_budget(tool, arguments, expected):
	argv = tuple(_build_args(tool, arguments))
	assert argv[0] == "hr"
	assert mcp_server._estimated_requests(argv) == expected


def test_accept_resume_mapping_keeps_dry_run_default():
	arguments = {"friend_id": 123, "message_id": 456, "dry_run": True}
	argv = tuple(_build_args("hr_accept_resume", arguments))
	assert "--dry-run" in argv
	assert mcp_server._estimated_requests(argv) is None
