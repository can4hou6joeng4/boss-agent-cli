"""CLI contract invariants for Agent-facing output."""

from __future__ import annotations

import json
from typing import Any

import pytest
from click.testing import CliRunner, Result

from boss_agent_cli.main import cli


ENVELOPE_KEYS = {
	"ok",
	"schema_version",
	"command",
	"data",
	"pagination",
	"error",
	"hints",
}

ERROR_KEYS = {"code", "message", "recoverable", "recovery_action"}


def _parse_single_envelope(result: Result) -> dict[str, Any]:
	output = result.output.strip()
	assert output
	assert "\n" not in output
	payload = json.loads(output)
	assert set(payload) == ENVELOPE_KEYS
	assert payload["schema_version"] == "1.0"
	return payload


@pytest.mark.parametrize(
	"args, command",
	[
		(["schema"], "schema"),
		(["--json", "schema"], "schema"),
		(["cities"], "cities"),
	],
)
def test_success_commands_emit_single_json_envelope(args: list[str], command: str) -> None:
	result = CliRunner().invoke(cli, args)

	assert result.exit_code == 0
	payload = _parse_single_envelope(result)
	assert payload["ok"] is True
	assert payload["command"] == command
	assert payload["error"] is None
	assert result.stderr == ""


@pytest.mark.parametrize(
	"args, command, code",
	[
		(["--platform", "nonexistent", "schema"], "boss", "INVALID_PARAM"),
		(["schema", "--format", "xml"], "schema", "INVALID_PARAM"),
		(["search"], "search", "INVALID_PARAM"),
		# 子命令的用法错误归到最外层命令上，与同一命令成功信封的 command 一致
		(["resume", "show"], "resume", "INVALID_PARAM"),
		(["shortlist", "list", "--bogus"], "shortlist", "INVALID_PARAM"),
		(["hr", "whoami", "--bogus"], "hr", "INVALID_PARAM"),
	],
)
def test_failure_commands_emit_single_json_error_envelope(
	args: list[str],
	command: str,
	code: str,
) -> None:
	result = CliRunner().invoke(cli, args)

	assert result.exit_code == 1
	payload = _parse_single_envelope(result)
	assert payload["ok"] is False
	assert payload["command"] == command
	assert payload["data"] is None
	assert payload["pagination"] is None
	assert set(payload["error"]) == ERROR_KEYS
	assert payload["error"]["code"] == code
	assert isinstance(payload["error"]["message"], str)
	assert isinstance(payload["error"]["recoverable"], bool)
	assert "recovery_action" in payload["error"]
	assert result.stderr == ""


def _invoke_schema(tmp_path: Any, *extra_args: str) -> dict[str, Any]:
	result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), *extra_args, "--json", "schema"])
	assert result.exit_code == 0, result.output
	return json.loads(result.output.strip())


def test_browser_source_flag_is_exposed_in_schema(tmp_path: Any) -> None:
	"""--browser-source 的字面量必须原样进 current_browser_source，不做映射。"""
	payload = _invoke_schema(tmp_path, "--browser-source", "stored-cookie")
	assert payload["data"]["current_browser_source"] == "stored-cookie"


def test_browser_source_defaults_to_auto(tmp_path: Any) -> None:
	payload = _invoke_schema(tmp_path)
	assert payload["data"]["current_browser_source"] == "auto"


def test_browser_source_reads_config_value(tmp_path: Any) -> None:
	"""config.json 里的 browser_source 必须被读取（无需 CLI 显式传）。"""
	(tmp_path / "config.json").write_text(json.dumps({"browser_source": "existing-browser"}), encoding="utf-8")
	payload = _invoke_schema(tmp_path)
	assert payload["data"]["current_browser_source"] == "existing-browser"


def test_browser_source_rejects_unknown_config_value(tmp_path: Any) -> None:
	"""config.json 里的非法 browser_source 走 main.py 的白名单分支（不是 click.Choice），必须以 INVALID_PARAM 信封拒绝。"""
	(tmp_path / "config.json").write_text(json.dumps({"browser_source": "cdp"}), encoding="utf-8")
	result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "--json", "schema"])
	assert result.exit_code != 0
	payload = json.loads(result.output.strip())
	assert payload["ok"] is False
	assert payload["error"]["code"] == "INVALID_PARAM"
	assert "cdp" in payload["error"]["message"]


def test_browser_source_config_value_is_normalized(tmp_path: Any) -> None:
	"""config.json 里带大小写/空白的合法值按 resolve_policy 同样的规则归一化后接受。"""
	(tmp_path / "config.json").write_text(json.dumps({"browser_source": " Stored-Cookie "}), encoding="utf-8")
	payload = _invoke_schema(tmp_path)
	assert payload["data"]["current_browser_source"] == "stored-cookie"


def test_json_after_subcommand_explains_global_option(tmp_path: Any) -> None:
	result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "search", "实习", "--json"])
	assert result.exit_code == 1
	payload = _parse_single_envelope(result)
	assert payload["ok"] is False
	assert payload["error"]["code"] == "INVALID_PARAM"
	assert "全局选项" in payload["error"]["message"]
	assert "boss --json search" in payload["error"]["message"]
	assert payload["hints"]["operator_actions"]


def test_json_before_subcommand_still_parses(tmp_path: Any) -> None:
	result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "--json", "schema"])
	assert result.exit_code == 0
	payload = _parse_single_envelope(result)
	assert payload["ok"] is True
	assert payload["command"] == "schema"


def test_valued_global_option_hint_includes_placeholder(tmp_path: Any) -> None:
	result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "schema", "--delay", "1-2"])
	assert result.exit_code == 1
	payload = _parse_single_envelope(result)
	assert "boss --delay <value> schema" in payload["error"]["message"]


def test_no_json_after_subcommand_is_also_global(tmp_path: Any) -> None:
	result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "schema", "--no-json"])
	assert result.exit_code == 1
	payload = _parse_single_envelope(result)
	assert "boss --no-json schema" in payload["error"]["message"]


def test_unknown_subcommand_option_stays_a_plain_usage_error(tmp_path: Any) -> None:
	result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "schema", "--not-a-real-option"])
	assert result.exit_code == 1
	payload = _parse_single_envelope(result)
	assert payload["error"]["code"] == "INVALID_PARAM"
	assert "全局选项" not in payload["error"]["message"]


def test_nested_global_option_hint_includes_group_path(tmp_path: Any) -> None:
	result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "hr", "whoami", "--json"])
	assert result.exit_code == 1
	payload = _parse_single_envelope(result)
	assert payload["error"]["code"] == "INVALID_PARAM"
	assert "boss --json hr whoami" in payload["error"]["message"]


def test_browser_source_rejects_unknown_value(tmp_path: Any) -> None:
	result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "--browser-source", "bridge", "--json", "schema"])
	assert result.exit_code != 0


def _next_action_literals() -> list[tuple[str, int, str]]:
	"""收集源码里能静态求值的 ``hints.next_actions`` 条目（字面量与 f-string 片段）。"""
	import ast
	from pathlib import Path

	def render(node: ast.AST) -> str | None:
		if isinstance(node, ast.Constant) and isinstance(node.value, str):
			return node.value
		if isinstance(node, ast.JoinedStr):
			parts: list[str] = []
			for value in node.values:
				if isinstance(value, ast.Constant):
					parts.append(str(value.value))
				else:
					parts.append("{}")
			return "".join(parts)
		return None

	def from_list(node: ast.AST) -> list[str]:
		if not isinstance(node, ast.List):
			return []
		return [text for item in node.elts if (text := render(item)) is not None]

	found: list[tuple[str, int, str]] = []
	root = Path(__file__).resolve().parent.parent / "src" / "boss_agent_cli"
	for path in sorted(root.rglob("*.py")):
		tree = ast.parse(path.read_text(encoding="utf-8"))
		for node in ast.walk(tree):
			if isinstance(node, ast.Dict):
				for key, value in zip(node.keys, node.values):
					if isinstance(key, ast.Constant) and key.value == "next_actions":
						for text in from_list(value):
							found.append((str(path.relative_to(root)), node.lineno, text))
			elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "append":
				target = node.func.value
				if (
					isinstance(target, ast.Subscript)
					and isinstance(target.slice, ast.Constant)
					and target.slice.value == "next_actions"
				):
					for arg in node.args:
						text = render(arg)
						if text is not None:
							found.append((str(path.relative_to(root)), node.lineno, text))
	return found


def test_next_actions_hold_executable_commands_only() -> None:
	"""``hints.next_actions`` 是 Agent 直接执行的通道，条目不能夹带说明文字。

	历史上多处写成「boss xxx — 说明」「使用 boss xxx 查看…」「A 或 B」，Agent
	原样执行只会换回 INVALID_PARAM。带自然语言说明的条目应改放
	``hints.operator_actions``（见 ``docs/commands.md``）。
	"""
	offenders = [
		(f"{path}:{line}", text)
		for path, line, text in _next_action_literals()
		if " — " in text or text.startswith("使用 ") or (text.startswith("boss ") and " 或 " in text)
	]
	assert offenders == []
