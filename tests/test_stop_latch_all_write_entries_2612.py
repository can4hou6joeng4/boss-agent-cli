"""停止闩锁的持久化熔断与所有写入口共用判定（Issue #2612）。"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from boss_agent_cli.automation.storage import AutomationStore
from boss_agent_cli.main import cli
from boss_agent_cli.output import Logger
from boss_agent_cli.wizard.actions import ActionContext, DEFAULT_ACTIONS
from boss_agent_cli.wizard.runner import WorkflowActionError

FRIEND_ID = 10001


def _open_state_breaker(data_dir: Path) -> None:
	directory = data_dir / "automation"
	directory.mkdir(parents=True, exist_ok=True)
	(directory / "state.json").write_text(
		json.dumps({"autonomy": {"circuit_breaker": {"open": True, "reason": "ACCOUNT_RISK"}}}),
		encoding="utf-8",
	)


def _invoke_cli(data_dir: Path, command: str) -> dict:
	module = {
		"location": "boss_agent_cli.commands.recruiter.location",
		"request-resume": "boss_agent_cli.commands.recruiter.request_resume",
	}[command]
	args = ["hr", "location", str(FRIEND_ID)] if command == "location" else ["hr", "request-resume", str(FRIEND_ID)]
	with patch(module + ".AuthManager") as auth, patch(module + ".get_recruiter_platform_instance") as factory:
		result = CliRunner().invoke(cli, ["--data-dir", str(data_dir), "--json", *args])
	assert result.exit_code == 1, result.output
	auth.assert_not_called()
	factory.assert_not_called()
	return json.loads(result.output)


@pytest.mark.parametrize("command", ["location", "request-resume"])
def test_persisted_breaker_blocks_recruiter_write_commands(tmp_path: Path, command: str) -> None:
	_open_state_breaker(tmp_path)
	assert not (tmp_path / "automation" / "stop.json").exists()

	payload = _invoke_cli(tmp_path, command)

	assert payload["error"]["code"] == "CIRCUIT_BREAKER_OPEN"
	assert "ACCOUNT_RISK" in payload["error"]["message"]


def test_corrupt_state_is_fail_closed_without_creating_directories(tmp_path: Path) -> None:
	directory = tmp_path / "automation"
	directory.mkdir()
	(directory / "state.json").write_text('{"autonomy": {"circuit_breaker": {"open": "yes"}}}', encoding="utf-8")

	reason = AutomationStore.stop_reason_for(tmp_path)

	assert reason is not None
	assert "state.json" in reason


def test_wizard_write_action_is_blocked_by_stop_latch(tmp_path: Path) -> None:
	AutomationStore(tmp_path).request_stop("manual-stop")
	platform = MagicMock()
	factory = MagicMock()
	factory.return_value.__enter__.return_value = platform
	context = ActionContext(
		data_dir=tmp_path,
		platform="zhipin",
		role="recruiter",
		logger=Logger("error"),
		recruiter_factory=factory,
	)

	with pytest.raises(WorkflowActionError) as caught:
		DEFAULT_ACTIONS["recruiter_request_resume"](context, {"friend_id": FRIEND_ID}, {})

	assert caught.value.code == "CIRCUIT_BREAKER_OPEN"
	assert "manual-stop" in str(caught.value)
	platform.exchange_request_by_friend.assert_not_called()


def test_wizard_write_action_is_blocked_by_persisted_breaker(tmp_path: Path) -> None:
	_open_state_breaker(tmp_path)
	platform = MagicMock()
	factory = MagicMock()
	factory.return_value.__enter__.return_value = platform
	context = ActionContext(
		data_dir=tmp_path,
		platform="zhipin",
		role="recruiter",
		logger=Logger("error"),
		recruiter_factory=factory,
	)

	with pytest.raises(WorkflowActionError) as caught:
		DEFAULT_ACTIONS["recruiter_request_resume"](context, {"friend_id": FRIEND_ID}, {})

	assert caught.value.code == "CIRCUIT_BREAKER_OPEN"
	platform.exchange_request_by_friend.assert_not_called()
