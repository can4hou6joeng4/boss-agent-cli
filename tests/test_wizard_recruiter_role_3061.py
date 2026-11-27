"""向导熔断恢复命令必须保留招聘者角色（Issue #3061）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from boss_agent_cli.automation.storage import AutomationStore
from boss_agent_cli.output import Logger
from boss_agent_cli.wizard.actions import ActionContext, _hint_command, _write_call
from boss_agent_cli.wizard.runner import WorkflowActionError


def _context(tmp_path: Path, role: str) -> ActionContext:
	return ActionContext(data_dir=tmp_path, platform="zhipin", role=role, logger=Logger())


@pytest.mark.parametrize(("role", "expect_recruiter_role"), [("candidate", False), ("recruiter", True)])
def test_wizard_breaker_recovery_keeps_recruiter_role(
	tmp_path: Path, role: str, expect_recruiter_role: bool
) -> None:
	AutomationStore(tmp_path).request_stop("manual-stop")

	with pytest.raises(WorkflowActionError) as caught:
		_write_call(_context(tmp_path, role), object(), lambda: {}, "fallback")

	error = caught.value
	assert error.code == "CIRCUIT_BREAKER_OPEN"
	assert error.auto_retry is False
	recovery = error.recovery_action or ""
	assert "agent run --resume" in recovery
	assert ("--role recruiter" in recovery) is expect_recruiter_role


def test_hint_command_accepts_explicit_role(tmp_path: Path) -> None:
	command = _hint_command(_context(tmp_path, "recruiter"), "agent run --resume", role="recruiter")

	assert "--role recruiter" in command
	assert "agent run --resume" in command
	assert str(tmp_path) in command
