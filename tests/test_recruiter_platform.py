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


def test_list_applications_delegates():
	client = _mock_client()
	client.list_applications.return_value = {"code": 0, "zpData": {"result": []}}
	platform = BossRecruiterPlatform(client)
	result = platform.list_applications(page=1, status="active")
	client.list_applications.assert_called_once_with(page=1, status="active")
	assert result == {"code": 0, "zpData": {"result": []}}


def test_get_resume_delegates():
	client = _mock_client()
	client.get_resume.return_value = {"code": 0, "zpData": {"name": "Alice"}}
	platform = BossRecruiterPlatform(client)
	result = platform.get_resume("g1", "s1")
	client.get_resume.assert_called_once_with("g1", "s1")
	assert result == {"code": 0, "zpData": {"name": "Alice"}}


def test_context_manager_closes():
	client = _mock_client()
	with BossRecruiterPlatform(client) as platform:
		assert platform.name == "zhipin-recruiter"
	client.close.assert_called_once()


def test_recruiter_platform_registry():
	cls = get_recruiter_platform("zhipin-recruiter")
	assert cls is BossRecruiterPlatform
