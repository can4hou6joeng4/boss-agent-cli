"""BossRecruiterClient unit tests — mock httpx + browser channels."""
import json
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
			params={
				"page": 2,
				"keywords": "Python",
				"tag": "",
				"city": "101010100",
				"gender": "-1",
				"experience": "-1,-1",
				"salary": "-1,-1",
				"age": "-1,-1",
				"applyStatus": "-1",
				"degree": "-1,-1",
				"switchFreq": 0,
				"manageExperience": 0,
				"geekJobRequirements": 0,
				"exchangeResume": 0,
				"viewResume": 0,
				"firstDegree": 0,
				"queryAnd": 0,
				"source": 4,
				"activeness": 0,
				"defaultCondition": 2,
				"hasRcd": 0,
				"filterParams": '{"sortType":1,"region":{"cityCode":"101010100","cityName":"","areas":[]},"overSeaWorkExperience":0,"overSeaWorkLanguage":0,"overSeaWorkWill":0,"manageExperience":0}',
			},
		)
		assert result == mock_result
	client.close()


def test_search_geeks_forwards_new_filters():
	auth = _make_auth()
	client = BossRecruiterClient(auth)
	mock_result = {"code": 0, "zpData": {"list": []}}
	with patch.object(client, "_request", return_value=mock_result) as mock_req:
		result = client.search_geeks(
			"Python",
			page=3,
			job_id="job123",
			experience="3,5",
			degree="201,201",
			age="20,30",
			school_level="1101",
			activeness="2",
			source="5",
			salary="-1,3",
			select=True,
		)
		params = mock_req.call_args.kwargs["params"]
		assert params["jobId"] == "job123"
		assert params["experience"] == "3,5"
		assert params["degree"] == "201,201"
		assert params["age"] == "20,30"
		assert params["schoolLevel"] == "1101"
		assert params["activeness"] == "2"
		assert params["source"] == "5"
		assert params["salary"] == "-1,3"
		assert params["select"] == "true"
		assert params["page"] == 3
		assert result == mock_result
	client.close()


def test_search_geeks_filter_params_city_defaults_to_nationwide():
	auth = _make_auth()
	client = BossRecruiterClient(auth)
	mock_result = {"code": 0, "zpData": {"list": []}}
	with patch.object(client, "_request", return_value=mock_result) as mock_req:
		client.search_geeks("Python")
		params = mock_req.call_args.kwargs["params"]
		filter_params = json.loads(params["filterParams"])
		assert params["city"] == "-2"
		assert filter_params["region"]["cityCode"] == "-2"
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
