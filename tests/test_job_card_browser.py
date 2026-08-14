"""job_card_browser 公开方法的测试。

job_card_browser 强制走 CDP 浏览器通道取 JD，不走 httpx。动机：公开的 job_card()
httpx 优先，而 code 37（TOKEN_REFRESH_FAILED）以响应字典返回时不抛异常、不会降级
浏览器，导致走 httpx 拿不到完整 JD。调用方需要一条明确强制浏览器通道的公开路径。
"""

from unittest.mock import patch

from boss_agent_cli.api import endpoints
from boss_agent_cli.api.client import BossClient


def _bare_client():
	"""不执行 __init__（不碰 auth/浏览器），只拿实例调公开方法。"""
	return BossClient.__new__(BossClient)


def test_job_card_browser_forces_browser_channel():
	client = _bare_client()
	with patch.object(client, "_browser_request", return_value={"code": 0, "zpData": {}}) as mock:
		result = client.job_card_browser("sec1", "lid1")

	assert result == {"code": 0, "zpData": {}}
	mock.assert_called_once_with(
		"GET",
		endpoints.JOB_CARD_URL,
		params={"securityId": "sec1", "lid": "lid1"},
	)


def test_job_card_browser_does_not_call_httpx():
	client = _bare_client()
	with patch.object(client, "_browser_request", return_value={"code": 0}), patch.object(
		client, "_request"
	) as mock_request:
		client.job_card_browser("sec1", "lid1")

	mock_request.assert_not_called()
