"""RecruiterPlatform ABC + BossRecruiterPlatform adapter 测试。"""

from unittest.mock import MagicMock

from boss_agent_cli.platforms import get_recruiter_platform
from boss_agent_cli.platforms.zhipin_recruiter import BossRecruiterPlatform


def _mock_client():
	client = MagicMock()
	client.close = MagicMock()
	return client


def test_boss_recruiter_platform_metadata():
	client = _mock_client()
	platform = BossRecruiterPlatform(client)
	assert platform.name == "zhipin-recruiter"
	assert "招聘者" in platform.display_name


def test_boss_recruiter_is_success():
	client = _mock_client()
	platform = BossRecruiterPlatform(client)
	assert platform.is_success({"code": 0}) is True
	assert platform.is_success({"code": 1}) is False


def test_boss_recruiter_unwrap_data():
	client = _mock_client()
	platform = BossRecruiterPlatform(client)
	response = {"code": 0, "zpData": {"jobs": [1, 2, 3]}}
	assert platform.unwrap_data(response) == {"jobs": [1, 2, 3]}


def test_boss_recruiter_parse_error():
	client = _mock_client()
	platform = BossRecruiterPlatform(client)
	unified, message = platform.parse_error({"code": 9, "message": "too fast"})
	assert unified == "RATE_LIMITED"
	assert "too fast" in message


def test_friend_list_delegates():
	client = _mock_client()
	client.friend_list.return_value = {"code": 0, "zpData": {"result": []}}
	platform = BossRecruiterPlatform(client)
	result = platform.friend_list(page=1, job_id="j1")
	client.friend_list.assert_called_once_with(page=1, job_id="j1", label_id=0)
	assert result == {"code": 0, "zpData": {"result": []}}


def test_view_geek_delegates():
	client = _mock_client()
	client.view_geek.return_value = {"code": 0, "zpData": {"name": "Alice"}}
	platform = BossRecruiterPlatform(client)
	result = platform.view_geek("g1", "j1", security_id="s1")
	client.view_geek.assert_called_once_with("g1", job_id="j1", security_id="s1")
	assert result == {"code": 0, "zpData": {"name": "Alice"}}


def test_search_geeks_delegates():
	client = _mock_client()
	client.search_geeks.return_value = {"code": 0, "zpData": {"list": []}}
	platform = BossRecruiterPlatform(client)
	result = platform.search_geeks("Python", city="101010100", page=2)
	client.search_geeks.assert_called_once_with(
		"Python", city="101010100", page=2, job_id=None, experience=None, degree=None,
	)
	assert result == {"code": 0, "zpData": {"list": []}}


def test_job_offline_delegates():
	client = _mock_client()
	client.job_offline.return_value = {"code": 0, "zpData": {}}
	platform = BossRecruiterPlatform(client)
	result = platform.job_offline("enc123")
	client.job_offline.assert_called_once_with("enc123")
	assert result == {"code": 0, "zpData": {}}


def test_send_message_delegates():
	client = _mock_client()
	client.send_message.return_value = {"code": 0, "zpData": {}}
	platform = BossRecruiterPlatform(client)
	result = platform.send_message(123, "你好")
	client.send_message.assert_called_once_with(123, "你好")
	assert result == {"code": 0, "zpData": {}}


def test_context_manager_closes():
	client = _mock_client()
	with BossRecruiterPlatform(client) as platform:
		assert platform.name == "zhipin-recruiter"
	client.close.assert_called_once()


def test_recruiter_platform_registry():
	cls = get_recruiter_platform("zhipin-recruiter")
	assert cls is BossRecruiterPlatform
