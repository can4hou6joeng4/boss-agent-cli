"""补齐缺失的命令测试：chatmsg / mark / exchange / detail / me / show / history / interviews / login / logout"""
import json
from unittest.mock import patch, MagicMock

from click.testing import CliRunner
from boss_agent_cli.main import cli


def _ctx_mock(mock_cls):
	instance = mock_cls.return_value
	instance.__enter__ = lambda self: self
	instance.__exit__ = lambda self, *a: None
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


@patch("boss_agent_cli.commands.chatmsg.BossClient")
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


@patch("boss_agent_cli.commands.chatmsg.BossClient")
@patch("boss_agent_cli.commands.chatmsg.AuthManager")
def test_chatmsg_not_found(mock_auth_cls, mock_client_cls):
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.friend_list.return_value = _friend_list_response([])
	runner = CliRunner()
	result = runner.invoke(cli, ["chatmsg", "sec_nonexistent"])
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["error"]["code"] == "JOB_NOT_FOUND"


# ── mark ─────────────────────────────────────────────────────────────


@patch("boss_agent_cli.commands.mark.BossClient")
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


@patch("boss_agent_cli.commands.mark.BossClient")
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


@patch("boss_agent_cli.commands.mark.BossClient")
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


@patch("boss_agent_cli.commands.exchange.BossClient")
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


@patch("boss_agent_cli.commands.exchange.BossClient")
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
	runner = CliRunner()
	result = runner.invoke(cli, ["detail", "sec_001", "--job-id", "enc_001"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["ok"] is True
	assert parsed["data"]["title"] == "Go 开发"
	assert "Golang" in parsed["data"]["skills"]


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


# ── history ──────────────────────────────────────────────────────────


@patch("boss_agent_cli.commands.history.BossClient")
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
	runner = CliRunner()
	result = runner.invoke(cli, ["history"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["ok"] is True


@patch("boss_agent_cli.commands.history.BossClient")
@patch("boss_agent_cli.commands.history.AuthManager")
def test_history_uses_client_context_manager(mock_auth_cls, mock_client_cls):
	instance = mock_client_cls.return_value
	instance.__enter__ = MagicMock(return_value=instance)
	instance.__exit__ = MagicMock(return_value=None)
	instance.job_history.return_value = {"code": 0, "zpData": {"hasMore": False, "jobList": []}}
	runner = CliRunner()
	result = runner.invoke(cli, ["history"])
	assert result.exit_code == 0
	instance.__enter__.assert_called_once()
	instance.__exit__.assert_called_once()


# ── interviews ───────────────────────────────────────────────────────


@patch("boss_agent_cli.commands.interviews.get_platform_instance")
@patch("boss_agent_cli.commands.interviews.AuthManager")
def test_interviews_success(mock_auth_cls, mock_client_cls):
	mock_client = _ctx_mock(mock_client_cls)
	mock_client.interview_data.return_value = {
		"code": 0,
		"zpData": {"interviewList": []},
	}
	runner = CliRunner()
	result = runner.invoke(cli, ["interviews"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["ok"] is True


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


# ── schema 包含新命令 ────────────────────────────────────────────────


def test_schema_includes_new_commands_v2():
	runner = CliRunner()
	result = runner.invoke(cli, ["schema"])
	parsed = json.loads(result.output)
	commands = parsed["data"]["commands"]
	assert "chatmsg" in commands
	assert "mark" in commands
	assert "exchange" in commands
