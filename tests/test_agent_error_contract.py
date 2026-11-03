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
		# 选项缺取值时 Click 在 parser 层报错、不挂 ctx：仍要落在对应子命令上
		(("search", "--city"), "search", "--city"),
		(("wizard", "--resume"), "wizard", "--resume"),
		(("crawl", "start", "--city"), "crawl", "--city"),
		(("--data-dir",), "boss", "--data-dir"),
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


def test_unknown_option_points_at_command_help(tmp_path):
	"""Click 的英文 ``No such option`` 与拼写建议对 Agent 是误导。

	``boss search golang --limit 10`` 拿到的 ``Did you mean '--city'?`` 与「限制结果
	数」的意图无关；信封应点名命令与选项，并给出可执行的 ``--help``。
	"""
	runner = CliRunner()
	for args, command, path in (
		(("search", "golang", "--limit", "10"), "search", "boss search"),
		(("resume", "show", "--format", "x"), "resume", "boss resume show"),
	):
		result = runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", *args])
		parsed = _assert_single_stdout_json_error(result, command=command)
		message = parsed["error"]["message"]
		assert "No such option" not in message
		assert "Did you mean" not in message
		assert f"{path} 没有选项" in message
		assert parsed["hints"]["next_actions"] == [f"{path} --help"]


def test_unknown_subcommand_reports_available_commands(tmp_path):
	"""写错子命令时不能只回 Click 的英文原文。

	`boss resume bogus` 的信封只有 `No such command 'bogus'.`：Agent 看不出属于哪个
	命令组、有哪些可选值，`hints` 也是 null，只能再猜一次；`boss agent review bogus`
	这类二级组同样如此。
	"""
	runner = CliRunner()
	for args, command, path in (
		(("resume", "bogus"), "resume", "boss resume"),
		(("agent", "review", "bogus"), "agent", "boss agent review"),
	):
		result = runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", *args])
		parsed = _assert_single_stdout_json_error(result, command=command)
		message = parsed["error"]["message"]
		assert "No such command" not in message
		assert f"{path} 没有子命令 'bogus'" in message
		assert "可选：" in message
		next_actions = parsed["hints"]["next_actions"]
		assert all(action.startswith(f"{path} ") for action in next_actions)


def test_missing_required_parameter_points_at_command_help(tmp_path):
	"""缺必填参数 / 选项时不能只回 Click 的英文原文。

	``boss mark <uid>`` 少写 ``--label``、``boss resume diff <旧名>`` 少写第二个
	名字，信封原来只有 ``Missing option '--label'.`` / ``Missing argument
	'NAME2'.`` 与笼统的「修正参数」：Agent 看不出缺的是选项还是位置参数，也没有
	可执行的下一步。现在按类型给出中文说明，并指向该命令的 ``--help``。
	"""
	runner = CliRunner()
	for args, command, fragment, path in (
		(("mark", "abc"), "mark", "缺少必填选项 '--label'", "boss mark"),
		(("resume", "diff", "a"), "resume", "缺少必填参数 'NAME2'", "boss resume diff"),
	):
		result = runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", *args])
		parsed = _assert_single_stdout_json_error(result, command=command)
		message = parsed["error"]["message"]
		assert "Missing option" not in message
		assert "Missing argument" not in message
		assert message == f"{path} {fragment}"
		assert parsed["error"]["recovery_action"] == f"用 {path} --help 查看用法"
		assert parsed["hints"]["next_actions"] == [f"{path} --help"]


def test_unexpected_extra_argument_is_reported_in_chinese(tmp_path):
	"""多给参数时同样只有英文原文与一句「修正参数」。

	``boss search golang extra`` / ``boss resume init a b`` 的信封原来是
	``Got unexpected extra argument (extra)``——读起来像内部错误而不是「你多写了
	一个参数」，Agent 不知道下一步该删掉它。
	"""
	runner = CliRunner()
	for args, command, shown, path in (
		(("search", "golang", "extra"), "search", "'extra'", "boss search"),
		(("resume", "init", "a", "b"), "resume", "'a b'", "boss resume init"),
	):
		result = runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", *args])
		parsed = _assert_single_stdout_json_error(result, command=command)
		message = parsed["error"]["message"]
		assert "Got unexpected" not in message
		assert message == f"{path} 收到了多余的参数 {shown}"
		assert f"用 {path} --help 查看用法" in parsed["error"]["recovery_action"]
		assert parsed["hints"]["next_actions"] == [f"{path} --help"]


def test_invalid_option_value_is_reported_in_chinese(tmp_path):
	"""Click 自带校验（范围 / 枚举 / 数值转换）的英文正文不能原样透出。

	``boss search golang --page 0``、``boss export golang --format ""``、
	``boss hr reply u1 hi`` 原来分别回 ``Invalid value for '--page': 0 is not in
	the range x>=1.`` / ``'' is not one of 'html', 'csv', 'json'.`` /
	``'u1' is not a valid integer.``：错误码正确，但正文英文、``hints`` 为 null，
	Agent 读不出该把参数改成什么。
	"""
	runner = CliRunner()
	for args, command, expected, path in (
		(("search", "golang", "--page", "0"), "search", "boss search 的 --page 取值无效：0 不在范围 x>=1 内", "boss search"),
		(
			("export", "golang", "--format", "pdf"),
			"export",
			"boss export 的 --format 取值无效：'pdf' 不在可选值 'html', 'csv', 'json' 中",
			"boss export",
		),
		(("hr", "reply", "u1", "hi"), "hr", "boss hr reply 的 FRIEND_ID 取值无效：'u1' 不是整数", "boss hr reply"),
	):
		result = runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", *args])
		parsed = _assert_single_stdout_json_error(result, command=command)
		message = parsed["error"]["message"]
		assert "Invalid value for" not in message
		assert message == expected
		assert parsed["error"]["recovery_action"] == f"用 {path} --help 查看参数说明"
		assert parsed["hints"]["next_actions"] == [f"{path} --help"]


def test_invalid_path_value_is_reported_in_chinese(tmp_path):
	"""Click 的路径校验（``Path '' does not exist.``）同样是英文正文 + 无 hints。

	``boss ai local import --path ""`` 原来只回 ``Invalid value for '--path':
	Path '' does not exist.``，Agent 看不出是路径为空、不存在还是类型不对。
	"""
	runner = CliRunner()
	for value in ("", str(tmp_path / "missing.gguf")):
		result = runner.invoke(
			cli,
			["--data-dir", str(tmp_path), "--json", "ai", "local", "import", "--path", value, "--model", "qwen3"],
		)
		parsed = _assert_single_stdout_json_error(result, command="ai")
		message = parsed["error"]["message"]
		assert "Invalid value for" not in message
		assert message == f"boss ai local import 的 --path 无效：路径 '{value}' 不存在"
		assert parsed["error"]["recovery_action"] == "用 boss ai local import --help 查看参数说明"
		assert parsed["hints"]["next_actions"] == ["boss ai local import --help"]


def test_file_typed_path_error_is_reported_in_chinese(tmp_path):
	"""只收文件的 ``click.Path`` 前缀是 ``File``，同样要被路径转换器接住。

	``boss stats --output <目录>`` 原来回 ``Invalid value for '-o' / '--output':
	File '/tmp' is a directory.``：错误码正确，但正文英文、``hints`` 为 null。
	Click 的 ``Path`` 按 ``dir_okay`` / ``file_okay`` 生成 ``Path`` / ``File`` /
	``Directory`` 三种前缀。
	"""
	result = CliRunner().invoke(
		cli, ["--data-dir", str(tmp_path), "--json", "stats", "--output", str(tmp_path)]
	)
	parsed = _assert_single_stdout_json_error(result, command="stats")
	message = parsed["error"]["message"]
	assert "Invalid value for" not in message
	assert message == f"boss stats 的 -o / --output 无效：路径 '{tmp_path}' 是目录，但这里需要文件"
	assert parsed["error"]["recovery_action"] == "用 boss stats --help 查看参数说明"
	assert parsed["hints"]["next_actions"] == ["boss stats --help"]


def test_unopenable_file_error_is_reported_in_chinese(tmp_path):
	"""``click.File`` 打不开文件时透出的是 OSError 的英文 strerror。

	``boss login --curl-file ""`` 原来回 ``Invalid value for '--curl-file': '':
	No such file or directory``，``--curl-file <目录>`` 回 ``'/tmp': Is a
	directory``：错误码正确，但正文英文、``hints`` 为 null，Agent 读不出该改什么。
	"""
	runner = CliRunner()
	missing = tmp_path / "missing.txt"
	for value, expected in (
		("", "boss login 的 --curl-file 的值不能为空"),
		(str(missing), f"boss login 的 --curl-file 无效：路径 '{missing}' 不存在"),
		(str(tmp_path), f"boss login 的 --curl-file 无效：路径 '{tmp_path}' 是目录，但这里需要文件"),
	):
		result = runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", "login", "--curl-file", value])
		parsed = _assert_single_stdout_json_error(result, command="login")
		message = parsed["error"]["message"]
		assert "Invalid value for" not in message
		assert message == expected
		assert parsed["error"]["recovery_action"] == "用 boss login --help 查看参数说明"
		assert parsed["hints"]["next_actions"] == ["boss login --help"]


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


@pytest.mark.parametrize("args", [("search", "golang"), ("status",), ("interviews",)])
def test_auth_required_points_at_login_with_operator_hint(tmp_path, args):
	"""未登录信封不能只有「boss login」：还要给出可执行的下一步与真人扫码说明。"""
	runner = CliRunner()
	result = runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", *args])

	parsed = _assert_single_stdout_json_error(result, command=args[0], code="AUTH_REQUIRED")
	assert parsed["error"]["recovery_action"] == "boss login"
	assert parsed["hints"]["next_actions"] == ["boss login"]
	operator_actions = parsed["hints"]["operator_actions"]
	assert len(operator_actions) == 1
	assert "boss login" in operator_actions[0]
	assert "扫码" in operator_actions[0]


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


@pytest.mark.parametrize(
	("args", "command", "recovery_fragment"),
	[
		(("agent", "review", "approve", "missing"), "agent.review.approve", "agent review list"),
		(("agent", "review", "reject", "missing"), "agent.review.reject", "agent review list"),
		(("agent", "crawl", "--run-id", "x", "--query", "y", "--resume", "r"), "agent.crawl", "--run-id"),
		(("agent", "crawl", "--query", "golang", "--resume", "r"), "agent.crawl", "--city"),
	],
)
def test_agent_param_errors_point_at_recovery_command(tmp_path, args, command, recovery_fragment):
	"""复核项不存在、crawl 参数冲突原来只回登记表的「修正参数」，Agent 拿不到下一步。"""
	runner = CliRunner()
	result = runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", *args])

	parsed = _assert_single_stdout_json_error(result, command=command)
	assert recovery_fragment in parsed["error"]["recovery_action"]
	assert parsed["hints"]["next_actions"]


@pytest.mark.parametrize(
	("args", "command", "message", "help_path"),
	[
		(("search", "--city"), "search", "boss search 的选项 --city 缺少取值", "boss search"),
		(("crawl", "start", "--city"), "crawl", "boss crawl start 的选项 --city 缺少取值", "boss crawl start"),
	],
)
def test_missing_option_value_is_reported_in_chinese(tmp_path, args, command, message, help_path):
	"""``boss search --city`` 原来把 Click 的英文原文直接交给 Agent，``recovery_action``
	也只有「修正参数」；解析器层异常不挂 ctx，命令路径要从 argv 推断。"""
	runner = CliRunner()
	result = runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", *args])

	parsed = _assert_single_stdout_json_error(result, command=command)
	assert parsed["error"]["message"] == message
	assert "requires an argument" not in parsed["error"]["message"]
	assert "--city" in parsed["error"]["recovery_action"]
	assert parsed["hints"]["next_actions"] == [f"{help_path} --help"]


def test_empty_extra_argument_is_reported_in_chinese(tmp_path):
	"""``boss chat ""`` 也是多给了一个参数：Click 的括号里为空，翻译不能漏掉这条路径。"""
	runner = CliRunner()
	result = runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", "chat", ""])

	parsed = _assert_single_stdout_json_error(result, command="chat")
	assert parsed["error"]["message"] == "boss chat 不接受额外的空参数"
	assert parsed["hints"]["next_actions"] == ["boss chat --help"]
