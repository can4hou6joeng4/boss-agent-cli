"""招聘者侧读取类命令的终端渲染（Issue #2774）。"""

import io
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner, Result
from rich.console import Console

from boss_agent_cli import display
from boss_agent_cli.main import cli


def _tty(monkeypatch: pytest.MonkeyPatch, *, width: int = 120) -> io.StringIO:
	stream = io.StringIO()
	monkeypatch.setattr(display, "console", Console(file=stream, width=width, emoji=False))
	monkeypatch.setattr(display, "is_json_mode", display.json_forced)
	return stream


def _invoke(data_dir: Path, *args: str) -> Result:
	return CliRunner().invoke(cli, ["--role", "recruiter", "--data-dir", str(data_dir), *args])


def _ctx_mock(mock_cls):
	instance = mock_cls.return_value
	instance.__enter__ = lambda self: self
	instance.__exit__ = lambda self, *a: None
	instance.unwrap_data.side_effect = lambda response: response.get("zpData") if "zpData" in response else response.get("data")
	instance.is_success.side_effect = lambda response: response.get("code", 0) in (0, 200)
	return instance


@patch("boss_agent_cli.commands.recruiter.whoami.get_platform_instance")
@patch("boss_agent_cli.commands.recruiter.whoami.AuthManager")
def test_hr_whoami_renders_identity_panel(
	mock_auth_cls, mock_platform_cls, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
	stream = _tty(monkeypatch)
	platform = _ctx_mock(mock_platform_cls)
	platform.user_info.return_value = {
		"code": 200,
		"data": {"identity": 1, "doubleIdentity": False, "recruit": True},
	}
	result = _invoke(tmp_path, "hr", "whoami")
	assert result.exit_code == 0, result.output or repr(result.exception)
	assert not result.stdout
	out = stream.getvalue()
	assert "hr whoami" in out
	assert "side: recruiter" in out
	assert "ready: True" in out
	assert "hr chat" in out


@patch("boss_agent_cli.commands.recruiter.chat.get_recruiter_platform_instance")
@patch("boss_agent_cli.commands.recruiter.chat.AuthManager")
def test_hr_chat_renders_friend_rows(
	mock_auth_cls, mock_platform_cls, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
	stream = _tty(monkeypatch)
	platform = _ctx_mock(mock_platform_cls)
	platform.friend_list.return_value = {
		"code": 200,
		"data": {
			"friendList": [
				{
					"name": "候选人甲",
					"brandName": "示例公司",
					"jobName": "后端工程师",
					"lastMsg": "您好，我对岗位很感兴趣",
					"lastTS": 1700000000000,
					"unreadMsgCount": 2,
				},
			],
			"hasMore": False,
		},
	}
	result = _invoke(tmp_path, "hr", "chat")
	assert result.exit_code == 0, result.output or repr(result.exception)
	assert not result.stdout
	out = stream.getvalue()
	for text in ("hr chat", "候选人甲", "示例公司", "后端工程师", "您好，我对岗位很感兴趣", "2"):
		assert text in out
	parsed = json.loads(_invoke(tmp_path, "--json", "hr", "chat").stdout)
	assert parsed["data"]["friendList"][0]["name"] == "候选人甲"


@patch("boss_agent_cli.commands.recruiter.applications.get_recruiter_platform_instance")
@patch("boss_agent_cli.commands.recruiter.applications.AuthManager")
def test_hr_applications_renders_friend_rows(
	mock_auth_cls, mock_platform_cls, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
	stream = _tty(monkeypatch)
	platform = _ctx_mock(mock_platform_cls)
	platform.friend_list.return_value = {
		"code": 200,
		"data": {
			"friendList": [{"name": "候选人乙", "brandName": "示例公司", "jobName": "测试工程师"}],
			"hasMore": False,
		},
	}
	result = _invoke(tmp_path, "hr", "applications")
	assert result.exit_code == 0, result.output or repr(result.exception)
	assert not result.stdout
	out = stream.getvalue()
	for text in ("hr applications", "候选人乙", "测试工程师", "hr chat"):
		assert text in out


@patch("boss_agent_cli.commands.recruiter.candidates.get_recruiter_platform_instance")
@patch("boss_agent_cli.commands.recruiter.candidates.AuthManager")
def test_hr_candidates_renders_geek_rows(
	mock_auth_cls, mock_platform_cls, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
	stream = _tty(monkeypatch)
	platform = _ctx_mock(mock_platform_cls)
	platform.search_geeks.return_value = {
		"code": 200,
		"data": {
			"geekList": [
				{
					"name": "候选人丙",
					"expectPosition": "Python 工程师",
					"city": "上海",
					"workYearDesc": "5年",
					"degreeCategory": "本科",
					"activeTimeDesc": "今日活跃",
				},
			],
			"hasMore": False,
		},
	}
	result = _invoke(tmp_path, "hr", "candidates", "python")
	assert result.exit_code == 0, result.output or repr(result.exception)
	assert not result.stdout
	out = stream.getvalue()
	for text in ("hr candidates", "候选人丙", "Python 工程师", "上海", "5年", "本科", "今日活跃"):
		assert text in out


@patch("boss_agent_cli.commands.recruiter.recommendations.get_recruiter_platform_instance")
@patch("boss_agent_cli.commands.recruiter.recommendations.AuthManager")
def test_hr_recommendations_renders_geek_rows(
	mock_auth_cls, mock_platform_cls, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
	stream = _tty(monkeypatch)
	platform = _ctx_mock(mock_platform_cls)
	platform.recommend_geeks.return_value = {
		"code": 200,
		"data": {"geekList": [{"name": "候选人丁", "expectPosition": "Go 工程师"}], "hasMore": False},
	}
	result = _invoke(tmp_path, "hr", "recommendations", "--job-id", "job-demo")
	assert result.exit_code == 0, result.output or repr(result.exception)
	assert not result.stdout
	out = stream.getvalue()
	for text in ("hr recommendations", "候选人丁", "Go 工程师", "hr greet"):
		assert text in out


@patch("boss_agent_cli.commands.recruiter.jobs.get_recruiter_platform_instance")
@patch("boss_agent_cli.commands.recruiter.jobs.AuthManager")
def test_hr_jobs_list_renders_job_rows(
	mock_auth_cls, mock_platform_cls, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
	stream = _tty(monkeypatch)
	platform = _ctx_mock(mock_platform_cls)
	platform.list_jobs.return_value = {
		"code": 200,
		"data": {
			"jobList": [
				{
					"jobName": "后端工程师",
					"jobStatusDesc": "招聘中",
					"cityName": "杭州",
					"salaryDesc": "25-40K",
					"encryptJobId": "job-enc-demo",
				},
			],
		},
	}
	result = _invoke(tmp_path, "hr", "jobs", "list")
	assert result.exit_code == 0, result.output or repr(result.exception)
	assert not result.stdout
	out = stream.getvalue()
	for text in ("hr jobs list", "后端工程师", "招聘中", "杭州", "25-40K", "job-enc-demo"):
		assert text in out


@patch("boss_agent_cli.commands.recruiter.chat.get_recruiter_platform_instance")
@patch("boss_agent_cli.commands.recruiter.chat.AuthManager")
def test_hr_chatmsg_renders_message_rows(
	mock_auth_cls, mock_platform_cls, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
	stream = _tty(monkeypatch)
	platform = _ctx_mock(mock_platform_cls)
	platform.chat_history.return_value = {
		"code": 200,
		"data": {
			"messages": [
				{
					"msgId": 9001,
					"from": {"name": "招聘者", "type": "boss"},
					"content": "您好，看了您的简历想进一步沟通",
					"time": "2026-05-12 14:30:00",
				},
				{"msgId": 9002, "from": {"type": "geek"}, "content": "好的，方便约面吗"},
			],
			"hasMore": False,
		},
	}
	result = _invoke(tmp_path, "hr", "chatmsg", "12345")
	assert result.exit_code == 0, result.output or repr(result.exception)
	assert not result.stdout
	out = stream.getvalue()
	for text in ("hr chatmsg", "boss", "geek", "您好，看了您的简历想进一步沟通", "2026-05-12 14:30:00"):
		assert text in out


@patch("boss_agent_cli.commands.recruiter.chat.get_recruiter_platform_instance")
@patch("boss_agent_cli.commands.recruiter.chat.AuthManager")
def test_hr_last_messages_renders_summary_rows(
	mock_auth_cls, mock_platform_cls, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
	stream = _tty(monkeypatch)
	platform = _ctx_mock(mock_platform_cls)
	platform.friend_list.return_value = {
		"code": 200,
		"data": {"friendList": [{"friendId": 7, "name": "候选人戊"}], "hasMore": False},
	}
	platform.last_messages.return_value = {
		"code": 200,
		"data": {
			"lastMessageList": [
				{
					"friendId": 7,
					"unreadMsgCount": 1,
					"lastMsg": "简历已发送，请查收",
					"lastMessageInfo": {"status": 1},
					"lastTime": "05-13 17:28",
				},
			],
		},
	}
	result = _invoke(tmp_path, "hr", "last-messages")
	assert result.exit_code == 0, result.output or repr(result.exception)
	assert not result.stdout
	out = stream.getvalue()
	for text in ("hr last-messages", "7", "简历已发送，请查收", "05-13 17:28", "hr reply"):
		assert text in out
	parsed = json.loads(_invoke(tmp_path, "--json", "hr", "last-messages").stdout)
	assert parsed["data"]["messages"][0]["last_msg"] == "简历已发送，请查收"


@patch("boss_agent_cli.commands.recruiter.resume.get_recruiter_platform_instance")
@patch("boss_agent_cli.commands.recruiter.resume.AuthManager")
def test_hr_resume_renders_sections(
	mock_auth_cls, mock_platform_cls, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
	stream = _tty(monkeypatch)
	platform = _ctx_mock(mock_platform_cls)
	platform.view_geek.return_value = {
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
				"geekWorkExpList": [{"positionName": "后端开发"}],
			},
		},
	}
	result = _invoke(tmp_path, "hr", "resume", "geek-1", "--job-id", "job-1", "--security-id", "sec-1")
	assert result.exit_code == 0, result.output or repr(result.exception)
	assert not result.stdout
	out = stream.getvalue()
	for text in ("basic", "张三", "expectation", "后端工程师", "work_experience", "后端开发"):
		assert text in out
