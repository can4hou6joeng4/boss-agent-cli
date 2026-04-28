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
	return instance


def _invoke(*args):
	runner = CliRunner()
	return runner.invoke(cli, ["--role", "recruiter", *args])


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
