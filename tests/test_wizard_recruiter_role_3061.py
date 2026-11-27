"""向导熔断恢复命令必须保留招聘者角色（Issue #3061 #3063）。"""

from __future__ import annotations

import io
from pathlib import Path

import click
import pytest
from rich.console import Console

import boss_agent_cli.display as display
from boss_agent_cli.automation.storage import AutomationStore
from boss_agent_cli.commands.wizard import _command_for_ctx
from boss_agent_cli.output import Logger
from boss_agent_cli.wizard.actions import ActionContext, _hint_command, _write_call
from boss_agent_cli.wizard.renderer import render_run
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


def _breaker_recovery(tmp_path: Path, role: str) -> str:
	AutomationStore(tmp_path).request_stop("manual-stop")
	with pytest.raises(WorkflowActionError) as caught:
		_write_call(_context(tmp_path, role), object(), lambda: {}, "fallback")
	return caught.value.recovery_action or ""


def _render_breaker_panel(tmp_path: Path, run: dict[str, object]) -> str:
	ctx = click.Context(click.Command("wizard"))
	ctx.obj = {"data_dir": tmp_path, "platform": "zhipin", "role": "candidate"}
	buffer = io.StringIO()
	console = Console(file=buffer, width=140, force_terminal=False, color_system=None)
	original = display.console
	display.console = console
	try:
		render_run(run, command_for=_command_for_ctx(ctx))
	finally:
		display.console = original
	return buffer.getvalue()


@pytest.mark.parametrize(("role", "expect_recruiter_role"), [("recruiter", True), ("candidate", False)])
def test_tty_breaker_panel_matches_envelope_recovery_role(
	tmp_path: Path, role: str, expect_recruiter_role: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
	"""TTY 面板照抄的是动作层恢复指引，不能丢掉动作上下文里的角色（Issue #3063）。"""
	monkeypatch.delenv("BOSS_EGRESS_PROBE_URL", raising=False)
	recovery = _breaker_recovery(tmp_path, role)
	panel = _render_breaker_panel(
		tmp_path,
		{
			"role": role,
			"goal": "greet" if role == "candidate" else "recruiter_greet",
			"status": "failed",
			"error": {
				"code": "CIRCUIT_BREAKER_OPEN",
				"message": "停止开关已打开（manual-stop），本次不会发出写请求",
				"recovery_action": recovery,
			},
		},
	)

	# Rich 会按终端宽度在空格处换行；先折叠空白再断言命令本身（Issue #3063 CI）。
	flat = " ".join(panel.split())
	assert "agent run --resume" in flat
	assert str(tmp_path.resolve()) in flat
	assert ("--role recruiter" in flat) is expect_recruiter_role


def test_recovery_command_for_ctx_uses_current_role(tmp_path: Path) -> None:
	"""静态恢复文案的兜底路径也按当前角色生成，不能无条件切到招聘者。"""
	from boss_agent_cli.display import recovery_command_for_ctx

	ctx = click.Context(click.Command("wizard"))
	ctx.obj = {"data_dir": tmp_path, "platform": "zhipin", "role": "recruiter"}
	assert "--role recruiter" in recovery_command_for_ctx(ctx, "agent run --resume")

	ctx.obj["role"] = "candidate"
	command = recovery_command_for_ctx(ctx, "agent run --resume")
	assert "--role recruiter" not in command
	assert "agent run --resume" in command
