"""BossRecruiterClient unit tests — mock httpx + browser channels."""
from unittest.mock import MagicMock, patch

from boss_agent_cli.api.recruiter_client import BossRecruiterClient
from boss_agent_cli.api import recruiter_endpoints as ep


def _make_auth(token=None):
	auth = MagicMock()
	auth.get_token.return_value = token or {
		"cookies": {"wt2": "fake"},
		"stoken": "fake_stoken",
		"user_agent": "TestAgent",
	}
	return auth


def test_friend_list_calls_post():
	auth = _make_auth()
	client = BossRecruiterClient(auth)
	mock_result = {"code": 0, "zpData": {"list": []}}
	with patch.object(client, "_request", return_value=mock_result) as mock_req:
		result = client.friend_list(page=1)
		mock_req.assert_called_once_with("POST", ep.BOSS_FRIEND_LIST_URL, data={"labelId": 0, "page": 1})
		assert result == mock_result
	client.close()


def test_greet_list_calls_get():
	auth = _make_auth()
	client = BossRecruiterClient(auth)
	mock_result = {"code": 0, "zpData": {"list": []}}
	with patch.object(client, "_request", return_value=mock_result) as mock_req:
		result = client.greet_list(page=1, job_id="abc")
		mock_req.assert_called_once_with(
			"GET", ep.BOSS_GREET_LIST_URL,
			params={"page": 1, "encJobId": "abc"},
		)
		assert result == mock_result
	client.close()


def test_search_geeks_calls_get():
	auth = _make_auth()
	client = BossRecruiterClient(auth)
	mock_result = {"code": 0, "zpData": {"list": []}}
	with patch.object(client, "_request", return_value=mock_result) as mock_req:
		result = client.search_geeks("Python", city="101010100", page=2)
		mock_req.assert_called_once_with(
			"GET", ep.BOSS_SEARCH_GEEK_URL,
			params={"query": "Python", "page": 2, "city": "101010100"},
		)
		assert result == mock_result
	client.close()


def test_view_geek_calls_get():
	auth = _make_auth()
	client = BossRecruiterClient(auth)
	mock_result = {"code": 0, "zpData": {"name": "张三"}}
	with patch.object(client, "_request", return_value=mock_result) as mock_req:
		result = client.view_geek("g1", "j1", security_id="s1")
		mock_req.assert_called_once_with(
			"GET", ep.BOSS_VIEW_GEEK_URL,
			params={"encryptGeekId": "g1", "encryptJobId": "j1", "securityId": "s1"},
		)
		assert result == mock_result
	client.close()


def test_send_message_calls_browser():
	auth = _make_auth()
	client = BossRecruiterClient(auth)
	mock_result = {"code": 0, "zpData": {}}
	with patch.object(client, "_browser_request", return_value=mock_result) as mock_br:
		result = client.send_message(12345, "你好")
		mock_br.assert_called_once_with(
			"POST", ep.BOSS_SEND_MESSAGE_URL,
			data={"gid": 12345, "content": "你好"},
		)
		assert result == mock_result
	client.close()


def test_list_jobs_calls_get():
	auth = _make_auth()
	client = BossRecruiterClient(auth)
	mock_result = {"code": 0, "zpData": {"list": []}}
	with patch.object(client, "_request", return_value=mock_result) as mock_req:
		result = client.list_jobs()
		mock_req.assert_called_once_with("GET", ep.BOSS_JOB_LIST_URL)
		assert result == mock_result
	client.close()


def test_job_offline_calls_browser():
	auth = _make_auth()
	client = BossRecruiterClient(auth)
	mock_result = {"code": 0, "zpData": {}}
	with patch.object(client, "_browser_request", return_value=mock_result) as mock_br:
		result = client.job_offline("enc123")
		mock_br.assert_called_once_with(
			"POST", ep.BOSS_JOB_OFFLINE_URL,
			data={"encryptJobId": "enc123"},
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
