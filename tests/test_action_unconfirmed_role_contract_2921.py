"""Issue #2921：ACTION_UNCONFIRMED 的通用恢复指引必须按角色生成。"""

from __future__ import annotations

import io
import json
from contextlib import redirect_stdout
from types import SimpleNamespace
from typing import Any

import pytest

from boss_agent_cli import display
from boss_agent_cli.schema.error_codes import ERROR_CODES
from boss_agent_cli.wizard.renderer import ERROR_LABELS


def _ctx(tmp_path: Any, role: str) -> SimpleNamespace:
	return SimpleNamespace(
		obj={"platform": "zhipin", "role": role, "data_dir": tmp_path, "json_output": True}
	)


def test_platform_contract_uses_candidate_chat_for_candidate(tmp_path: Any) -> None:
	recoverable, action, hints = display.platform_error_contract(
		"ACTION_UNCONFIRMED", ctx=_ctx(tmp_path, "candidate")
	)

	assert recoverable is False
	assert "hr " not in action
	assert " chat" in action
	assert hints is not None
	[hint] = hints["next_actions"]
	assert "hr " not in hint
	assert hint.endswith(" chat")


def test_platform_contract_keeps_recruiter_chatmsg(tmp_path: Any) -> None:
	recoverable, action, hints = display.platform_error_contract(
		"ACTION_UNCONFIRMED", ctx=_ctx(tmp_path, "recruiter")
	)

	assert recoverable is False
	assert "hr chatmsg" in action
	assert hints is not None
	assert any("hr chatmsg" in hint for hint in hints["next_actions"])


def test_shared_error_output_does_not_leak_recruiter_command(tmp_path: Any) -> None:
	stdout = io.StringIO()
	with redirect_stdout(stdout), pytest.raises(SystemExit):
		display.handle_error_output(
			_ctx(tmp_path, "candidate"),
			"wizard",
			code="ACTION_UNCONFIRMED",
			message="写请求在途，结果尚未确认",
			recoverable=False,
			recovery_action=None,
			json_mode=True,
		)

	envelope = json.loads(stdout.getvalue())
	assert envelope["error"]["code"] == "ACTION_UNCONFIRMED"
	assert "hr " not in envelope["error"]["recovery_action"]
	assert envelope["hints"] is not None
	assert all("hr " not in hint for hint in envelope["hints"]["next_actions"])


def test_registry_and_tty_labels_are_role_neutral() -> None:
	assert "hr " not in ERROR_CODES["ACTION_UNCONFIRMED"]["recovery_action"]
	assert "hr " not in ERROR_LABELS["ACTION_UNCONFIRMED"]
