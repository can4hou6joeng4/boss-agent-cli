"""wizard 超时参数有限性校验与状态面板的数据目录改写（Issue #1385 / #1386）。"""

from __future__ import annotations

import io
import json
import time
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner
from rich.console import Console

from boss_agent_cli import display
from boss_agent_cli.commands import wizard as wizard_mod
from boss_agent_cli.display import shell_argument
from boss_agent_cli.main import cli
from boss_agent_cli.mcp_args import _build_args
from boss_agent_cli.wizard.models import StepResult, WorkflowPlan
from boss_agent_cli.wizard.renderer import render_run
from boss_agent_cli.wizard.store import WorkflowStore


def _shortlist_input() -> str:
	return json.dumps(
		{"role": "candidate", "platform": "zhipin", "goal": "shortlist", "inputs": {}},
		ensure_ascii=False,
	)


def _create_failed_run(data_dir: Path, run_id: str, code: str) -> None:
	plan = WorkflowPlan(
		role="candidate",
		platform="zhipin",
		goal="ai_assist",
		inputs={"prompt": "优化"},
		requested_steps=("ai_assist",),
		mode="headless",
	)
	with WorkflowStore(data_dir) as store:
		store.create(run_id, plan)
		store.update_run(
			run_id,
			status="failed",
			current_step="ai_assist",
			error={"code": code, "message": "任务执行失败", "recoverable": True},
		)


@pytest.mark.parametrize("value", ["nan", "inf", "1e400", "-1", "0"])
def test_wizard_timeout_rejects_non_finite_or_non_positive_values(tmp_path: Path, value: str) -> None:
	"""nan / inf / 非正数必须在解析阶段拒绝，且不能留下 workflow run（Issue #1385）。

	修复前 `click.FloatRange` 只比较大小，nan / inf 全部通过；deadline 变成
	无穷大或永远比较为 False，超时保护被静默关闭，命令仍以 ok:true 结束。
	"""
	result = CliRunner().invoke(
		cli,
		[
			"--json",
			"--data-dir",
			str(tmp_path),
			"wizard",
			"--input-json",
			_shortlist_input(),
			"--timeout",
			value,
		],
	)

	assert result.exit_code == 1, result.output
	payload = json.loads(result.stdout)
	assert payload["ok"] is False
	assert payload["error"]["code"] == "INVALID_PARAM"
	assert "timeout" in payload["error"]["message"]
	# 参数错误发生在建立 run 之前：缓存库都不应被创建。
	assert not (tmp_path / "cache" / "boss_agent.db").exists()


def test_wizard_timeout_still_stops_a_slow_workflow(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	"""合法的有限超时保持既有行为：慢动作仍落 WORKFLOW_TIMEOUT。"""

	def slow(context: Any, inputs: Any, prior: Any) -> StepResult:
		time.sleep(0.2)
		return StepResult({"ok": True})

	monkeypatch.setattr(wizard_mod, "DEFAULT_ACTIONS", {"local_shortlist": slow})
	result = CliRunner().invoke(
		cli,
		[
			"--json",
			"--data-dir",
			str(tmp_path),
			"wizard",
			"--input-json",
			_shortlist_input(),
			"--timeout",
			"0.05",
		],
	)

	assert result.exit_code == 1, result.output
	payload = json.loads(result.stdout)
	assert payload["error"]["code"] == "WORKFLOW_TIMEOUT"


@pytest.mark.parametrize(
	("code", "marker"),
	[
		("AI_NOT_CONFIGURED", "ai config"),
		("RESUME_FILE_INVALID", "resume delete"),
	],
)
def test_status_panel_recovery_commands_follow_custom_data_dir(
	tmp_path: Path, monkeypatch: pytest.MonkeyPatch, code: str, marker: str
) -> None:
	"""`--status` 的 TTY 恢复命令必须带本次 `--data-dir`（Issue #1386）。

	修复前 `handle_output(..., render=render_run)` 没传 `command_for`，面板给出
	裸 `boss ai config ...` / `boss resume delete ...`；复制执行会落到默认目录。
	"""
	run_id = f"run-{code.lower()}"
	_create_failed_run(tmp_path, run_id, code)
	monkeypatch.setattr("boss_agent_cli.commands.wizard._is_interactive", lambda ctx: True)
	monkeypatch.setattr("boss_agent_cli.display.is_json_mode", lambda ctx: False)
	# 面板按终端宽度折行，命令会被拆到多行；用宽控制台一次性读回完整文案。
	stream = io.StringIO()
	monkeypatch.setattr(display, "console", Console(file=stream, force_terminal=False, width=400))

	result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "wizard", "--status", run_id])

	assert result.exit_code == 0, result.output
	output = stream.getvalue()
	assert f"boss --data-dir {shell_argument(str(tmp_path.resolve()))} {marker}" in output
	assert f"boss {marker}" not in output


def test_status_panel_without_data_dir_context_keeps_bare_commands(monkeypatch: pytest.MonkeyPatch) -> None:
	"""没有运行上下文时保持原有裸命令，不凭空补默认数据目录。"""
	stream = io.StringIO()
	monkeypatch.setattr(display, "console", Console(file=stream, force_terminal=False, width=400))

	render_run(
		{
			"status": "failed",
			"run_id": "run-bare",
			"error": {"code": "AI_NOT_CONFIGURED", "message": "任务执行失败", "recoverable": True},
		}
	)

	output = stream.getvalue()
	assert "boss ai config" in output
	assert "--data-dir" not in output


@pytest.mark.parametrize("value", ["nan", "inf", "1e400"])
def test_mcp_wizard_timeout_is_rejected_by_the_cli_entry(tmp_path: Path, value: str) -> None:
	"""MCP 的 timeout 通过子进程 argv 下发，同样必须被 CLI 入口拦下（Issue #1385）。"""
	args = _build_args("boss_wizard", {"goal": "shortlist", "inputs": {}, "timeout": value})
	result = CliRunner().invoke(cli, ["--json", "--data-dir", str(tmp_path), *args])

	assert result.exit_code == 1, result.output
	payload = json.loads(result.stdout)
	assert payload["error"]["code"] == "INVALID_PARAM"
	assert not (tmp_path / "cache" / "boss_agent.db").exists()
