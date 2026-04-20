import json
from unittest.mock import patch

from click.testing import CliRunner

from boss_agent_cli.main import cli


def _ctx_mock(mock_cls):
	instance = mock_cls.return_value
	instance.__enter__ = lambda self: self
	instance.__exit__ = lambda self, *a: None
	return instance


@patch("boss_agent_cli.commands.apply.get_platform_instance")
@patch("boss_agent_cli.commands.apply.AuthManager")
@patch("boss_agent_cli.commands.apply.CacheStore")
def test_apply_success(mock_cache_cls, mock_auth_cls, mock_get_platform):
	mock_cache = _ctx_mock(mock_cache_cls)
	mock_cache.is_applied.return_value = False
	mock_platform = _ctx_mock(mock_get_platform)
	mock_platform.apply.return_value = {"code": 0, "zpData": {}}

	runner = CliRunner()
	result = runner.invoke(cli, ["--json", "apply", "sec_001", "job_001"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert parsed["ok"] is True
	assert parsed["data"]["security_id"] == "sec_001"
	assert parsed["data"]["job_id"] == "job_001"
	assert parsed["data"]["mode"] == "immediate_chat_apply"
	mock_cache.record_apply.assert_called_once_with("sec_001", "job_001")


@patch("boss_agent_cli.commands.apply.get_platform_instance")
@patch("boss_agent_cli.commands.apply.AuthManager")
@patch("boss_agent_cli.commands.apply.CacheStore")
def test_apply_duplicate_is_blocked(mock_cache_cls, mock_auth_cls, mock_get_platform):
	mock_cache = _ctx_mock(mock_cache_cls)
	mock_cache.is_applied.return_value = True

	runner = CliRunner()
	result = runner.invoke(cli, ["apply", "sec_001", "job_001"])
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["error"]["code"] == "ALREADY_APPLIED"


@patch("boss_agent_cli.commands.apply.get_platform_instance")
@patch("boss_agent_cli.commands.apply.AuthManager")
@patch("boss_agent_cli.commands.apply.CacheStore")
def test_apply_failure_does_not_record_local_state(mock_cache_cls, mock_auth_cls, mock_get_platform):
	mock_cache = _ctx_mock(mock_cache_cls)
	mock_cache.is_applied.return_value = False
	mock_platform = _ctx_mock(mock_get_platform)
	mock_platform.apply.return_value = {"code": 1, "message": "失败"}

	runner = CliRunner()
	result = runner.invoke(cli, ["apply", "sec_001", "job_001"])
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["error"]["code"] == "NETWORK_ERROR"
	mock_cache.record_apply.assert_not_called()


def test_apply_is_exposed_in_schema():
	runner = CliRunner()
	result = runner.invoke(cli, ["schema"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert "apply" in parsed["data"]["commands"]
