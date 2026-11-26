"""Issue #2905：hr resume 参数齐全时不能静默忽略 --friend-id。"""

import json
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from boss_agent_cli.main import cli

RESUME_MODULE = "boss_agent_cli.commands.recruiter.resume"


def _platform(mock_cls):
	instance = mock_cls.return_value
	instance.__enter__ = lambda self: self
	instance.__exit__ = lambda self, *args: None
	instance.is_success.return_value = True
	instance.unwrap_data.side_effect = lambda response: response.get("zpData", response.get("data"))
	instance.view_geek.return_value = {"code": 200, "data": {"name": "候选人A"}}
	instance.exchange_request_by_friend.return_value = {"code": 200, "zpData": {"requested": True}}
	return instance


def _invoke(tmp_path: Path, *extra: str):
	with (
		patch(RESUME_MODULE + ".AuthManager"),
		patch(RESUME_MODULE + ".get_recruiter_platform_instance") as mock_platform,
	):
		platform = _platform(mock_platform)
		result = CliRunner().invoke(
			cli,
			[
				"--data-dir", str(tmp_path), "--json", "hr", "resume",
				"geek1", "--job-id", "job1", "--security-id", "sid", "--raw", *extra,
			],
		)
	return result, platform


def test_view_resume_with_friend_id_points_at_exchange(tmp_path: Path):
	result, platform = _invoke(tmp_path, "--friend-id", "123")

	assert result.exit_code == 0, result.output
	payload = json.loads(result.output)
	assert payload["ok"] is True
	platform.view_geek.assert_called_once()
	platform.exchange_request_by_friend.assert_not_called()
	assert "hr resume --friend-id 123 --exchange" in payload["hints"]["next_actions"][0]
	assert "未用于交换" in payload["hints"]["operator_actions"][0]


def test_view_resume_without_friend_id_keeps_existing_hints(tmp_path: Path):
	result, platform = _invoke(tmp_path)

	assert result.exit_code == 0, result.output
	payload = json.loads(result.output)
	platform.view_geek.assert_called_once()
	assert len(payload["hints"]["next_actions"]) == 1
	assert "hr applications" in payload["hints"]["next_actions"][0]
	assert "operator_actions" not in payload["hints"]


def test_exchange_with_friend_id_keeps_existing_contract(tmp_path: Path):
	result, platform = _invoke(tmp_path, "--friend-id", "123", "--exchange")

	assert result.exit_code == 0, result.output
	payload = json.loads(result.output)
	platform.exchange_request_by_friend.assert_called_once()
	platform.view_geek.assert_not_called()
	assert "operator_actions" not in payload["hints"]
	assert payload["data"]["message"] == "联系方式交换请求已发送"
