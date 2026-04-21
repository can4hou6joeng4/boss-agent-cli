"""Recruiter command group tests."""
import json
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from boss_agent_cli.main import cli


def _ctx_mock(mock_cls):
	instance = mock_cls.return_value
	instance.__enter__ = lambda self: self
	instance.__exit__ = lambda self, *a: None
	return instance


def test_recruiter_group_is_registered():
	runner = CliRunner()
	result = runner.invoke(cli, ["--help"])
	assert result.exit_code == 0
	assert "hr" in result.output
	assert "\n  recruiter" not in result.output


def test_recruiter_role_flag_is_accepted():
	runner = CliRunner()
	result = runner.invoke(cli, ["--role", "recruiter", "--help"])
	assert result.exit_code == 0


@patch("boss_agent_cli.commands.recruiter.applications.get_recruiter_platform_instance")
@patch("boss_agent_cli.commands.recruiter.applications.AuthManager")
def test_applications_command_lists_friends(mock_auth_cls, mock_get_platform):
	mock_platform = _ctx_mock(mock_get_platform)
	mock_platform.friend_list.return_value = {"code": 0, "zpData": {"list": []}}

	runner = CliRunner()
	result = runner.invoke(cli, ["--role", "recruiter", "hr", "applications"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["ok"] is True
	assert parsed["command"] == "recruiter-applications"


@patch("boss_agent_cli.commands.recruiter.candidates.get_recruiter_platform_instance")
@patch("boss_agent_cli.commands.recruiter.candidates.AuthManager")
def test_candidates_command_searches(mock_auth_cls, mock_get_platform):
	mock_platform = _ctx_mock(mock_get_platform)
	mock_platform.search_geeks.return_value = {"code": 0, "zpData": {"list": []}}

	runner = CliRunner()
	result = runner.invoke(cli, ["--role", "recruiter", "hr", "candidates", "Python"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["ok"] is True
	assert parsed["command"] == "recruiter-candidates"


@patch("boss_agent_cli.commands.recruiter.chat.get_recruiter_platform_instance")
@patch("boss_agent_cli.commands.recruiter.chat.AuthManager")
def test_chat_command_lists_friends(mock_auth_cls, mock_get_platform):
	mock_platform = _ctx_mock(mock_get_platform)
	mock_platform.friend_list.return_value = {"code": 0, "zpData": {"list": []}}

	runner = CliRunner()
	result = runner.invoke(cli, ["--role", "recruiter", "hr", "chat"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["ok"] is True
	assert parsed["command"] == "recruiter-chat"


def test_role_default_is_candidate():
	runner = CliRunner()
	result = runner.invoke(cli, ["schema"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["data"].get("current_role", "candidate") == "candidate"


def test_recruiter_in_schema():
	runner = CliRunner()
	result = runner.invoke(cli, ["schema"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert "hr" in parsed["data"]["commands"]
	assert "recruiter" not in parsed["data"]["commands"]


def test_recruiter_role_in_schema_output():
	runner = CliRunner()
	result = runner.invoke(cli, ["--role", "recruiter", "schema"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["data"]["current_role"] == "recruiter"
