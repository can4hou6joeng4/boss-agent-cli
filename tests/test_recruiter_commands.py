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
	assert "recruiter" in result.output


def test_recruiter_role_flag_is_accepted():
	runner = CliRunner()
	result = runner.invoke(cli, ["--role", "recruiter", "--help"])
	assert result.exit_code == 0


@patch("boss_agent_cli.commands.recruiter.applications.get_recruiter_platform_instance")
@patch("boss_agent_cli.commands.recruiter.applications.AuthManager")
def test_applications_command_lists_applications(mock_auth_cls, mock_get_platform):
	mock_platform = _ctx_mock(mock_get_platform)
	mock_platform.list_applications.return_value = {"code": 0, "zpData": {"list": []}}

	runner = CliRunner()
	result = runner.invoke(cli, ["--role", "recruiter", "recruiter", "applications"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["ok"] is True
	assert parsed["command"] == "recruiter-applications"


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
	assert "recruiter" in parsed["data"]["commands"]


def test_recruiter_role_in_schema_output():
	runner = CliRunner()
	result = runner.invoke(cli, ["--role", "recruiter", "schema"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["data"]["current_role"] == "recruiter"
