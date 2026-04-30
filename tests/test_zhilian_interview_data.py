"""验证 ZhilianPlatform.interview_data 不再抛 NotImplementedError。"""
from unittest.mock import MagicMock

from boss_agent_cli.platforms.zhilian import ZhilianPlatform


def test_zhilian_interview_data_returns_dict() -> None:
	"""interview_data 应返回 dict，而非抛 NotImplementedError。"""
	mock_client = MagicMock()
	mock_client.interview_data.return_value = {"items": [], "total": 0}
	platform = ZhilianPlatform(client=mock_client)
	result = platform.interview_data()
	assert isinstance(result, dict)
	assert "items" in result


def test_zhilian_interview_data_passes_through_envelope() -> None:
	"""客户端原始包络应透传，由命令层调用 unwrap_data。"""
	mock_client = MagicMock()
	mock_client.interview_data.return_value = {
		"code": 200,
		"data": {"items": [{"id": "iv-1"}]},
	}
	platform = ZhilianPlatform(client=mock_client)
	result = platform.interview_data()
	assert result["code"] == 200
	assert result["data"]["items"][0]["id"] == "iv-1"
