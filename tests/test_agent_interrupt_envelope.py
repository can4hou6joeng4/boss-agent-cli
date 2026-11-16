"""agent run / train 中断时仍输出 JSON 信封（Issue #1262）。

runner 在 `KeyboardInterrupt` 时已打开熔断并保留 checkpoint，但 Click 会把未捕获
的 `KeyboardInterrupt` 转成 `Abort` 上抛：stdout 为空，MCP 子进程只能按空输出处理。
命令层必须把它转成 `CIRCUIT_BREAKER_OPEN` 信封。
"""

import json
from unittest.mock import patch

from click.testing import CliRunner

from boss_agent_cli.main import cli


def _invoke(tmp_path, *args: str):
	return CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "--json", *args])


def test_agent_run_interrupt_returns_circuit_breaker_envelope(tmp_path):
	with patch("boss_agent_cli.commands.agent.run_automation_cycle", side_effect=KeyboardInterrupt):
		result = _invoke(tmp_path, "--role", "recruiter", "agent", "run", "--dry-run", "--limit", "1")

	assert result.exit_code == 1
	payload = json.loads(result.output)
	assert payload["ok"] is False
	assert payload["command"] == "agent.run"
	assert payload["error"]["code"] == "CIRCUIT_BREAKER_OPEN"
	assert payload["error"]["recoverable"] is True
	assert "--resume" in payload["error"]["recovery_action"]


def test_agent_train_interrupt_returns_envelope(tmp_path):
	with patch("boss_agent_cli.commands.agent.run_automation_cycle", side_effect=KeyboardInterrupt):
		result = _invoke(tmp_path, "--role", "recruiter", "agent", "train", "--dry-run", "--limit", "1")

	assert result.exit_code == 1
	payload = json.loads(result.output)
	assert payload["command"] == "agent.train"
	assert payload["error"]["code"] == "CIRCUIT_BREAKER_OPEN"
