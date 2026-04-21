"""BossRecruiterClient unit tests — mock httpx + browser channels."""
from unittest.mock import MagicMock, patch

from boss_agent_cli.api.recruiter_client import BossRecruiterClient


def _make_auth(token=None):
	auth = MagicMock()
	auth.get_token.return_value = token or {
		"cookies": {"wt2": "fake"},
		"stoken": "fake_stoken",
		"user_agent": "TestAgent",
	}
	return auth


def test_list_applications_calls_browser():
	auth = _make_auth()
	client = BossRecruiterClient(auth)
	mock_result = {"code": 0, "zpData": {"list": []}}
	with patch.object(client, "_browser_request", return_value=mock_result) as mock_br:
		result = client.list_applications()
		mock_br.assert_called_once_with("GET", "https://www.zhipin.com/wapi/zpboss/recommend/geeks", params={"page": 1})
		assert result == mock_result
	client.close()


def test_get_resume_calls_httpx():
	auth = _make_auth()
	client = BossRecruiterClient(auth)
	mock_result = {"code": 0, "zpData": {"name": "张三"}}
	with patch.object(client, "_request", return_value=mock_result) as mock_req:
		result = client.get_resume("geek_001", "sec_001")
		mock_req.assert_called_once_with(
			"GET",
			"https://www.zhipin.com/wapi/zpboss/geek/resume",
			params={"geekId": "geek_001", "securityId": "sec_001"},
		)
		assert result == mock_result
	client.close()


def test_request_resume_calls_browser():
	auth = _make_auth()
	client = BossRecruiterClient(auth)
	mock_result = {"code": 0, "zpData": {}}
	with patch.object(client, "_browser_request", return_value=mock_result) as mock_br:
		result = client.request_resume("geek_001")
		mock_br.assert_called_once_with(
			"POST",
			"https://www.zhipin.com/wapi/zpboss/geek/requestResume",
			data={"geekId": "geek_001"},
		)
		assert result == mock_result
	client.close()


def test_list_jobs_calls_httpx():
	auth = _make_auth()
	client = BossRecruiterClient(auth)
	mock_result = {"code": 0, "zpData": {"list": []}}
	with patch.object(client, "_request", return_value=mock_result) as mock_req:
		result = client.list_jobs()
		mock_req.assert_called_once_with(
			"GET",
			"https://www.zhipin.com/wapi/zpboss/job/list",
			params={"page": 1},
		)
		assert result == mock_result
	client.close()


def test_close_is_idempotent():
	auth = _make_auth()
	client = BossRecruiterClient(auth)
	client.close()
	client.close()  # Should not raise


def test_context_manager():
	auth = _make_auth()
	with BossRecruiterClient(auth) as client:
		assert client is not None
