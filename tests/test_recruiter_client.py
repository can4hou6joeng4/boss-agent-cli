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
	"""A' 路径：friend_detail → evaluate_js_with_chat_events → WS 证据命中。"""
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
		mock_browser.evaluate_js_with_chat_events.return_value = {
			"value": {"ok": True, "log": ["geekClick called", "sendText returned undefined"]},
			"events": [{"kind": "ws_send", "bytes": 194, "utf8_bits": ["你好"]}],
		}
		mock_get_browser.return_value = mock_browser

		result = client.send_message_by_friend(12345, "你好")
		assert result["code"] == 0
		assert "events" not in result["zpData"]
		# 验证 friendData 拼装：uid → friendId, uniqueId 由 friendId-friendSource 拼成
		js_arg = mock_browser.evaluate_js_with_chat_events.call_args[0][1]
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
		assert result["zpData"]["action"] == "reply"
		assert result["zpData"]["friendId"] == 99999
		assert result["zpData"]["ok"] is False
		assert result["zpData"]["ws_evidence"]["matched_ws_count"] == 0
	client.close()


def test_exchange_request_by_friend_uses_frontend_component():
	"""exchange_request_by_friend 走 geekClick + ExchangePhone/Resume.handleExChange 前端链路。"""
	auth = _make_auth()
	client = BossRecruiterClient(auth)
	friend = {"uid": 1, "encryptUid": "u", "encryptJobId": "j", "encryptExpectId": None, "securityId": "sec-old", "name": "Tester", "friendSource": 0}
	friend_detail_resp = {"code": 0, "zpData": {"friendList": [friend]}}
	page_response = {
		"ok": True,
		"componentName": "ExchangePhone",
		"confirmed": True,
		"log": ["geekClick called", "found ExchangePhone type=1", "handleExChange returned"],
	}
	with patch.object(client, "_request", return_value=friend_detail_resp), \
		patch.object(client, "_get_browser") as mock_get_browser:
		mock_browser = MagicMock()
		mock_browser.evaluate_js_with_chat_events.return_value = {
			"value": page_response,
			"events": [{"kind": "ws_send", "bytes": 194, "utf8_bits": ["请求交换联系方式"]}],
		}
		mock_get_browser.return_value = mock_browser

		result = client.exchange_request_by_friend(1, exchange_type=1)
		assert result["code"] == 0
		assert result["zpData"]["friendId"] == 1
		assert result["zpData"]["componentName"] == "ExchangePhone"
		assert result["zpData"]["exchange_type"] == 1
		assert result["zpData"]["matched_ws_count"] == 1
		js_arg = mock_browser.evaluate_js_with_chat_events.call_args[0][1]
		assert js_arg["componentName"] == "ExchangePhone"
		assert js_arg["targetFriendId"] == 1
	assert js_arg["friendData"]["uniqueId"] == "1-0"
	client.close()


def test_exchange_request_by_friend_maps_wechat_to_exchangewx():
	"""exchange_type=2 应命中 ExchangeWx 前端组件。"""
	auth = _make_auth()
	client = BossRecruiterClient(auth)
	friend_detail_resp = {
		"code": 0,
		"zpData": {"friendList": [{"uid": 1, "encryptUid": "u", "encryptJobId": "j", "securityId": "s", "name": "Tester", "friendSource": 0}]},
	}
	with patch.object(client, "_request", return_value=friend_detail_resp), \
		patch.object(client, "_get_browser") as mock_get_browser:
		mock_browser = MagicMock()
		mock_browser.evaluate_js_with_chat_events.return_value = {
			"value": {"ok": True, "componentName": "ExchangeWx", "confirmed": True, "log": []},
			"events": [{"kind": "ws_send", "bytes": 194, "utf8_bits": ["请求交换联系方式"]}],
		}
		mock_get_browser.return_value = mock_browser

		result = client.exchange_request_by_friend(1, exchange_type=2)
		assert result["code"] == 0
		js_arg = mock_browser.evaluate_js_with_chat_events.call_args[0][1]
		assert js_arg["componentName"] == "ExchangeWx"
	client.close()


def test_exchange_request_by_friend_page_error_propagated():
	"""页面侧 Exchange 组件失败时，错误信息进入 CLI 信封。"""
	auth = _make_auth()
	client = BossRecruiterClient(auth)
	friend_detail_resp = {"code": 0, "zpData": {"friendList": [{"uid": 1, "encryptUid": "u", "encryptJobId": "j", "securityId": "s", "name": "Tester", "friendSource": 0}]}}
	with patch.object(client, "_request", return_value=friend_detail_resp), \
		patch.object(client, "_get_browser") as mock_get_browser:
		mock_browser = MagicMock()
		mock_browser.evaluate_js_with_chat_events.return_value = {
			"value": {"ok": False, "error": "ExchangeResume Vue component not found", "log": []},
			"events": [],
		}
		mock_get_browser.return_value = mock_browser

		result = client.exchange_request_by_friend(1, exchange_type=4)
		assert result["code"] == -1
		assert "ExchangeResume" in result["message"]
		assert result["zpData"]["error"] == "ExchangeResume Vue component not found"
		assert result["zpData"]["action"] == "exchange"
		assert result["zpData"]["friendId"] == 1
		assert result["zpData"]["exchange_type"] == 4
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
		mock_browser.evaluate_js_with_chat_events.return_value = {
			"value": {"ok": False, "error": "geek-list Vue component not at .chat-user", "log": []},
			"events": [],
		}
		mock_get_browser.return_value = mock_browser

		result = client.send_message_by_friend(1, "x")
		assert result["code"] == -1
		assert "geek-list Vue" in result["message"]
		assert result["zpData"]["action"] == "reply"
	client.close()


def test_send_message_by_friend_without_real_ws_send_returns_error():
	"""只出现 suggestion 等旁路流量时，不应乐观判成功。"""
	auth = _make_auth()
	client = BossRecruiterClient(auth)
	private_message = "候选人张三问薪资 30K 可否远程"
	friend_detail_resp = {
		"code": 0,
		"zpData": {"friendList": [{"uid": 1, "encryptUid": "u", "encryptJobId": "j", "securityId": "s", "friendSource": 0}]},
	}
	with patch.object(client, "_request", return_value=friend_detail_resp), \
		patch.object(client, "_get_browser") as mock_get_browser:
		mock_browser = MagicMock()
		mock_browser.evaluate_js_with_chat_events.return_value = {
			"value": {"ok": True, "log": ["sendText returned undefined"]},
			"events": [{"kind": "ws_send", "bytes": 156, "utf8_bits": ["/message/suggest", private_message]}],
		}
		mock_get_browser.return_value = mock_browser

		result = client.send_message_by_friend(1, "x")
		assert result["code"] == -1
		assert "no confirmed chat websocket send detected" in result["message"]
		assert result["zpData"]["ws_evidence"]["matched_ws_count"] == 0
		assert "sample_bits" not in result["zpData"]["ws_evidence"]
		assert private_message not in json.dumps(result, ensure_ascii=False)
	client.close()


def test_exchange_request_by_friend_without_real_ws_send_returns_error():
	"""exchange 也必须命中真实 chat WS 帧，DOM 文案不足以判成功。"""
	auth = _make_auth()
	client = BossRecruiterClient(auth)
	friend_detail_resp = {
		"code": 0,
		"zpData": {"friendList": [{"uid": 1, "encryptUid": "u", "encryptJobId": "j", "securityId": "s", "friendSource": 0}]},
	}
	with patch.object(client, "_request", return_value=friend_detail_resp), \
		patch.object(client, "_get_browser") as mock_get_browser:
		mock_browser = MagicMock()
		mock_browser.evaluate_js_with_chat_events.return_value = {
			"value": {"ok": True, "componentName": "ExchangeResume", "confirmed": True, "log": ["handleExChange returned"]},
			"events": [{"kind": "ws_send", "bytes": 156, "utf8_bits": ["/message/suggest", "query"]}],
		}
		mock_get_browser.return_value = mock_browser

		result = client.exchange_request_by_friend(1, exchange_type=4)
		assert result["code"] == -1
		assert "no confirmed chat websocket send detected" in result["message"]
		assert result["zpData"]["action"] == "exchange"
		assert result["zpData"]["ws_evidence"]["matched_ws_count"] == 0
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
