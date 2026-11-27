"""招聘者聊天方向列与熔断恢复角色（Issue #3057 / #3058）。"""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from click.testing import CliRunner
from rich.console import Console

import boss_agent_cli.display as display
from boss_agent_cli.commands.recruiter._render import message_rows, render_message_history
from boss_agent_cli.main import cli


def _messages_payload() -> dict[str, Any]:
	return {
		"messages": [
			{"content": "您好", "from": {"name": "招聘者", "type": "boss"}, "createTime": 1},
			{"content": "方便约面吗", "from": {"name": "张三", "type": "geek"}, "createTime": 2},
		],
		"hasMore": False,
	}


def test_message_rows_prefers_sender_name_over_machine_type() -> None:
	rows = message_rows(_messages_payload())

	assert [row["direction"] for row in rows] == ["招聘者", "张三"]


@pytest.mark.parametrize(("machine_type", "expected"), [("boss", "我"), ("geek", "对方")])
def test_message_rows_machine_type_falls_back_to_readable_side(
	machine_type: str, expected: str
) -> None:
	rows = message_rows(
		{"messages": [{"content": "您好", "from": {"type": machine_type}}], "hasMore": False}
	)

	assert rows[0]["direction"] == expected


def test_chatmsg_tty_shows_sender_names_not_machine_codes(monkeypatch) -> None:
	buffer = io.StringIO()
	console = Console(file=buffer, width=120, force_terminal=False, color_system=None)
	monkeypatch.setattr(display, "console", console)

	render_message_history(_messages_payload(), title="hr chatmsg", next_steps=[])

	out = buffer.getvalue()
	assert "招聘者" in out
	assert "张三" in out
	assert "boss" not in out
	assert "geek" not in out


def _open_stop(tmp_path: Path) -> None:
	stop_dir = tmp_path / "automation"
	stop_dir.mkdir(parents=True)
	(stop_dir / "stop.json").write_text(
		json.dumps({"open": True, "reason": "manual-stop"}), encoding="utf-8"
	)


def _invoke_blocked(tmp_path: Path, module: str, argv: list[str]) -> dict[str, Any]:
	with patch(module + ".AuthManager"), patch(module + ".get_recruiter_platform_instance"):
		result = CliRunner().invoke(
			cli, ["--data-dir", str(tmp_path), "--json", *argv]
		)
	assert result.exit_code == 1, result.output
	return json.loads(result.output)


@pytest.mark.parametrize(
	("module", "argv", "command"),
	[
		("boss_agent_cli.commands.recruiter.location", ["hr", "location", "10001", "--yes"], "location"),
		("boss_agent_cli.commands.recruiter.request_resume", ["hr", "request-resume", "10001"], "request-resume"),
	],
)
def test_recruiter_breaker_recovery_carries_recruiter_role(
	tmp_path: Path, module: str, argv: list[str], command: str
) -> None:
	_open_stop(tmp_path)

	payload = _invoke_blocked(tmp_path, module, argv)
	error = payload["error"]

	assert error["code"] == "CIRCUIT_BREAKER_OPEN"
	recovery = error["recovery_action"]
	assert "--role recruiter" in recovery, command
	assert "agent run --resume" in recovery, command
	next_actions = payload["hints"]["next_actions"]
	assert any("--role recruiter" in item and "agent run --resume" in item for item in next_actions)
	assert all("--role recruiter" in item for item in next_actions)
