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


def test_send_message_by_friend_happy_path():
	"""A' 路径：friend_detail → page.evaluate(geekClick + sendText) → ok 信封。"""
	auth = _make_auth()
	client = BossRecruiterClient(auth)
	friend_detail_resp = {
		"code": 0,
		"zpData": {"friendList": [{
			"uid": 12345, "encryptUid": "enc-u",
			"encryptJobId": "enc-j", "securityId": "sec-s",
			"friendSource": 0, "name": "Tester",
		}]},
	}
	with patch.object(client, "_request", return_value=friend_detail_resp), \
		patch.object(client, "_get_browser") as mock_get_browser:
		mock_browser = MagicMock()
		mock_browser.evaluate_js.return_value = {"ok": True, "log": ["geekClick called", "done"]}
		mock_get_browser.return_value = mock_browser

		result = client.send_message_by_friend(12345, "你好")
		assert result["code"] == 0
		# 验证 friendData 拼装：uid → friendId, uniqueId 由 friendId-friendSource 拼成
		js_arg = mock_browser.evaluate_js.call_args[0][1]
		assert js_arg["targetFriendId"] == 12345
		assert js_arg["friendData"]["friendId"] == 12345
		assert js_arg["friendData"]["uniqueId"] == "12345-0"
		assert js_arg["content"] == "你好"
	client.close()


def test_send_message_by_friend_no_friend_returns_error():
	"""friend_detail 返回空列表时，返回 code=-1 错误信封。"""
	auth = _make_auth()
	client = BossRecruiterClient(auth)
	with patch.object(client, "_request", return_value={"code": 0, "zpData": {"friendList": []}}):
		result = client.send_message_by_friend(99999, "x")
		assert result["code"] == -1
		assert "friend_detail" in result["message"]
	client.close()


def test_exchange_request_by_friend_full_chain():
	"""exchange_request_by_friend 走 geekClick + zpblock → test×2 → request 完整链路。"""
	auth = _make_auth()
	client = BossRecruiterClient(auth)
	friend = {"uid": 1, "encryptUid": "u", "encryptJobId": "j", "encryptExpectId": None, "securityId": "sec-old", "name": "Tester", "friendSource": 0}
	friend_detail_resp = {"code": 0, "zpData": {"friendList": [friend]}}
	ok = {"code": 0, "zpData": {}}
	# Mock evaluate_js (the geekClick + read conversation$ call)
	switch_response = {
		"ok": True,
		"encryptUid": "u",
		"encryptJobId": "j",
		"encryptExpectId": "ex-encrypted-from-conv",  # 从 conversation$ 拿到的真实 encryptExpectId
		"securityId": "sec-new-from-conv",
		"name": "Tester",
	}
	with patch.object(client, "_request", return_value=friend_detail_resp), \
		patch.object(client, "_get_browser") as mock_get_browser, \
		patch.object(client, "_evaluate_request", return_value=ok) as mock_er:
		mock_browser = MagicMock()
		mock_browser.evaluate_js.return_value = switch_response
		mock_get_browser.return_value = mock_browser

		result = client.exchange_request_by_friend(1, exchange_type=1)
		assert result == ok
		# 4 个 _evaluate_request 调用按顺序：zpblock → test → test → request
		assert mock_er.call_count == 4
		first_call = mock_er.call_args_list[0]
		assert first_call[0][1] == ep.BOSS_CHAT_REPLY_BLOCK_URL
		assert first_call[1]["data"]["bgSource"] == "12"  # exchange 用 12
		assert first_call[1]["data"]["encryptExpId"] == "ex-encrypted-from-conv"  # 从 conversation$ 拿
		assert first_call[1]["data"]["securityId"] == "sec-new-from-conv"  # 从 conversation$ 拿
		test_call = mock_er.call_args_list[1]
		assert test_call[0][1] == ep.BOSS_EXCHANGE_TEST_URL
		assert test_call[1]["data"] == {"type": 1, "securityId": "sec-new-from-conv"}
		final = mock_er.call_args_list[3]
		assert final[0][1] == ep.BOSS_EXCHANGE_REQUEST_URL
		assert final[1]["data"] == {"type": 1, "securityId": "sec-new-from-conv", "name": "Tester"}
	client.close()


def test_exchange_request_by_friend_aborts_on_zpblock_failure():
	"""zpblock 前置失败时立即返回错误，不调后续 test/request。"""
	auth = _make_auth()
	client = BossRecruiterClient(auth)
	friend_detail_resp = {"code": 0, "zpData": {"friendList": [{"uid": 1, "encryptUid": "u", "encryptJobId": "j", "securityId": "s", "name": "Tester", "friendSource": 0}]}}
	switch_response = {"ok": True, "encryptUid": "u", "encryptJobId": "j", "encryptExpectId": "e", "securityId": "s", "name": "Tester"}
	with patch.object(client, "_request", return_value=friend_detail_resp), \
		patch.object(client, "_get_browser") as mock_get_browser, \
		patch.object(client, "_evaluate_request", return_value={"code": 121, "message": "blocked"}) as mock_er:
		mock_browser = MagicMock()
		mock_browser.evaluate_js.return_value = switch_response
		mock_get_browser.return_value = mock_browser

		result = client.exchange_request_by_friend(1, exchange_type=4)
		assert result["code"] == 121
		# 只调了一次 zpblock，没继续 test/request
		assert mock_er.call_count == 1
	client.close()


def test_send_message_by_friend_page_error_propagated():
	"""页面侧 ok=false 时，错误信息进入 CLI 信封。"""
	auth = _make_auth()
	client = BossRecruiterClient(auth)
	friend_detail_resp = {
		"code": 0,
		"zpData": {"friendList": [{"uid": 1, "encryptUid": "u", "encryptJobId": "j", "securityId": "s", "friendSource": 0}]},
	}
	with patch.object(client, "_request", return_value=friend_detail_resp), \
		patch.object(client, "_get_browser") as mock_get_browser:
		mock_browser = MagicMock()
		mock_browser.evaluate_js.return_value = {"ok": False, "error": "geek-list Vue component not at .chat-user", "log": []}
		mock_get_browser.return_value = mock_browser

		result = client.send_message_by_friend(1, "x")
		assert result["code"] == -1
		assert "geek-list Vue" in result["message"]
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
