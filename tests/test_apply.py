import json
import shlex
from unittest.mock import patch

import pytest

from click.testing import CliRunner

from boss_agent_cli.main import cli
from boss_agent_cli.schema.error_codes import ERROR_CODES


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
	mock_cache.record_apply.assert_called_once_with("sec_001", "job_001", account="")


def test_apply_rejects_blank_identifiers_with_search_hint(tmp_path):
	"""空编号不会发出投递请求；恢复指引指回搜索结果取得完整参数。"""
	result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "--json", "apply", "", "job_001"])

	assert result.exit_code == 1, result.output
	parsed = json.loads(result.output)
	assert parsed["error"]["code"] == "INVALID_PARAM"
	actions = parsed["hints"]["next_actions"]
	assert [shlex.split(action) for action in actions] == [
		["boss", "--data-dir", str(tmp_path.resolve()), "search", "<关键词>"],
	]
	assert actions[0] in parsed["error"]["recovery_action"]


@patch("boss_agent_cli.commands.apply.get_platform_instance")
@patch("boss_agent_cli.commands.apply.AuthManager")
@patch("boss_agent_cli.commands.apply.CacheStore")
def test_apply_duplicate_is_blocked(mock_cache_cls, mock_auth_cls, mock_get_platform, tmp_path):
	mock_cache = _ctx_mock(mock_cache_cls)
	mock_cache.is_applied.return_value = True

	runner = CliRunner()
	result = runner.invoke(cli, ["--data-dir", str(tmp_path), "apply", "sec_001", "job_001"])
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["error"]["code"] == "ALREADY_APPLIED"
	# 已投递不是死胡同：恢复指引要给出同一目录的查看投递记录 / 跟进会话路径。
	actions = parsed["hints"]["next_actions"]
	assert [shlex.split(action) for action in actions] == [
		["boss", "--data-dir", str(tmp_path.resolve()), "me", "--section", "deliver"],
		["boss", "--data-dir", str(tmp_path.resolve()), "chat"],
	]
	assert all(action in parsed["error"]["recovery_action"] for action in actions)
	mock_get_platform.assert_not_called()


@patch("boss_agent_cli.commands.apply.get_platform_instance")
@patch("boss_agent_cli.commands.apply.AuthManager")
@patch("boss_agent_cli.commands.apply.CacheStore")
def test_apply_failure_does_not_record_local_state(mock_cache_cls, mock_auth_cls, mock_get_platform):
	mock_cache = _ctx_mock(mock_cache_cls)
	mock_cache.is_applied.return_value = False
	mock_platform = _ctx_mock(mock_get_platform)
	mock_platform.apply.return_value = {"code": 1, "message": "失败"}
	mock_platform.is_success.return_value = False
	mock_platform.parse_error.return_value = ("NETWORK_ERROR", "失败")

	runner = CliRunner()
	result = runner.invoke(cli, ["apply", "sec_001", "job_001"])
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["error"]["code"] == "NETWORK_ERROR"
	mock_cache.record_apply.assert_not_called()


@patch("boss_agent_cli.commands.apply.get_platform_instance")
@patch("boss_agent_cli.commands.apply.AuthManager")
@patch("boss_agent_cli.commands.apply.CacheStore")
def test_apply_cache_write_failure_is_not_retryable(mock_cache_cls, mock_auth_cls, mock_get_platform):
	"""平台已接受投递后本地记账失败：不得提示重试，否则会重复提交。"""
	mock_cache = _ctx_mock(mock_cache_cls)
	mock_cache.is_applied.return_value = False
	mock_cache.record_apply.side_effect = OSError("disk full")
	mock_platform = _ctx_mock(mock_get_platform)
	mock_platform.apply.return_value = {"code": 0, "zpData": {}}
	mock_platform.is_success.return_value = True

	runner = CliRunner()
	result = runner.invoke(cli, ["--json", "apply", "sec_001", "job_001"])
	assert result.exit_code == 1
	parsed = json.loads(result.output)
	assert parsed["ok"] is False
	assert parsed["error"]["code"] == "LOCAL_STATE_WRITE_FAILED"
	assert parsed["error"]["recoverable"] is False
	assert parsed["error"]["recovery_action"] == ERROR_CODES["LOCAL_STATE_WRITE_FAILED"]["recovery_action"]
	assert parsed["error"]["details"]["applied"] is True
	assert parsed["error"]["details"]["recorded"] is False


@pytest.mark.parametrize("error_code", ["ACTION_UNCONFIRMED", "UNKNOWN"])
@patch("boss_agent_cli.commands.apply.get_platform_instance")
@patch("boss_agent_cli.commands.apply.AuthManager")
@patch("boss_agent_cli.commands.apply.CacheStore")
def test_apply_unknown_outcome_never_suggests_resending(
	mock_cache_cls, mock_auth_cls, mock_get_platform, tmp_path, error_code,
):
	"""未确认投递不记成功、不重复发送，核对命令保留原数据目录。"""
	data_dir = tmp_path / "candidate profile"
	mock_cache = _ctx_mock(mock_cache_cls)
	mock_cache.is_applied.return_value = False
	mock_platform = _ctx_mock(mock_get_platform)
	mock_platform.apply.return_value = {
		"code": -1,
		"message": "response is not JSON",
		"__cli_error_details__": {"outcome": "unknown", "replay_safe": False, "reason": "unparsed_response"},
	}
	mock_platform.is_success.return_value = False
	mock_platform.parse_error.return_value = (error_code, "response is not JSON")

	result = CliRunner().invoke(cli, ["--data-dir", str(data_dir), "--json", "apply", "synthetic-sid", "synthetic-job"])

	assert result.exit_code == 1, result.output
	body = json.loads(result.output)
	assert body["ok"] is False
	error = body["error"]
	assert error["code"] == error_code
	assert error["recoverable"] is False
	assert error["details"]["replay_safe"] is False
	assert error["details"]["reason"] == "unparsed_response"
	assert "不要自动重试" in error["recovery_action"] or "不要直接重试" in error["recovery_action"]
	if error_code == "ACTION_UNCONFIRMED":
		assert error["details"]["outcome_unknown"] is True
		actions = body["hints"]["next_actions"]
		assert [shlex.split(action) for action in actions] == [
			["boss", "--data-dir", str(data_dir.resolve()), "me", "--section", "deliver"],
			["boss", "--data-dir", str(data_dir.resolve()), "chat"],
		]
		assert actions[0] in error["recovery_action"]
		assert "hr chatmsg" not in error["recovery_action"]
	mock_platform.apply.assert_called_once_with("synthetic-sid", "synthetic-job", lid="")
	mock_cache.record_apply.assert_not_called()


def test_apply_is_exposed_in_schema():
	runner = CliRunner()
	result = runner.invoke(cli, ["schema"])
	assert result.exit_code == 0
	parsed = json.loads(result.output)
	assert "apply" in parsed["data"]["commands"]
