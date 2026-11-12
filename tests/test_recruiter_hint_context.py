"""招聘者命令的后继与恢复提示在产生它的数据目录中直接重放。"""

import json
import shlex
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from boss_agent_cli.api.recruiter_resume import ResumeValidationError
from boss_agent_cli.main import cli


@pytest.fixture
def hr_dir(tmp_path, monkeypatch):
	"""数据目录含空格、单引号与 Rich 标签；默认目录指向一次性 HOME。"""
	monkeypatch.setenv("HOME", str(tmp_path / "home"))
	data_dir = tmp_path / "jobs [bold] team's"
	data_dir.mkdir()
	return data_dir


def _run(data_dir, *args):
	return CliRunner().invoke(cli, ["--data-dir", str(data_dir), "--json", *args])


def _prefix(action: str, data_dir) -> list[str]:
	parts = shlex.split(action)
	assert parts[:3] == ["boss", "--data-dir", str(data_dir.resolve())], action
	return parts


def _platform(mock_cls):
	instance = mock_cls.return_value
	instance.__enter__ = lambda self: self
	instance.__exit__ = lambda self, *a: None
	instance.unwrap_data.side_effect = lambda response: response.get("zpData", response.get("data"))
	instance.is_success.side_effect = lambda response: response.get("code", 0) in (0, 200)
	return instance


def test_hr_whoami_hints_keep_directory(hr_dir):
	with (
		patch("boss_agent_cli.commands.recruiter.whoami.AuthManager"),
		patch("boss_agent_cli.commands.recruiter.whoami.get_platform_instance") as factory,
	):
		platform = _platform(factory)
		platform.user_info.return_value = {"code": 0, "zpData": {"identity": 1, "doubleIdentity": False, "recruit": False}}
		result = _run(hr_dir, "hr", "whoami")

	assert result.exit_code == 0, result.output
	actions = json.loads(result.stdout)["hints"]["next_actions"]
	assert [_prefix(action, hr_dir)[3:] for action in actions] == [["hr", "chat"], ["hr", "jobs", "list"]]


def test_hr_jobs_blank_id_recovery_replays_in_original_directory(hr_dir):
	with (
		patch("boss_agent_cli.commands.recruiter.jobs.AuthManager"),
		patch("boss_agent_cli.commands.recruiter.jobs.get_recruiter_platform_instance") as factory,
	):
		platform = _platform(factory)
		platform.list_jobs.return_value = {"code": 0, "zpData": {"data": []}}
		result = _run(hr_dir, "hr", "jobs", "offline", " ")

	assert result.exit_code == 1, result.output
	parsed = json.loads(result.stdout)
	assert parsed["error"]["code"] == "INVALID_PARAM"
	action = parsed["hints"]["next_actions"][0]
	assert _prefix(action, hr_dir)[3:] == ["hr", "jobs", "list"]
	assert action in parsed["error"]["recovery_action"]
	# 恢复命令只依赖原目录中的登录态，直接重放即可取到职位编号。
	with (
		patch("boss_agent_cli.commands.recruiter.jobs.AuthManager"),
		patch("boss_agent_cli.commands.recruiter.jobs.get_recruiter_platform_instance") as replay_factory,
	):
		_platform(replay_factory).list_jobs.return_value = {"code": 0, "zpData": {"data": []}}
		replayed = CliRunner().invoke(cli, ["--json", *shlex.split(action)[1:]])
	assert replayed.exit_code == 0, replayed.output


def test_hr_applications_page_hint_keeps_directory(hr_dir):
	with (
		patch("boss_agent_cli.commands.recruiter.applications.AuthManager"),
		patch("boss_agent_cli.commands.recruiter.applications.get_recruiter_platform_instance") as factory,
	):
		platform = _platform(factory)
		platform.friend_list.return_value = {"code": 200, "data": {"friendList": [{"name": "候选人"}], "hasMore": True}}
		result = _run(hr_dir, "hr", "applications", "--page", "2", "--job-id", "jobx", "--label-id", "2")

	assert result.exit_code == 0, result.output
	actions = json.loads(result.stdout)["hints"]["next_actions"]
	assert _prefix(actions[-1], hr_dir)[3:] == [
		"hr", "applications", "--page", "3", "--job-id", "jobx", "--label-id", "2",
	]


def test_hr_chat_hints_keep_directory(hr_dir):
	with (
		patch("boss_agent_cli.commands.recruiter.chat.AuthManager"),
		patch("boss_agent_cli.commands.recruiter.chat.get_recruiter_platform_instance") as factory,
	):
		_platform(factory).friend_list.return_value = {"code": 200, "data": {"friendList": [], "hasMore": False}}
		result = _run(hr_dir, "hr", "chat")

	assert result.exit_code == 0, result.output
	actions = json.loads(result.stdout)["hints"]["next_actions"]
	assert _prefix(actions[0], hr_dir)[3:] == ["hr", "resume", "<geek_id>", "--job-id", "<id>", "--security-id", "<id>"]
	assert _prefix(actions[1], hr_dir)[3:] == ["hr", "chatmsg", "<friend_id>"]


def test_hr_last_messages_empty_result_keeps_directory_in_prose(hr_dir):
	with (
		patch("boss_agent_cli.commands.recruiter.chat.AuthManager"),
		patch("boss_agent_cli.commands.recruiter.chat.get_recruiter_platform_instance") as factory,
	):
		_platform(factory).friend_list.return_value = {"code": 200, "data": {"friendList": [], "hasMore": False}}
		result = _run(hr_dir, "hr", "last-messages")

	assert result.exit_code == 0, result.output
	hints = json.loads(result.stdout)["hints"]
	assert _prefix(hints["next_actions"][0], hr_dir)[3:] == ["hr", "chat"]
	prose = hints["operator_actions"][0]
	chat_hint = prose.split("先用 ", 1)[1].split(" 查看沟通列表", 1)[0]
	candidates_hint = prose.split("或用 ", 1)[1].split(" 主动搜索候选人", 1)[0]
	assert _prefix(chat_hint, hr_dir)[3:] == ["hr", "chat"]
	assert _prefix(candidates_hint, hr_dir)[3:] == ["hr", "candidates", "<关键词>"]


def test_hr_greet_blank_message_recovery_keeps_directory(hr_dir):
	with (
		patch("boss_agent_cli.commands.recruiter.recommendations.AuthManager") as auth,
		patch("boss_agent_cli.commands.recruiter.recommendations.get_recruiter_platform_instance") as factory,
	):
		result = _run(
			hr_dir, "hr", "greet",
			"--geek-id", "geek", "--job-id", "job", "--expect-id", "expect", "--lid", "lid",
			"--security-id", "security", "--message", "   ",
		)

	assert result.exit_code == 1, result.output
	parsed = json.loads(result.stdout)
	assert parsed["error"]["code"] == "INVALID_PARAM"
	action = parsed["hints"]["next_actions"][0]
	assert _prefix(action, hr_dir)[3:] == ["hr", "recommendations", "--job-id", "job"]
	assert action in parsed["error"]["recovery_action"]
	auth.assert_not_called()
	factory.assert_not_called()


def test_hr_download_validation_error_keeps_directory(hr_dir):
	module = "boss_agent_cli.commands.recruiter.download_resume"
	with (
		patch(module + ".AuthManager"),
		patch(module + ".get_recruiter_platform_instance") as factory,
	):
		platform = _platform(factory)
		platform.download_resume_by_friend.side_effect = ResumeValidationError("该消息不是附件简历请求")
		result = _run(hr_dir, "hr", "download-resume", "123", "--message-id", "456", "--output", str(hr_dir / "resume.pdf"))

	assert result.exit_code == 1, result.output
	parsed = json.loads(result.stdout)
	assert parsed["error"]["code"] == "INVALID_PARAM"
	action = parsed["hints"]["next_actions"][0]
	assert _prefix(action, hr_dir)[3:] == ["hr", "chat"]
	assert action in parsed["error"]["recovery_action"]


def test_hr_platform_not_supported_recovery_keeps_directory(hr_dir):
	with (
		patch("boss_agent_cli.main.list_platforms", return_value=["zhipin", "fixture"]),
		patch("boss_agent_cli.commands.register.list_recruiter_platforms", return_value=["zhipin-recruiter"]),
	):
		result = _run(hr_dir, "--platform", "fixture", "hr", "whoami")

	assert result.exit_code == 1, result.output
	parsed = json.loads(result.stdout)
	assert parsed["error"]["code"] == "PLATFORM_NOT_SUPPORTED"
	parts = shlex.split(parsed["error"]["recovery_action"])
	assert parts[:5] == ["boss", "--platform", "zhipin", "--data-dir", str(hr_dir.resolve())]
	assert parts[5:] == ["hr", "..."]
