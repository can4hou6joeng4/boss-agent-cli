"""request-resume 的停止开关拦截与未知结果保护（Issue #1840）。"""

import json
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from boss_agent_cli.api.request_budget import RequestBudgetExceeded
from boss_agent_cli.main import cli

MODULE = "boss_agent_cli.commands.recruiter.request_resume"


def _invoke(tmp_path: Path, *args: str):
	return CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "--json", "hr", "request-resume", *args])


def _payload(result) -> dict:
	return json.loads(result.output)


def _open_stop(tmp_path: Path) -> None:
	stop_dir = tmp_path / "automation"
	stop_dir.mkdir(parents=True)
	(stop_dir / "stop.json").write_text(json.dumps({"open": True, "reason": "manual-stop"}), encoding="utf-8")


def test_request_resume_blocked_by_stop_latch(tmp_path: Path) -> None:
	_open_stop(tmp_path)
	with patch(MODULE + ".AuthManager"), patch(MODULE + ".get_recruiter_platform_instance") as factory:
		result = _invoke(tmp_path, "123")

	assert result.exit_code == 1, result.output
	payload = _payload(result)
	error = payload["error"]
	assert error["code"] == "CIRCUIT_BREAKER_OPEN"
	assert "manual-stop" in error["message"]
	assert error["details"]["requested"] is False
	assert any("agent run --resume" in item for item in payload["hints"]["next_actions"])
	assert any("停止开关" in item for item in payload["hints"]["operator_actions"])
	factory.assert_not_called()


def test_request_resume_unknown_exception_is_not_retryable(tmp_path: Path) -> None:
	with patch(MODULE + ".AuthManager"), patch(MODULE + ".get_recruiter_platform_instance") as factory:
		platform = factory.return_value.__enter__.return_value
		platform.exchange_request_by_friend.side_effect = ValueError("private-token-in-response")
		result = _invoke(tmp_path, "123")

	assert result.exit_code == 1, result.output
	payload = _payload(result)
	error = payload["error"]
	assert error["code"] == "ACTION_UNCONFIRMED"
	assert error["details"]["requested"] is None
	assert error["recoverable"] is False
	assert "hr chatmsg 123" in error["recovery_action"]
	assert "private-token" not in result.output
	next_actions = payload["hints"]["next_actions"]
	assert len(next_actions) == 1
	assert "hr chatmsg 123" in next_actions[0]
	platform.exchange_request_by_friend.assert_called_once_with(123, exchange_type=4)


def test_request_resume_budget_stop_is_not_unknown(tmp_path: Path) -> None:
	"""预算在调度前停止：请求没有发出，不能报成「结果未知」。"""
	with patch(MODULE + ".AuthManager"), patch(MODULE + ".get_recruiter_platform_instance") as factory:
		platform = factory.return_value.__enter__.return_value
		platform.exchange_request_by_friend.side_effect = RequestBudgetExceeded("预算耗尽")
		result = _invoke(tmp_path, "123")

	assert result.exit_code == 1, result.output
	error = _payload(result)["error"]
	assert error["code"] == "REQUEST_BUDGET_EXHAUSTED"
	assert error["code"] != "ACTION_UNCONFIRMED"


def test_request_resume_success_reports_requested(tmp_path: Path) -> None:
	with patch(MODULE + ".AuthManager"), patch(MODULE + ".get_recruiter_platform_instance") as factory:
		platform = factory.return_value.__enter__.return_value
		platform.exchange_request_by_friend.return_value = {"code": 0}
		platform.is_success.return_value = True
		result = _invoke(tmp_path, "123")

	assert result.exit_code == 0, result.output
	payload = _payload(result)
	assert payload["data"]["requested"] is True
	assert payload["data"]["friend_id"] == 123


def test_request_resume_platform_action_unconfirmed_response_is_not_retryable(tmp_path: Path) -> None:
	"""平台响应直接给出 ACTION_UNCONFIRMED 时保持不可重试契约。"""
	with patch(MODULE + ".AuthManager"), patch(MODULE + ".get_recruiter_platform_instance") as factory:
		platform = factory.return_value.__enter__.return_value
		platform.exchange_request_by_friend.return_value = {"code": 0}
		platform.is_success.return_value = False
		platform.parse_error.return_value = ("ACTION_UNCONFIRMED", "页面动作未确认")
		result = _invoke(tmp_path, "123")

	assert result.exit_code == 1, result.output
	error = _payload(result)["error"]
	assert error["code"] == "ACTION_UNCONFIRMED"
	assert error["recoverable"] is False
