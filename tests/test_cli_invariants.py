"""CLI contract invariants for Agent-facing output."""

from __future__ import annotations

import json
from pathlib import Path
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
	# 带取值的全局选项给不出可执行命令：next_actions 退回 boss --help，
	# 不能拼一个含 <value> 占位符、Agent 原样执行必然失败的假命令。
	assert payload["hints"]["next_actions"] == ["boss --help"]
	assert "<value>" not in payload["error"]["recovery_action"]
	assert "boss --help" in payload["error"]["recovery_action"]


def test_flag_global_option_hint_is_a_runnable_command(tmp_path: Any) -> None:
	"""不带取值的全局选项可以给出完整修正命令，且该命令必须真能执行。"""
	result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "schema", "--json"])
	assert result.exit_code == 1
	payload = _parse_single_envelope(result)
	assert payload["error"]["recovery_action"] == "boss --json schema"
	assert payload["hints"]["next_actions"] == ["boss --json schema"]

	runnable = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), *payload["hints"]["next_actions"][0].split()[1:]])
	assert runnable.exit_code == 0


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


def test_next_actions_are_accepted_by_the_cli(tmp_path: Any) -> None:
	"""字面量 ``next_actions`` 不只要能解析，还要真的被 CLI 接受。

	占位符放进**受约束的选项值**里时，条目看起来仍是命令，照抄却只会换回
	INVALID_PARAM：``boss --platform <name> status`` 里的 ``<name>`` 会被平台
	校验直接拒绝（可选值只有 ``zhipin``），Agent 拿到的一条可执行路径都没有。
	占位符只能出现在位置参数上，选项值必须给真实取值。

	给每条命令追加 ``--help``：Click 解析完选项就打印帮助退出，既不访问平台
	API，也不要求登录。
	"""
	import shlex

	runner = CliRunner()
	failures: list[str] = []
	for path, line, text in _next_action_literals():
		try:
			argv = shlex.split(text)
		except ValueError:
			failures.append(f"{path}:{line} 无法按 shell 解析：{text}")
			continue
		if not argv or argv[0] != "boss":
			continue
		result = runner.invoke(cli, ["--data-dir", str(tmp_path), *argv[1:], "--help"])
		if result.exit_code != 0:
			failures.append(f"{path}:{line} 退出码 {result.exit_code}：{text}")
	assert failures == []


def test_local_record_lookups_do_not_report_job_not_found() -> None:
	"""本地 / 会话侧记录缺失有自己的错误码，``JOB_NOT_FOUND`` 只表示平台职位不存在。

	crawl run、preset / watch、联系人、workflow run 都不是职位：报成
	``JOB_NOT_FOUND``（登记表文案「职位不存在或已下架」、recovery_action 为 null）
	会让 Agent 以为职位下架而跳过，而且拿不到任何下一步。
	"""
	root = Path(__file__).resolve().parents[1] / "src" / "boss_agent_cli"
	local_lookup_files = (
		"commands/search.py",
		"commands/watch.py",
		"commands/crawl.py",
		"commands/agent.py",
		"commands/_contact_lookup.py",
		"commands/wizard.py",
		"wizard/actions.py",
	)
	offenders = [
		name
		for name in local_lookup_files
		if '"JOB_NOT_FOUND"' in (root / name).read_text(encoding="utf-8")
	]
	assert offenders == [], f"本地记录查找仍复用 JOB_NOT_FOUND：{offenders}"


def _commands_root() -> Path:
	return Path(__file__).resolve().parents[1] / "src" / "boss_agent_cli" / "commands"


def _success_output_sources() -> list[Path]:
	"""守卫要扫描的源码：``commands`` 下所有模块，含 ``recruiter`` 子包。

	只 ``glob("*.py")`` 会漏掉 ``commands/recruiter/*.py``——招聘者侧的
	``boss hr jobs offline/online`` 同样是 ``render=`` + ``next_steps`` 的
	``handle_output``，同样的漂移在那一半命令里不会被发现。
	"""
	return sorted(_commands_root().rglob("*.py"))


def _call_name(func: Any) -> str:
	"""``handle_output(...)`` 与 ``display.handle_output(...)`` 两种写法都算。"""
	import ast

	if isinstance(func, ast.Attribute):
		return func.attr
	return getattr(func, "id", "")


def _success_calls_without_hints() -> list[str]:
	"""收集 render 里有 next_steps、却没给 ``hints`` 的 ``handle_output`` 调用。"""
	import ast

	package_root = _commands_root().parent
	offenders: list[str] = []
	for path in _success_output_sources():
		source = path.read_text(encoding="utf-8")
		for node in ast.walk(ast.parse(source)):
			if not isinstance(node, ast.Call) or _call_name(node.func) != "handle_output":
				continue
			keywords = {kw.arg for kw in node.keywords if kw.arg}
			render = next((kw.value for kw in node.keywords if kw.arg == "render"), None)
			if render is None:
				continue
			if "next_steps" not in (ast.get_source_segment(source, render) or ""):
				continue
			if "hints" not in keywords:
				offenders.append(f"{path.relative_to(package_root)}:{node.lineno}")
	return offenders


def test_success_hints_guard_covers_recruiter_package() -> None:
	"""守卫必须扫到 ``commands/recruiter/``，否则招聘者侧的成功路径不受约束。"""
	package_root = _commands_root().parent
	scanned = {path.relative_to(package_root).as_posix() for path in _success_output_sources()}
	assert "commands/recruiter/jobs.py" in scanned
	assert "commands/recruiter/chat.py" in scanned


def test_success_hints_match_tty_next_steps() -> None:
	"""TTY 展示的下一步必须同时进入信封 ``hints.next_actions``。

	``render`` 只在 TTY 生效，管道 / ``--json`` / MCP 只能看到 ``hints``。只写
	render 会让两条路径看到相反的结果：终端提示「下一步执行 boss preset list」，
	信封里却是 ``hints: null``，Agent 拿不到任何后继动作。
	"""
	assert _success_calls_without_hints() == []


def _leaf_commands_without_help() -> list[str]:
	"""收集没有任何帮助描述的叶子命令（分组命令先生成子命令再递归）。"""
	import click

	def walk(command: Any, path: tuple[str, ...]) -> list[str]:
		if isinstance(command, click.Group):
			found: list[str] = []
			for name, sub in command.commands.items():
				found.extend(walk(sub, (*path, name)))
			return found
		if command.get_short_help_str(limit=200).strip():
			return []
		return [" ".join(path)]

	return walk(cli, ())


def test_every_command_has_help_text() -> None:
	"""``boss --help`` 与各个命令组的 ``--help`` 是能力发现入口。

	没有 docstring 的命令在帮助里只剩一个名字：真人看不出它做什么，Agent 用
	``--help`` 探测能力时也读不到任何说明（同样的说明在 ``boss schema`` 里有，
	但 CLI 帮助不该只对读 schema 的调用方可见）。
	"""
	assert _leaf_commands_without_help() == []


def _public_options_without_help() -> list[str]:
	"""收集没有任何 help 文本的公开（非 hidden）选项。"""
	import click

	def walk(command: click.Command, path: tuple[str, ...]) -> list[str]:
		found: list[str] = []
		if isinstance(command, click.Group):
			for name, sub in command.commands.items():
				found.extend(walk(sub, (*path, name)))
		for param in command.params:
			if not isinstance(param, click.Option) or param.hidden:
				continue
			# is_flag 的布尔开关在 Click 8 里同样走 help；空的就说明没写。
			if not str(param.help or "").strip():
				found.append(f"{' '.join(path)} {param.opts[0] if param.opts else param.name}")
		return found

	return walk(cli, ())


def test_every_public_option_has_help_text() -> None:
	"""每个公开选项都要有说明，否则 ``--help`` 里只剩一个裸开关名。

	``--hook-profile`` 曾只列出 ``[screenshot-full|none]`` 而不说什么时候用、
	和 ``--hook-dir`` 什么关系；``ai local *`` 的 ``--runtime`` / ``--model``
	同样没有说明。用户和 Agent 都只能靠猜，猜错就落到 INVALID_PARAM 上。
	"""
	assert _public_options_without_help() == []
