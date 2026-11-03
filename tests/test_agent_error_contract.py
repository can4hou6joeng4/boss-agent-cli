"""Agent-facing CLI error envelope contract tests."""

import json
from typing import Any

import pytest
from click.testing import CliRunner, Result

from boss_agent_cli.main import cli


_ERROR_KEYS = {"code", "message", "recoverable", "recovery_action"}


def _assert_single_stdout_json_error(result: Result, *, command: str, code: str = "INVALID_PARAM") -> dict[str, Any]:
	"""Agent-facing errors must stay as a single machine-consumable stdout envelope."""
	assert result.exit_code == 1
	assert result.stderr == ""
	assert result.output.strip()
	assert "\n" not in result.output.strip()
	parsed = json.loads(result.output)
	assert parsed["ok"] is False
	assert parsed["schema_version"] == "1.0"
	assert parsed["command"] == command
	assert parsed["data"] is None
	assert parsed["pagination"] is None
	assert set(parsed["error"]) == _ERROR_KEYS
	assert parsed["error"]["code"] == code
	assert isinstance(parsed["error"]["message"], str)
	assert isinstance(parsed["error"]["recoverable"], bool)
	return parsed


@pytest.mark.parametrize(
	("args", "command", "message_fragment"),
	[
		(("--platform", "unknown", "status"), "boss", "--platform"),
		(("--delay", "fast", "status"), "boss", "--delay"),
		(("--role", "admin", "status"), "boss", "--role"),
		(("detail",), "detail", "SECURITY_ID"),
		# 子命令的用法错误归到最外层命令上，与同一命令成功信封的 command 一致
		(("config", "get"), "config", "KEY"),
		(("shortlist", "add"), "shortlist", "SECURITY_ID"),
	],
)
def test_agent_facing_usage_errors_emit_stdout_json_envelope(tmp_path, args, command, message_fragment):
	"""Global parameter and high-frequency usage errors keep the JSON error contract."""
	runner = CliRunner()
	result = runner.invoke(cli, ["--data-dir", str(tmp_path), *args])

	parsed = _assert_single_stdout_json_error(result, command=command)
	assert message_fragment in parsed["error"]["message"]


def test_group_without_subcommand_reports_subcommand_hints(tmp_path):
	"""只写命令组时不能把整份英文帮助塞进 error.message。

	Click 的 ``NoArgsIsHelpError`` 消息就是 usage/help 全文（「Usage: …」
	「Options: …」几百字符），Agent 既读不到「缺子命令」这个原因，也没有
	可执行的下一步。现在换成一句中文说明，并把子命令清单放进 hints。
	"""
	runner = CliRunner()
	for args, command, path in (
		(("resume",), "resume", "boss resume"),
		(("agent", "pending"), "agent", "boss agent pending"),
	):
		result = runner.invoke(cli, ["--data-dir", str(tmp_path), *args])
		parsed = _assert_single_stdout_json_error(result, command=command)
		message = parsed["error"]["message"]
		assert "Usage:" not in message
		assert "缺少子命令" in message
		assert parsed["error"]["recovery_action"]
		next_actions = parsed["hints"]["next_actions"]
		assert next_actions
		assert all(action.startswith(f"{path} ") for action in next_actions)

	assert "boss agent pending list" in json.loads(
		runner.invoke(cli, ["--data-dir", str(tmp_path), "agent", "pending"]).output
	)["hints"]["next_actions"]


def test_agent_run_requires_login_returns_envelope_not_traceback(tmp_path):
	"""未登录的 `agent run` 必须返回 AUTH_REQUIRED 信封，而不是 traceback。

	`run_cmd` / `train_cmd` 没挂 `handle_auth_errors`（同组的 `agent crawl` 挂了）：
	自动化适配器在扫描会话时抛出的 `AuthRequired` 一路冒到顶层，stdout 没有任何
	信封、stderr 是完整 traceback，MCP 子进程只能解析失败回成 `CLI_ERROR`。
	"""
	runner = CliRunner()
	result = runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", "agent", "run"])

	parsed = _assert_single_stdout_json_error(result, command="agent.run", code="AUTH_REQUIRED")
	assert parsed["error"]["recoverable"] is True
	assert parsed["error"]["recovery_action"]


def test_agent_train_live_requires_login_returns_envelope(tmp_path):
	"""`agent train --live` 的登录态检查同样走信封。"""
	runner = CliRunner()
	result = runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", "agent", "train", "--live"])

	parsed = _assert_single_stdout_json_error(result, command="agent.train", code="AUTH_REQUIRED")
	assert parsed["error"]["recoverable"] is True


def test_invalid_delay_reports_chinese_message(tmp_path):
	"""--delay 的范围错误要给出中文说明和示例，而不是 Click 的英文原文。"""
	runner = CliRunner()
	result = runner.invoke(cli, ["--data-dir", str(tmp_path), "--delay", "5-1", "status"])

	parsed = _assert_single_stdout_json_error(result, command="boss")
	message = parsed["error"]["message"]
	assert "Invalid value" not in message
	assert message.startswith("参数 --delay 的值无效: ")
	assert "30-60" in message
	assert "0 <= 下限 <= 上限" in message
