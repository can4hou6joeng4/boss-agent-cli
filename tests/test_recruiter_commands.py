import json
from unittest.mock import patch

from click.testing import CliRunner

from boss_agent_cli.main import cli
from boss_agent_cli.commands.recruiter.resume_parser import parse_resume


def _ctx_mock(mock_cls):
	instance = mock_cls.return_value
	instance.__enter__ = lambda self: self
	instance.__exit__ = lambda self, *a: None
	instance.unwrap_data.side_effect = lambda response: response.get("zpData") if "zpData" in response else response.get("data")
	instance.is_success.side_effect = lambda response: response.get("code", 0) in (0, 200)
	return instance


def _invoke(*args):
	runner = CliRunner()
	return runner.invoke(cli, ["--role", "recruiter", *args])


def _assert_error_contract(parsed: dict, *, code: str, message: str, recoverable: bool, recovery_action: str | None) -> None:
	assert parsed["error"]["code"] == code
	assert parsed["error"]["message"] == message
	assert parsed["error"]["recoverable"] is recoverable
	assert parsed["error"]["recovery_action"] == recovery_action


@patch("boss_agent_cli.commands.recruiter.candidates.get_recruiter_platform_instance")
@patch("boss_agent_cli.commands.recruiter.candidates.AuthManager")
def test_recruiter_candidates_supports_data_envelope(mock_auth_cls, mock_platform_cls):
	mock_platform = _ctx_mock(mock_platform_cls)
	mock_platform.search_geeks.return_value = {
		"code": 200,
		"data": {"geekList": [{"name": "候选人A"}], "hasMore": False},
	}
	result = _invoke("hr", "candidates", "python")
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["ok"] is True
	assert parsed["data"]["geekList"][0]["name"] == "候选人A"
	assert parsed["hints"]["next_actions"][0] == "boss hr resume <geek_id> --job-id <id> --security-id <id> — 查看简历"
	assert parsed["hints"]["next_actions"][1] == "boss hr chat — 查看沟通"


@patch("boss_agent_cli.commands.recruiter.candidates.get_recruiter_platform_instance")
@patch("boss_agent_cli.commands.recruiter.candidates.AuthManager")
def test_recruiter_candidates_reports_error_when_platform_rejects(mock_auth_cls, mock_platform_cls):
	mock_platform = _ctx_mock(mock_platform_cls)
	mock_platform.search_geeks.return_value = {"code": 9, "message": "too fast"}
	mock_platform.parse_error.return_value = ("RATE_LIMITED", "too fast")
	result = _invoke("hr", "candidates", "python")
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["error"]["code"] == "RATE_LIMITED"
	assert parsed["error"]["message"] == "too fast"


@patch("boss_agent_cli.commands.recruiter.chat.get_recruiter_platform_instance")
@patch("boss_agent_cli.commands.recruiter.chat.AuthManager")
def test_recruiter_chat_supports_data_envelope(mock_auth_cls, mock_platform_cls):
	mock_platform = _ctx_mock(mock_platform_cls)
	mock_platform.friend_list.return_value = {
		"code": 200,
		"data": {"friendList": [{"name": "候选人B"}]},
	}
	result = _invoke("hr", "chat")
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["data"]["friendList"][0]["name"] == "候选人B"
	assert parsed["hints"]["next_actions"][0] == "boss hr resume <geek_id> --job-id <id> --security-id <id> — 查看候选人简历"


@patch("boss_agent_cli.commands.recruiter.chat.get_recruiter_platform_instance")
@patch("boss_agent_cli.commands.recruiter.chat.AuthManager")
def test_recruiter_chat_reports_error_when_platform_rejects(mock_auth_cls, mock_platform_cls):
	mock_platform = _ctx_mock(mock_platform_cls)
	mock_platform.friend_list.return_value = {"code": 37, "message": "stoken expired"}
	mock_platform.parse_error.return_value = ("TOKEN_REFRESH_FAILED", "stoken expired")
	result = _invoke("hr", "chat")
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	_assert_error_contract(
		parsed,
		code="TOKEN_REFRESH_FAILED",
		message="stoken expired",
		recoverable=True,
		recovery_action="boss login",
	)


@patch("boss_agent_cli.commands.recruiter.applications.get_recruiter_platform_instance")
@patch("boss_agent_cli.commands.recruiter.applications.AuthManager")
def test_recruiter_applications_supports_data_envelope(mock_auth_cls, mock_platform_cls):
	mock_platform = _ctx_mock(mock_platform_cls)
	mock_platform.friend_list.return_value = {
		"code": 200,
		"data": {"friendList": [{"name": "候选人C"}]},
	}
	result = _invoke("hr", "applications")
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["data"]["friendList"][0]["name"] == "候选人C"
	assert parsed["hints"]["next_actions"][0] == "boss hr resume <geek_id> --job-id <id> --security-id <id> — 查看候选人简历"
	assert parsed["hints"]["next_actions"][1] == "boss hr chat — 查看沟通列表"


@patch("boss_agent_cli.commands.recruiter.applications.get_recruiter_platform_instance")
@patch("boss_agent_cli.commands.recruiter.applications.AuthManager")
def test_recruiter_applications_reports_error_when_platform_rejects(mock_auth_cls, mock_platform_cls):
	mock_platform = _ctx_mock(mock_platform_cls)
	mock_platform.friend_list.return_value = {"code": 36, "message": "account risk"}
	mock_platform.parse_error.return_value = ("ACCOUNT_RISK", "account risk")
	result = _invoke("hr", "applications")
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	_assert_error_contract(
		parsed,
		code="ACCOUNT_RISK",
		message="account risk",
		recoverable=True,
		recovery_action="启动 CDP Chrome 重试，或联系客服",
	)


@patch("boss_agent_cli.commands.recruiter.jobs.get_recruiter_platform_instance")
@patch("boss_agent_cli.commands.recruiter.jobs.AuthManager")
def test_recruiter_jobs_list_supports_data_envelope(mock_auth_cls, mock_platform_cls):
	mock_platform = _ctx_mock(mock_platform_cls)
	mock_platform.list_jobs.return_value = {
		"code": 200,
		"data": {"jobList": [{"jobName": "后端工程师"}]},
	}
	result = _invoke("hr", "jobs", "list")
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["data"]["jobList"][0]["jobName"] == "后端工程师"


@patch("boss_agent_cli.commands.recruiter.jobs.get_recruiter_platform_instance")
@patch("boss_agent_cli.commands.recruiter.jobs.AuthManager")
def test_recruiter_jobs_list_reports_error_when_platform_rejects(mock_auth_cls, mock_platform_cls):
	mock_platform = _ctx_mock(mock_platform_cls)
	mock_platform.list_jobs.return_value = {"code": 9, "message": "too fast"}
	mock_platform.parse_error.return_value = ("RATE_LIMITED", "too fast")
	result = _invoke("hr", "jobs", "list")
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	_assert_error_contract(
		parsed,
		code="RATE_LIMITED",
		message="too fast",
		recoverable=True,
		recovery_action="等待后重试",
	)


@patch("boss_agent_cli.commands.recruiter.jobs.get_recruiter_platform_instance")
@patch("boss_agent_cli.commands.recruiter.jobs.AuthManager")
def test_recruiter_jobs_offline_reports_error_when_platform_rejects(mock_auth_cls, mock_platform_cls):
	mock_platform = _ctx_mock(mock_platform_cls)
	mock_platform.job_offline.return_value = {"code": 37, "message": "stoken expired"}
	mock_platform.is_success.return_value = False
	mock_platform.parse_error.return_value = ("TOKEN_REFRESH_FAILED", "stoken expired")
	result = _invoke("hr", "jobs", "offline", "enc-job-1")
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["ok"] is False
	_assert_error_contract(
		parsed,
		code="TOKEN_REFRESH_FAILED",
		message="stoken expired",
		recoverable=True,
		recovery_action="boss login",
	)


@patch("boss_agent_cli.commands.recruiter.jobs.get_recruiter_platform_instance")
@patch("boss_agent_cli.commands.recruiter.jobs.AuthManager")
def test_recruiter_jobs_online_reports_error_when_platform_rejects(mock_auth_cls, mock_platform_cls):
	mock_platform = _ctx_mock(mock_platform_cls)
	mock_platform.job_online.return_value = {"code": 9, "message": "too fast"}
	mock_platform.is_success.return_value = False
	mock_platform.parse_error.return_value = ("RATE_LIMITED", "too fast")
	result = _invoke("hr", "jobs", "online", "enc-job-1")
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["ok"] is False
	_assert_error_contract(
		parsed,
		code="RATE_LIMITED",
		message="too fast",
		recoverable=True,
		recovery_action="等待后重试",
	)


@patch("boss_agent_cli.commands.recruiter.resume.get_recruiter_platform_instance")
@patch("boss_agent_cli.commands.recruiter.resume.AuthManager")
def test_recruiter_resume_exchange_supports_data_envelope(mock_auth_cls, mock_platform_cls):
	mock_platform = _ctx_mock(mock_platform_cls)
	mock_platform.exchange_request.return_value = {
		"code": 200,
		"data": {"exchangeStatus": "sent"},
	}
	result = _invoke("hr", "resume", "geek-1", "--exchange", "--uid", "1", "--gid", "2", "--job-id", "3")
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["data"]["exchangeStatus"] == "sent"
	assert "联系方式交换请求已发送" == parsed["data"]["message"]
	assert parsed["hints"]["next_actions"][0] == "boss hr applications — 返回候选人列表"


@patch("boss_agent_cli.commands.recruiter.resume.get_recruiter_platform_instance")
@patch("boss_agent_cli.commands.recruiter.resume.AuthManager")
def test_recruiter_resume_exchange_reports_error_when_platform_rejects(mock_auth_cls, mock_platform_cls):
	mock_platform = _ctx_mock(mock_platform_cls)
	mock_platform.exchange_request.return_value = {"code": 37, "message": "stoken expired"}
	mock_platform.is_success.return_value = False
	mock_platform.parse_error.return_value = ("TOKEN_REFRESH_FAILED", "stoken expired")
	result = _invoke("hr", "resume", "geek-1", "--exchange", "--uid", "1", "--gid", "2", "--job-id", "3")
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["ok"] is False
	_assert_error_contract(
		parsed,
		code="TOKEN_REFRESH_FAILED",
		message="stoken expired",
		recoverable=True,
		recovery_action="boss login",
	)


@patch("boss_agent_cli.commands.recruiter.resume.get_recruiter_platform_instance")
@patch("boss_agent_cli.commands.recruiter.resume.AuthManager")
def test_recruiter_resume_parse_supports_data_envelope(mock_auth_cls, mock_platform_cls):
	mock_platform = _ctx_mock(mock_platform_cls)
	mock_platform.view_geek.return_value = {
		"code": 200,
		"data": {
			"geekDetailInfo": {
				"geekBaseInfo": {
					"name": "张三",
					"gender": 1,
					"ageDesc": "28岁",
					"degreeCategory": "本科",
					"workYearDesc": "5年",
					"activeTimeDesc": "今日活跃",
				},
				"showExpectPosition": {
					"positionName": "后端工程师",
					"salaryDesc": "30-40K",
					"locationName": "上海",
				},
			},
		},
	}
	result = _invoke("hr", "resume", "geek-1", "--job-id", "job-1", "--security-id", "sec-1")
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["data"]["basic"]["name"] == "张三"
	assert parsed["data"]["expectation"]["position"] == "后端工程师"
	assert parsed["hints"]["next_actions"][0] == "boss hr applications — 返回候选人列表"


@patch("boss_agent_cli.commands.recruiter.resume.get_recruiter_platform_instance")
@patch("boss_agent_cli.commands.recruiter.resume.AuthManager")
def test_recruiter_resume_parse_reports_error_when_platform_rejects(mock_auth_cls, mock_platform_cls):
	mock_platform = _ctx_mock(mock_platform_cls)
	mock_platform.view_geek.return_value = {"code": 37, "message": "stoken expired"}
	mock_platform.parse_error.return_value = ("TOKEN_REFRESH_FAILED", "stoken expired")
	result = _invoke("hr", "resume", "geek-1", "--job-id", "job-1", "--security-id", "sec-1")
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	_assert_error_contract(
		parsed,
		code="TOKEN_REFRESH_FAILED",
		message="stoken expired",
		recoverable=True,
		recovery_action="boss login",
	)


@patch("boss_agent_cli.commands.recruiter.reply.get_recruiter_platform_instance")
@patch("boss_agent_cli.commands.recruiter.reply.AuthManager")
def test_recruiter_reply_reports_error_when_platform_rejects(mock_auth_cls, mock_platform_cls):
	mock_platform = _ctx_mock(mock_platform_cls)
	mock_platform.send_message.return_value = {"code": 9, "message": "too fast"}
	mock_platform.is_success.return_value = False
	mock_platform.parse_error.return_value = ("RATE_LIMITED", "too fast")
	result = _invoke("hr", "reply", "123", "你好")
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["ok"] is False
	assert parsed["error"]["code"] == "RATE_LIMITED"
	assert parsed["error"]["message"] == "too fast"


@patch("boss_agent_cli.commands.recruiter.request_resume.get_recruiter_platform_instance")
@patch("boss_agent_cli.commands.recruiter.request_resume.AuthManager")
def test_recruiter_request_resume_reports_error_when_platform_rejects(mock_auth_cls, mock_platform_cls):
	mock_platform = _ctx_mock(mock_platform_cls)
	mock_platform.exchange_request.return_value = {"code": 36, "message": "account risk"}
	mock_platform.is_success.return_value = False
	mock_platform.parse_error.return_value = ("ACCOUNT_RISK", "account risk")
	result = _invoke("hr", "request-resume", "123", "--job-id", "88")
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["ok"] is False
	_assert_error_contract(
		parsed,
		code="ACCOUNT_RISK",
		message="account risk",
		recoverable=True,
		recovery_action="启动 CDP Chrome 重试，或联系客服",
	)


def test_parse_resume_accepts_data_envelope():
	result = parse_resume(
		{
			"code": 200,
			"data": {
				"geekDetailInfo": {
					"geekBaseInfo": {"name": "李四", "gender": 1},
					"showExpectPosition": {"positionName": "Python 工程师"},
				},
			},
		}
	)
	assert result["basic"]["name"] == "李四"
	assert result["expectation"]["position"] == "Python 工程师"


def test_parse_resume_accepts_unwrapped_payload():
	result = parse_resume(
		{
			"geekDetailInfo": {
				"geekBaseInfo": {"name": "王五", "gender": 0},
				"showExpectPosition": {"positionName": "测试工程师"},
			},
		}
	)
	assert result["basic"]["name"] == "王五"
	assert result["expectation"]["position"] == "测试工程师"


def test_hr_group_rejects_unsupported_zhilian_platform():
	runner = CliRunner()
	result = runner.invoke(cli, ["--platform", "zhilian", "--json", "hr", "candidates", "python"])
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["ok"] is False
	assert parsed["error"]["code"] == "INVALID_PARAM"
	assert "暂不支持平台" in parsed["error"]["message"]
	assert "boss --platform zhipin hr ..." == parsed["error"]["recovery_action"]
