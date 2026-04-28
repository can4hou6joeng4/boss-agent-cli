"""补齐缺失的命令测试：chatmsg / mark / exchange / detail / me / show / history / interviews / login / logout"""
import json
from unittest.mock import patch, MagicMock

from click.testing import CliRunner
from boss_agent_cli.main import cli


def _ctx_mock(mock_cls):
	instance = mock_cls.return_value
	instance.__enter__ = lambda self: self
	instance.__exit__ = lambda self, *a: None
	instance.unwrap_data.side_effect = lambda response: response.get("zpData") if "zpData" in response else response.get("data")
	return instance


def _friend_list_response(items):
	return {"zpData": {"result": items, "friendList": items}}


def _make_friend(name="张HR", sid="sec_001", uid=12345):
	return {
		"name": name, "securityId": sid, "uid": uid,
		"title": "HR", "brandName": "TestCo",
		"friendSource": 0, "encryptJobId": "job_001",
		"lastMsg": "你好", "lastTS": 1700000000000,
		"unreadMsgCount": 0, "relationType": 1,
		"lastMessageInfo": {"status": 2},
	}


# ── chatmsg ──────────────────────────────────────────────────────────


@patch("boss_agent_cli.commands.chatmsg.get_platform_instance")
@patch("boss_agent_cli.commands.chatmsg.AuthManager")
def test_chatmsg_success(mock_auth_cls, mock_client_cls):
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.friend_list.return_value = _friend_list_response([_make_friend()])
	mock_client.chat_history.return_value = {
		"zpData": {
			"messages": [
				{"from": {"uid": 99, "name": "张HR"}, "type": 1, "text": "你好", "time": 1700000000000},
				{"from": {"uid": 12345, "name": "我"}, "type": 1, "text": "谢谢", "time": 1700000001000},
			],
		},
	}
	runner = CliRunner()
	result = runner.invoke(cli, ["chatmsg", "sec_001"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["ok"] is True
	assert len(parsed["data"]) == 2
	assert parsed["data"][0]["text"] == "你好"


@patch("boss_agent_cli.commands.chatmsg.get_platform_instance")
@patch("boss_agent_cli.commands.chatmsg.AuthManager")
def test_chatmsg_not_found(mock_auth_cls, mock_client_cls):
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.friend_list.return_value = _friend_list_response([])
	runner = CliRunner()
	result = runner.invoke(cli, ["chatmsg", "sec_nonexistent"])
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["error"]["code"] == "JOB_NOT_FOUND"


@patch("boss_agent_cli.commands.chatmsg.get_platform_instance")
@patch("boss_agent_cli.commands.chatmsg.AuthManager")
def test_chatmsg_supports_data_envelope(mock_auth_cls, mock_client_cls):
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.friend_list.return_value = {"code": 200, "data": {"result": [_make_friend()]}}
	mock_client.chat_history.return_value = {
		"code": 200,
		"data": {
			"messages": [
				{"from": {"uid": 99, "name": "张HR"}, "type": 1, "text": "智联你好", "time": 1700000000000},
			],
		},
	}
	runner = CliRunner()
	result = runner.invoke(cli, ["chatmsg", "sec_001"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["ok"] is True
	assert parsed["data"][0]["text"] == "智联你好"


@patch("boss_agent_cli.commands.chatmsg.get_platform_instance")
@patch("boss_agent_cli.commands.chatmsg.AuthManager")
def test_chatmsg_zhilian_hints_use_platform_specific_commands(mock_auth_cls, mock_client_cls):
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.friend_list.return_value = {"code": 200, "data": {"result": [_make_friend()]}}
	mock_client.chat_history.return_value = {
		"code": 200,
		"data": {
			"messages": [
				{"from": {"uid": 99, "name": "张HR"}, "type": 1, "text": "智联你好", "time": 1700000000000},
			],
		},
	}
	runner = CliRunner()
	result = runner.invoke(cli, ["--platform", "zhilian", "chatmsg", "sec_001"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["hints"]["next_actions"][0] == "boss --platform zhilian chat — 返回沟通列表"
	assert parsed["hints"]["next_actions"][1] == "boss --platform zhilian detail sec_001 — 查看职位详情"


# ── mark ─────────────────────────────────────────────────────────────


@patch("boss_agent_cli.commands.mark.get_platform_instance")
@patch("boss_agent_cli.commands.mark.AuthManager")
def test_mark_add_label(mock_auth_cls, mock_client_cls):
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.friend_list.return_value = _friend_list_response([_make_friend()])
	mock_client.friend_label.return_value = {"code": 0, "zpData": {}}
	runner = CliRunner()
	result = runner.invoke(cli, ["mark", "sec_001", "--label", "沟通中"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["ok"] is True
	assert "沟通中" in parsed["data"]["message"]


@patch("boss_agent_cli.commands.mark.get_platform_instance")
@patch("boss_agent_cli.commands.mark.AuthManager")
def test_mark_remove_label(mock_auth_cls, mock_client_cls):
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.friend_list.return_value = _friend_list_response([_make_friend()])
	mock_client.friend_label.return_value = {"code": 0, "zpData": {}}
	runner = CliRunner()
	result = runner.invoke(cli, ["mark", "sec_001", "--label", "不合适", "--remove"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["data"]["action"] == "移除"


@patch("boss_agent_cli.commands.mark.get_platform_instance")
@patch("boss_agent_cli.commands.mark.AuthManager")
def test_mark_reports_error_when_platform_rejects(mock_auth_cls, mock_client_cls):
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.friend_list.return_value = _friend_list_response([_make_friend()])
	mock_client.friend_label.return_value = {"code": 36, "message": "account risk"}
	mock_client.is_success.return_value = False
	mock_client.parse_error.return_value = ("ACCOUNT_RISK", "account risk")
	runner = CliRunner()
	result = runner.invoke(cli, ["mark", "sec_001", "--label", "沟通中"])
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["ok"] is False
	assert parsed["error"]["code"] == "ACCOUNT_RISK"
	assert parsed["error"]["message"] == "account risk"


@patch("boss_agent_cli.commands.mark.get_platform_instance")
@patch("boss_agent_cli.commands.mark.AuthManager")
def test_mark_not_found(mock_auth_cls, mock_client_cls):
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.friend_list.return_value = _friend_list_response([])
	runner = CliRunner()
	result = runner.invoke(cli, ["mark", "sec_none", "--label", "收藏"])
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["error"]["code"] == "JOB_NOT_FOUND"


# ── exchange ─────────────────────────────────────────────────────────


@patch("boss_agent_cli.commands.exchange.get_platform_instance")
@patch("boss_agent_cli.commands.exchange.AuthManager")
def test_exchange_phone(mock_auth_cls, mock_client_cls):
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.friend_list.return_value = _friend_list_response([_make_friend()])
	mock_client.exchange_contact.return_value = {"code": 0, "zpData": {}}
	runner = CliRunner()
	result = runner.invoke(cli, ["exchange", "sec_001"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["ok"] is True
	assert "手机号" in parsed["data"]["message"]


@patch("boss_agent_cli.commands.exchange.get_platform_instance")
@patch("boss_agent_cli.commands.exchange.AuthManager")
def test_exchange_wechat(mock_auth_cls, mock_client_cls):
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.friend_list.return_value = _friend_list_response([_make_friend()])
	mock_client.exchange_contact.return_value = {"code": 0, "zpData": {}}
	runner = CliRunner()
	result = runner.invoke(cli, ["exchange", "sec_001", "--type", "wechat"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert "微信" in parsed["data"]["message"]


@patch("boss_agent_cli.commands.exchange.get_platform_instance")
@patch("boss_agent_cli.commands.exchange.AuthManager")
def test_exchange_reports_error_when_platform_rejects(mock_auth_cls, mock_client_cls):
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.friend_list.return_value = _friend_list_response([_make_friend()])
	mock_client.exchange_contact.return_value = {"code": 9, "message": "too fast"}
	mock_client.is_success.return_value = False
	mock_client.parse_error.return_value = ("RATE_LIMITED", "too fast")
	runner = CliRunner()
	result = runner.invoke(cli, ["exchange", "sec_001", "--type", "wechat"])
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["ok"] is False
	assert parsed["error"]["code"] == "RATE_LIMITED"
	assert parsed["error"]["message"] == "too fast"


# ── detail ───────────────────────────────────────────────────────────


@patch("boss_agent_cli.commands.detail.CacheStore")
@patch("boss_agent_cli.commands.detail.get_platform_instance")
@patch("boss_agent_cli.commands.detail.AuthManager")
def test_detail_with_job_id(mock_auth_cls, mock_client_cls, mock_cache_cls):
	mock_cache = _ctx_mock(mock_cache_cls)
	mock_cache.is_greeted.return_value = False
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.job_detail.return_value = {
		"code": 0,
		"zpData": {
			"jobInfo": {
				"jobName": "Go 开发",
				"salaryDesc": "30K",
				"experienceName": "3-5年",
				"degreeName": "本科",
				"locationName": "北京",
				"postDescription": "职位描述",
				"showSkills": ["Golang", "K8s"],
				"jobLabels": ["Golang", "K8s"],
				"encryptId": "enc_001",
			},
			"bossInfo": {
				"name": "张总", "title": "CTO",
				"activeTimeDesc": "刚刚活跃",
			},
			"brandComInfo": {
				"brandName": "TestCo",
				"industryName": "互联网",
				"scaleName": "100-499",
				"stageName": "A轮",
				"labels": ["五险一金", "双休"],
			},
		},
	}
	mock_client.unwrap_data.return_value = mock_client.job_detail.return_value["zpData"]
	runner = CliRunner()
	result = runner.invoke(cli, ["detail", "sec_001", "--job-id", "enc_001"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["ok"] is True
	assert parsed["data"]["title"] == "Go 开发"
	assert "Golang" in parsed["data"]["skills"]


@patch("boss_agent_cli.commands.detail.CacheStore")
@patch("boss_agent_cli.commands.detail.get_platform_instance")
@patch("boss_agent_cli.commands.detail.AuthManager")
def test_detail_zhilian_hints_use_platform_specific_commands(mock_auth_cls, mock_client_cls, mock_cache_cls):
	mock_cache = _ctx_mock(mock_cache_cls)
	mock_cache.is_greeted.return_value = False
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.job_detail.return_value = {
		"code": 200,
		"data": {
			"jobInfo": {
				"jobName": "Go 开发",
				"salaryDesc": "30K",
				"experienceName": "3-5年",
				"degreeName": "本科",
				"jobLabels": ["Golang"],
			},
			"bossInfo": {"name": "张总", "title": "CTO"},
			"brandComInfo": {"brandName": "智联测试公司"},
		},
	}
	mock_client.unwrap_data.return_value = mock_client.job_detail.return_value["data"]
	runner = CliRunner()
	result = runner.invoke(cli, ["--platform", "zhilian", "detail", "sec_001", "--job-id", "enc_001"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["hints"]["next_actions"][0] == "boss --platform zhilian greet sec_001 enc_001"
	assert parsed["hints"]["next_actions"][1] == "boss --platform zhilian search <query>"


@patch("boss_agent_cli.commands.show.CacheStore")
@patch("boss_agent_cli.commands.show.get_job_by_index")
@patch("boss_agent_cli.commands.show.get_platform_instance")
@patch("boss_agent_cli.commands.show.AuthManager")
def test_show_zhilian_hints_use_platform_specific_commands(mock_auth_cls, mock_client_cls, mock_get_job_by_index, mock_cache_cls):
	mock_get_job_by_index.return_value = {"security_id": "sec_001"}
	mock_cache = _ctx_mock(mock_cache_cls)
	mock_cache.is_greeted.return_value = False
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.job_card.return_value = {
		"code": 200,
		"data": {
			"jobCard": {
				"encryptJobId": "enc_001",
				"jobName": "Go 开发",
				"brandName": "智联测试公司",
				"salaryDesc": "30K",
				"cityName": "北京",
				"experienceName": "3-5年",
				"degreeName": "本科",
			},
		},
	}
	mock_client.unwrap_data.return_value = mock_client.job_card.return_value["data"]
	runner = CliRunner()
	result = runner.invoke(cli, ["--platform", "zhilian", "show", "1"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["hints"]["next_actions"][0] == "boss --platform zhilian greet sec_001 enc_001"
	assert parsed["hints"]["next_actions"][1] == "boss --platform zhilian search <query>"


# ── me ───────────────────────────────────────────────────────────────


@patch("boss_agent_cli.commands.me.get_platform_instance")
@patch("boss_agent_cli.commands.me.AuthManager")
def test_me_basic(mock_auth_cls, mock_client_cls):
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.user_info.return_value = {"code": 0, "zpData": {"name": "测试用户", "userId": 123}}
	mock_client.resume_baseinfo.return_value = {"code": 0, "zpData": {"degree": "本科"}}
	mock_client.resume_expect.return_value = {"code": 0, "zpData": {"city": "北京"}}
	mock_client.deliver_list.return_value = {"code": 0, "zpData": {"list": []}}
	runner = CliRunner()
	result = runner.invoke(cli, ["me"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["ok"] is True
	assert "测试用户" in str(parsed["data"])


@patch("boss_agent_cli.commands.me.get_platform_instance")
@patch("boss_agent_cli.commands.me.AuthManager")
def test_me_user_section_supports_data_envelope(mock_auth_cls, mock_client_cls):
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.user_info.return_value = {"code": 200, "data": {"name": "智联用户", "email": "z@demo.dev"}}
	runner = CliRunner()
	result = runner.invoke(cli, ["me", "--section", "user"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["ok"] is True
	assert parsed["data"]["user"]["name"] == "智联用户"


@patch("boss_agent_cli.commands.me.get_platform_instance")
@patch("boss_agent_cli.commands.me.AuthManager")
def test_me_zhilian_hints_use_platform_specific_commands(mock_auth_cls, mock_client_cls):
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.user_info.return_value = {"code": 200, "data": {"name": "智联用户"}}
	runner = CliRunner()
	result = runner.invoke(cli, ["--platform", "zhilian", "me", "--section", "user"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["hints"]["next_actions"][0] == "boss --platform zhilian search <关键词> --city <城市>"
	assert parsed["hints"]["next_actions"][1] == "boss --platform zhilian recommend"


# ── history ──────────────────────────────────────────────────────────


@patch("boss_agent_cli.commands.history.get_platform_instance")
@patch("boss_agent_cli.commands.history.AuthManager")
def test_history_success(mock_auth_cls, mock_client_cls):
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.job_history.return_value = {
		"code": 0,
		"zpData": {
			"hasMore": False,
			"jobList": [
				{
					"encryptJobId": "j1", "jobName": "测试岗位",
					"brandName": "公司A", "salaryDesc": "20K",
					"cityName": "北京", "jobExperience": "3-5年",
					"jobDegree": "本科", "bossName": "HR",
					"bossTitle": "招聘", "bossOnline": True,
					"securityId": "sec_h1",
				},
			],
		},
	}
	mock_client.unwrap_data.return_value = mock_client.job_history.return_value["zpData"]
	runner = CliRunner()
	result = runner.invoke(cli, ["history"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["ok"] is True


@patch("boss_agent_cli.commands.history.get_platform_instance")
@patch("boss_agent_cli.commands.history.AuthManager")
def test_history_uses_client_context_manager(mock_auth_cls, mock_client_cls):
	instance = mock_client_cls.return_value
	instance.__enter__ = MagicMock(return_value=instance)
	instance.__exit__ = MagicMock(return_value=None)
	instance.job_history.return_value = {"code": 0, "zpData": {"hasMore": False, "jobList": []}}
	instance.unwrap_data.return_value = instance.job_history.return_value["zpData"]
	runner = CliRunner()
	result = runner.invoke(cli, ["history"])
	assert result.exit_code == 0
	instance.__enter__.assert_called_once()
	instance.__exit__.assert_called_once()


@patch("boss_agent_cli.commands.history.get_platform_instance")
@patch("boss_agent_cli.commands.history.AuthManager")
def test_history_zhilian_hints_use_platform_specific_commands(mock_auth_cls, mock_client_cls):
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.job_history.return_value = {
		"code": 200,
		"data": {
			"hasMore": True,
			"jobList": [
				{
					"encryptJobId": "j1", "jobName": "测试岗位",
					"brandName": "公司A", "salaryDesc": "20K",
					"cityName": "北京", "jobExperience": "3-5年",
					"jobDegree": "本科", "bossName": "HR",
					"bossTitle": "招聘", "bossOnline": True,
					"securityId": "sec_h1",
				},
			],
		},
	}
	mock_client.unwrap_data.return_value = mock_client.job_history.return_value["data"]
	runner = CliRunner()
	result = runner.invoke(cli, ["--platform", "zhilian", "history"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["hints"]["next_actions"][0] == "使用 boss --platform zhilian detail <security_id> 查看职位详情"
	assert parsed["hints"]["next_actions"][1] == "使用 boss --platform zhilian greet <security_id> <job_id> 打招呼"
	assert parsed["hints"]["next_actions"][2] == "使用 boss --platform zhilian history --page 2 查看下一页"


# ── interviews ───────────────────────────────────────────────────────


@patch("boss_agent_cli.commands.interviews.get_platform_instance")
@patch("boss_agent_cli.commands.interviews.AuthManager")
def test_interviews_success(mock_auth_cls, mock_client_cls):
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.interview_data.return_value = {
		"code": 0,
		"zpData": {"interviewList": []},
	}
	mock_client.unwrap_data.return_value = mock_client.interview_data.return_value["zpData"]
	runner = CliRunner()
	result = runner.invoke(cli, ["interviews"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["ok"] is True


@patch("boss_agent_cli.commands.interviews.get_platform_instance")
@patch("boss_agent_cli.commands.interviews.AuthManager")
def test_interviews_supports_zhilian_style_data(mock_auth_cls, mock_client_cls):
	mock_auth_cls.return_value.check_status.return_value = {"cookies": {"zp_token": "x"}}
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.interview_data.return_value = {
		"code": 200,
		"data": {"interviewList": [{"jobName": "测试岗位"}]},
	}
	mock_client.unwrap_data.return_value = mock_client.interview_data.return_value["data"]
	runner = CliRunner()
	result = runner.invoke(cli, ["interviews"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["data"][0]["jobName"] == "测试岗位"


@patch("boss_agent_cli.commands.chat_summary.get_platform_instance")
@patch("boss_agent_cli.commands.chat_summary.AuthManager")
def test_chat_summary_zhilian_hints_use_platform_specific_commands(mock_auth_cls, mock_client_cls):
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.friend_list.return_value = {
		"code": 200,
		"data": {"result": [_make_friend("张HR", "sec_001", 12345)]},
	}
	mock_client.chat_history.return_value = {
		"code": 200,
		"data": {
			"messages": [
				{"from": {"uid": 12345, "name": "张HR"}, "text": "您好", "type": 1, "time": 1700000000000},
				{"from": {"uid": 99999, "name": "我"}, "text": "收到", "type": 1, "time": 1700000001000},
			],
		},
	}
	runner = CliRunner()
	result = runner.invoke(cli, ["--json", "--platform", "zhilian", "chat-summary", "sec_001"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["hints"]["next_actions"][0] == "boss --platform zhilian chat"
	assert parsed["hints"]["next_actions"][1] == "boss --platform zhilian chatmsg sec_001"


@patch("boss_agent_cli.commands.detail.CacheStore")
@patch("boss_agent_cli.commands.detail.get_platform_instance")
@patch("boss_agent_cli.commands.detail.AuthManager")
def test_detail_uses_client_context_manager(mock_auth_cls, mock_client_cls, mock_cache_cls):
	mock_cache = _ctx_mock(mock_cache_cls)
	mock_cache.is_greeted.return_value = False
	instance = mock_client_cls.return_value
	instance.__enter__ = MagicMock(return_value=instance)
	instance.__exit__ = MagicMock(return_value=None)
	instance.job_detail.return_value = {
		"code": 0,
		"zpData": {
			"jobInfo": {"jobName": "Go 开发", "salaryDesc": "30K", "experienceName": "3-5年", "degreeName": "本科"},
			"bossInfo": {"name": "张总", "title": "CTO"},
			"brandComInfo": {"brandName": "TestCo"},
		},
	}
	instance.unwrap_data.return_value = instance.job_detail.return_value["zpData"]
	runner = CliRunner()
	result = runner.invoke(cli, ["detail", "sec_001", "--job-id", "enc_001"])
	assert result.exit_code == 0
	instance.__enter__.assert_called_once()
	instance.__exit__.assert_called_once()


@patch("boss_agent_cli.commands.detail.CacheStore")
@patch("boss_agent_cli.commands.detail.get_platform_instance")
@patch("boss_agent_cli.commands.detail.AuthManager")
def test_detail_httpx_fallback_to_browser_on_auth_error(mock_auth_cls, mock_client_cls, mock_cache_cls):
	"""httpx 快速通道因 AuthError 失败时，应降级到浏览器通道而非直接报错"""
	from boss_agent_cli.api.client import AuthError
	mock_cache = _ctx_mock(mock_cache_cls)
	mock_cache.is_greeted.return_value = False
	mock_cache.get_job_id.return_value = ""
	mock_client = _ctx_mock(mock_client_cls)
	# httpx 通道抛 AuthError（stoken 过期刷新失败）
	mock_client.job_detail.side_effect = AuthError("stoken refresh failed")
	# 浏览器通道返回正常数据
	mock_client.job_card.return_value = {
		"zpData": {
			"jobCard": {
				"encryptJobId": "enc_001",
				"jobName": "Go 开发",
				"brandName": "TestCo",
				"salaryDesc": "30K",
				"cityName": "北京",
				"experienceName": "3-5年",
				"degreeName": "本科",
				"postDescription": "JD 描述",
				"bossName": "张总",
				"bossTitle": "CTO",
				"activeTimeDesc": "刚刚活跃",
			}
		}
	}
	def unwrap_data_side_effect(payload):
		return payload.get("zpData")
	mock_client.unwrap_data.side_effect = unwrap_data_side_effect
	runner = CliRunner()
	result = runner.invoke(cli, ["detail", "sec_001", "--job-id", "enc_001"])
	assert result.exit_code == 0, f"exit_code={result.exit_code}, output={result.output}"
	parsed = json.loads(result.output)
	assert parsed["ok"] is True
	assert parsed["data"]["title"] == "Go 开发"
	# 确认 httpx 被调用后又降级到了浏览器通道
	mock_client.job_detail.assert_called_once()
	mock_client.job_card.assert_called_once()


@patch("boss_agent_cli.commands.detail.CacheStore")
@patch("boss_agent_cli.commands.detail.get_platform_instance")
@patch("boss_agent_cli.commands.detail.AuthManager")
def test_detail_httpx_fallback_to_browser_on_none(mock_auth_cls, mock_client_cls, mock_cache_cls):
	"""httpx 快速通道返回空 jobInfo 时，应降级到浏览器通道"""
	mock_cache = _ctx_mock(mock_cache_cls)
	mock_cache.is_greeted.return_value = False
	mock_cache.get_job_id.return_value = ""
	mock_client = _ctx_mock(mock_client_cls)
	# httpx 返回无 jobInfo（解析后 result=None）
	mock_client.job_detail.return_value = {"zpData": {"jobInfo": {}}}
	# 浏览器通道返回正常数据
	mock_client.job_card.return_value = {
		"zpData": {
			"jobCard": {
				"encryptJobId": "enc_001",
				"jobName": "Go 开发",
				"brandName": "FallbackCo",
				"salaryDesc": "25K",
				"cityName": "深圳",
				"experienceName": "1-3年",
				"degreeName": "大专",
				"postDescription": "降级 JD",
				"bossName": "李总",
				"bossTitle": "HR",
				"activeTimeDesc": "在线",
			}
		}
	}
	def unwrap_data_side_effect(payload):
		return payload.get("zpData")
	mock_client.unwrap_data.side_effect = unwrap_data_side_effect
	runner = CliRunner()
	result = runner.invoke(cli, ["detail", "sec_001", "--job-id", "enc_001"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["ok"] is True
	assert parsed["data"]["company"] == "FallbackCo"


@patch("boss_agent_cli.commands.show.CacheStore")
@patch("boss_agent_cli.commands.show.get_job_by_index")
@patch("boss_agent_cli.commands.show.get_platform_instance")
@patch("boss_agent_cli.commands.show.AuthManager")
def test_show_uses_client_context_manager(mock_auth_cls, mock_client_cls, mock_get_job_by_index, mock_cache_cls):
	mock_get_job_by_index.return_value = {"security_id": "sec_001"}
	mock_cache = _ctx_mock(mock_cache_cls)
	mock_cache.is_greeted.return_value = False
	instance = mock_client_cls.return_value
	instance.__enter__ = MagicMock(return_value=instance)
	instance.__exit__ = MagicMock(return_value=None)
	instance.job_card.return_value = {
		"zpData": {
			"jobCard": {
				"encryptJobId": "enc_001",
				"jobName": "Go 开发",
				"brandName": "TestCo",
				"salaryDesc": "30K",
				"cityName": "北京",
				"experienceName": "3-5年",
				"degreeName": "本科",
			},
		},
	}
	instance.unwrap_data.return_value = instance.job_card.return_value["zpData"]
	runner = CliRunner()
	result = runner.invoke(cli, ["show", "1"])
	assert result.exit_code == 0
	instance.__enter__.assert_called_once()
	instance.__exit__.assert_called_once()


@patch("boss_agent_cli.commands.interviews.get_platform_instance")
@patch("boss_agent_cli.commands.interviews.AuthManager")
def test_interviews_uses_client_context_manager(mock_auth_cls, mock_client_cls):
	mock_auth_cls.return_value.check_status.return_value = {"cookies": {"wt2": "ok"}}
	instance = mock_client_cls.return_value
	instance.__enter__ = MagicMock(return_value=instance)
	instance.__exit__ = MagicMock(return_value=None)
	instance.interview_data.return_value = {"code": 0, "zpData": {"interviewList": []}}
	instance.unwrap_data.return_value = instance.interview_data.return_value["zpData"]
	runner = CliRunner()
	result = runner.invoke(cli, ["interviews"])
	assert result.exit_code == 0
	instance.__enter__.assert_called_once()
	instance.__exit__.assert_called_once()


# ── show ─────────────────────────────────────────────────────────────


def test_show_no_cache(tmp_path):
	"""没有缓存时 show 应返回错误"""
	runner = CliRunner()
	result = runner.invoke(cli, ["--data-dir", str(tmp_path), "show", "1"])
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["ok"] is False


# ── login / logout ───────────────────────────────────────────────────


@patch("boss_agent_cli.commands.login.AuthManager")
def test_login_auth_required(mock_auth_cls):
	"""login 命令基本调用（mock 不实际启动浏览器）"""
	from boss_agent_cli.auth.manager import AuthRequired
	mock_auth_cls.return_value.login.side_effect = AuthRequired("test")
	runner = CliRunner()
	result = runner.invoke(cli, ["login"])
	# login 失败应返回错误
	assert result.exit_code != 0


@patch("boss_agent_cli.commands.logout.AuthManager")
def test_logout_success(mock_auth_cls):
	mock_auth_cls.return_value.logout.return_value = None
	runner = CliRunner()
	result = runner.invoke(cli, ["logout"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["ok"] is True


@patch("boss_agent_cli.commands.logout.AuthManager")
def test_logout_success_for_zhilian_has_platform_specific_next_action(mock_auth_cls):
	mock_auth_cls.return_value.logout.return_value = None
	runner = CliRunner()
	result = runner.invoke(cli, ["--platform", "zhilian", "logout"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["hints"]["next_actions"][0] == "boss --platform zhilian login — 重新登录"


# ── schema 包含新命令 ────────────────────────────────────────────────


def test_schema_includes_new_commands_v2():
	runner = CliRunner()
	result = runner.invoke(cli, ["schema"])
	parsed = json.loads(result.output)
	commands = parsed["data"]["commands"]
	assert "chatmsg" in commands
	assert "mark" in commands
	assert "exchange" in commands
