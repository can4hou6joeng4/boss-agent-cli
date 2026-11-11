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


def _boss_command_argument_literals() -> list[tuple[str, int, str]]:
	"""收集 ``boss_command_for_ctx(ctx, "...")`` 里能静态求值的子命令实参。"""
	import ast

	def render(node: ast.AST) -> str | None:
		if isinstance(node, ast.Constant) and isinstance(node.value, str):
			return node.value
		if isinstance(node, ast.JoinedStr):
			parts: list[str] = []
			for value in node.values:
				parts.append(str(value.value) if isinstance(value, ast.Constant) else "1")
			return "".join(parts)
		return None

	found: list[tuple[str, int, str]] = []
	root = Path(__file__).resolve().parent.parent / "src" / "boss_agent_cli"
	for path in sorted(root.rglob("*.py")):
		tree = ast.parse(path.read_text(encoding="utf-8"))
		parents: dict[ast.AST, ast.AST] = {}
		for parent in ast.walk(tree):
			for child in ast.iter_child_nodes(parent):
				parents[child] = parent
		for node in ast.walk(tree):
			if not (
				isinstance(node, ast.Call)
				and isinstance(node.func, ast.Name)
				and node.func.id == "boss_command_for_ctx"
				and len(node.args) >= 2
			):
				continue
			parent = parents.get(node)
			# `boss_command_for_ctx(ctx, "batch-greet").split(" ")` 只取命令前缀，
			# 调用者随后还会拼接参数，不能单拿字面量校验。
			if isinstance(parent, ast.Attribute) and parent.attr == "split":
				continue
			text = render(node.args[1])
			if text is not None:
				found.append((str(path.relative_to(root)), node.lineno, text))
	return found


def test_boss_command_for_ctx_literals_are_accepted_by_the_cli(tmp_path: Any) -> None:
	"""``boss_command_for_ctx`` 的静态子命令实参也要能被 CLI 接受。

	``hints`` / ``recovery_action`` 里的命令大多由 ``boss_command_for_ctx(ctx, "...")``
	拼出：实参不含 ``boss`` 前缀，也不经过 ``next_actions`` 字面量扫描，写错选项
	时只有 Agent 在执行 hint 的那一刻才会失败。这里按 AST 收集第二参数能静态
	求值的调用点，把 ``<占位符>`` 掩码成合法取值后逐条追加 ``--help`` 交给 CLI，
	只校验解析、不执行命令。
	"""
	import re
	import shlex

	command_literals = _boss_command_argument_literals()
	assert len(command_literals) >= 150, f"只收集到 {len(command_literals)} 条，AST 扫描可能失效"
	runner = CliRunner()
	failures: list[str] = []
	for path, line, text in command_literals:
		text = re.sub(r"<[^<>\s]+>", "1", text)
		try:
			argv = shlex.split(text)
		except ValueError:
			failures.append(f"{path}:{line} 无法按 shell 解析：{text}")
			continue
		if argv and argv[0] == "boss":
			argv = argv[1:]
		result = runner.invoke(cli, ["--data-dir", str(tmp_path), *argv, "--help"])
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


# Issue #809 尚未拍板的信封 command 词汇表：这些取值既不是 ``boss schema`` 的
# 一级命令名，也没有统一成点号层级或连字符扁平中的任何一种。本表只用于冻结
# 现状、阻止新增第四种风格；迁移落地时逐项删除，不要往这里加新条目。
_LEGACY_ENVELOPE_COMMANDS = frozenset({
	"agent.control",
	"agent.crawl",
	"agent.pending.list",
	"agent.review.approve",
	"agent.review.list",
	"agent.review.reject",
	"agent.run",
	"agent.stats",
	"agent.stop",
	"agent.train",
	"ai-analyze-jd",
	"ai-chat-coach",
	"ai-config",
	"ai-cover-letter",
	"ai-fit",
	"ai-interview-prep",
	"ai-optimize",
	"ai-polish",
	"ai-reply",
	"ai-resume-optimize",
	"ai-suggest",
	"ai-suggest-keywords",
	"ai.local.configure",
	"ai.local.import",
	"ai.local.pull",
	"ai.local.smoke",
	"ai.local.status",
	"crawl.configure",
	"crawl.results",
	"crawl.resume",
	"crawl.shortlist",
	"crawl.start",
	"crawl.status",
	"crawl.stop",
	"favorites-list",
	"favorites-sync",
	"recruiter-accept-resume",
	"recruiter-applications",
	"recruiter-candidates",
	"recruiter-chat",
	"recruiter-chatmsg",
	"recruiter-download-resume",
	"recruiter-greet",
	"recruiter-jobs-detail",
	"recruiter-jobs-list",
	"recruiter-jobs-offline",
	"recruiter-jobs-online",
	"recruiter-last-messages",
	"recruiter-location",
	"recruiter-recommendations",
	"recruiter-reply",
	"recruiter-request-resume",
	"recruiter-resume",
	"recruiter-whoami",
})


def _envelope_command_literals() -> list[tuple[str, int, str]]:
	"""收集成功 / 错误信封 command 实参里能直接读出的字符串字面量。

	除了六个直接 emit 调用，还要扫 ``@handle_auth_errors("...")``：这个装饰器
	在认证失败时同样产出信封，漏扫会让错误路径悄悄引入新取值。
	"""
	import ast

	call_positions = {
		"emit_success": 0,
		"emit_error": 0,
		"handle_output": 1,
		"handle_error_output": 1,
		"handle_not_supported": 1,
		"handle_platform_error_output": 1,
	}
	found: list[tuple[str, int, str]] = []
	root = Path(__file__).resolve().parent.parent / "src" / "boss_agent_cli"
	for path in sorted(root.rglob("*.py")):
		tree = ast.parse(path.read_text(encoding="utf-8"))
		for node in ast.walk(tree):
			if isinstance(node, ast.Call):
				name = node.func.id if isinstance(node.func, ast.Name) else (
					node.func.attr if isinstance(node.func, ast.Attribute) else ""
				)
				position = call_positions.get(name)
				if position is not None and len(node.args) > position:
					arg = node.args[position]
					if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
						found.append((str(path.relative_to(root)), node.lineno, arg.value))
			if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
				for decorator in node.decorator_list:
					if not isinstance(decorator, ast.Call) or not decorator.args:
						continue
					name = decorator.func.id if isinstance(decorator.func, ast.Name) else (
						decorator.func.attr if isinstance(decorator.func, ast.Attribute) else ""
					)
					if name != "handle_auth_errors":
						continue
					arg = decorator.args[0]
					if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
						found.append((str(path.relative_to(root)), decorator.lineno, arg.value))
	return found


def test_envelope_command_values_are_registered() -> None:
	"""信封 command 取值必须收敛到一级命令名或 #809 的显式遗留清单。

	Agent 按 ``boss schema`` 的 39 个一级命令做路由；新增命令若随手写
	``favorites-star`` / ``crawl.foo``，路由会在发布后才踩空。这个守卫让第四种
	风格在本地门禁失败，保留 Issue #809 的词汇表决定权。
	"""
	from boss_agent_cli.schema.data import SCHEMA_DATA

	registered = set(SCHEMA_DATA["commands"]) | _LEGACY_ENVELOPE_COMMANDS
	offenders = [
		f"{path}:{line} -> {value!r}"
		for path, line, value in _envelope_command_literals()
		if value not in registered
	]
	assert offenders == [], (
		"信封 command 出现未登记取值；请复用一级命令名，或先在 Issue #809 决定词汇表并更新本表：\n"
		+ "\n".join(offenders)
	)


def test_legacy_envelope_command_allowlist_has_no_overlap_or_stale_entries() -> None:
	"""遗留清单不能与一级命令重名，也不能留下没人再产出的死条目。"""
	from boss_agent_cli.schema.data import SCHEMA_DATA

	values = {value for _, _, value in _envelope_command_literals()}
	assert _LEGACY_ENVELOPE_COMMANDS.isdisjoint(SCHEMA_DATA["commands"])
	assert _LEGACY_ENVELOPE_COMMANDS <= values, sorted(_LEGACY_ENVELOPE_COMMANDS - values)
