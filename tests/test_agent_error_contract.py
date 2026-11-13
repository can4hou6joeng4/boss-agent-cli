"""Agent-facing CLI error envelope contract tests."""

import json
import shlex
import sys
from typing import Any

import click
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

	assert "boss agent pending list --help" in json.loads(
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


def _subcommand_error_cases(
	group: click.Group, prefix: tuple[str, ...] = ()
) -> list[tuple[tuple[str, ...], bool]]:
	cases: list[tuple[tuple[str, ...], bool]] = []
	for name, command in sorted(group.commands.items()):
		if not isinstance(command, click.Group):
			continue
		path = (*prefix, name)
		if command.no_args_is_help:
			cases.append((path, False))
		cases.append((path, True))
		cases.extend(_subcommand_error_cases(command, path))
	return cases


@pytest.mark.parametrize(("group_path", "unknown"), _subcommand_error_cases(cli))
def test_subcommand_error_hints_display_help_without_invoking_actions(tmp_path, monkeypatch, group_path, unknown):
	"""逐组重放错误信封里的提示，不能再次缺参，也不能执行创建、删除等业务动作。"""
	# 提示原样重放；只改测试进程的 Click 默认目录，避免启动流程使用真实用户目录。
	data_dir_option = next(parameter for parameter in cli.params if parameter.name == "data_dir")
	monkeypatch.setattr(data_dir_option, "default", str(tmp_path))
	group = cli
	for name in group_path:
		group = group.commands[name]
	assert isinstance(group, click.Group)
	calls: list[bool] = []

	def forbidden_action(*args, **kwargs):
		calls.append(True)
		raise AssertionError("恢复提示不得调用业务动作")

	for command in group.commands.values():
		monkeypatch.setattr(command, "callback", forbidden_action)

	runner = CliRunner()
	args = [*group_path, *(["bogus"] if unknown else [])]
	parsed = _assert_single_stdout_json_error(runner.invoke(cli, args), command=group_path[0])
	actions = parsed["hints"]["next_actions"]
	assert actions
	for action in actions:
		argv = shlex.split(action)
		assert argv[0] == "boss"
		result = runner.invoke(cli, argv[1:])
		assert result.exit_code == 0, (action, result.output, result.exception)
		assert "Usage:" in result.stdout
	assert calls == []


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


def test_chinese_choice_values_are_still_translated(tmp_path):
	"""候选值本身是中文时，Click 的英文骨架也不能漏出来。

	``boss ai reply --tone 不存在`` 原来回 ``参数 '--tone' 的值无效: '不存在' is
	not one of '简洁专业', …``：正文里混着英文、``recovery_action`` 只有「修正参数」、
	``hints`` 为 null。不能按「消息含中文」判断是不是自家文案——``--industry`` /
	``--stage`` 这类选项的候选值本来就是中文。
	"""
	result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "--json", "ai", "reply", "--tone", "不存在"])
	parsed = _assert_single_stdout_json_error(result, command="ai")
	message = parsed["error"]["message"]
	assert "is not one of" not in message
	assert message == "boss ai reply 的 --tone 取值无效：'不存在' 不在可选值 '简洁专业', '热情积极', '谨慎确认' 中"
	assert parsed["error"]["recovery_action"] == "用 boss ai reply --help 查看参数说明"
	assert parsed["hints"]["next_actions"] == ["boss ai reply --help"]


def test_chinese_paths_are_still_reported_in_chinese(tmp_path):
	"""路径名带中文时，Click 的路径错误同样要翻译。

	``Path '/tmp/不存在的模型目录' does not exist.`` 与 ``'/tmp/不存在.txt': No
	such file or directory`` 都含中文；按「消息含中文」跳过翻译会让英文正文、
	默认「修正参数」和 null ``hints`` 直接透给用户。
	"""
	runner = CliRunner()
	missing_dir = tmp_path / "不存在的模型目录"
	result = runner.invoke(
		cli,
		["--data-dir", str(tmp_path), "--json", "ai", "local", "import", "--path", str(missing_dir), "--model", "qwen3"],
	)
	parsed = _assert_single_stdout_json_error(result, command="ai")
	assert parsed["error"]["message"] == f"boss ai local import 的 --path 无效：路径 '{missing_dir}' 不存在"
	assert parsed["error"]["recovery_action"] == "用 boss ai local import --help 查看参数说明"
	assert parsed["hints"]["next_actions"] == ["boss ai local import --help"]

	curl_file = tmp_path / "不存在的cookies.txt"
	result = runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", "login", "--curl-file", str(curl_file)])
	parsed = _assert_single_stdout_json_error(result, command="login")
	assert parsed["error"]["message"] == f"boss login 的 --curl-file 无效：路径 '{curl_file}' 不存在"
	assert parsed["error"]["recovery_action"] == "用 boss login --help 查看参数说明"
	assert parsed["hints"]["next_actions"] == ["boss login --help"]


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
	login_action = parsed["error"]["recovery_action"]
	assert shlex.split(login_action) == ["boss", "--data-dir", str(tmp_path.resolve()), "login"]
	assert parsed["hints"]["next_actions"] == [login_action]
	operator_actions = parsed["hints"]["operator_actions"]
	assert len(operator_actions) == 1
	assert login_action in operator_actions[0]
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
	assert message.startswith("boss 的 --delay 取值无效：")
	assert "30-60" in message
	assert "0 <= 下限 <= 上限" in message
	assert parsed["error"]["recovery_action"] == "用 boss --help 查看参数说明"
	assert parsed["hints"]["next_actions"] == ["boss --help"]


@pytest.mark.parametrize(
	("args", "command", "recovery", "next_actions"),
	[
		(("--platform", "bogus", "status"), "boss", "用 boss --help 查看参数说明", ["boss --help"]),
		(("--role", "admin", "status"), "boss", "用 boss --help 查看参数说明", ["boss --help"]),
		(
			("platforms", "--capability", "bogus"),
			"platforms",
			"用 boss platforms --help 查看参数说明",
			["boss platforms --help"],
		),
		(
			("mark", "uid1", "--label", "不存在的标签"),
			"mark",
			"用 boss mark --help 查看参数说明",
			["boss mark --help"],
		),
	],
)
def test_custom_parameter_errors_point_at_help(tmp_path, args, command, recovery, next_actions):
	"""回调自定义的中文参数校验不能只回「修正参数」+ null hints。

	``--platform`` / ``--role`` / ``--capability`` 由回调自己 raise，``mark --label``
	在命令体里 raise：原来都没有可执行的下一步；``mark`` 甚至会被
	``handle_auth_errors`` 兜成可重试的 ``NETWORK_ERROR``。
	"""
	runner = CliRunner()
	result = runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", *args])

	parsed = _assert_single_stdout_json_error(result, command=command, code="INVALID_PARAM")
	assert parsed["error"]["recoverable"] is False
	assert parsed["error"]["recovery_action"] == recovery
	assert parsed["hints"]["next_actions"] == next_actions


@pytest.mark.parametrize("value", [":::", "http://", "localhost:9222", "http://127.0.0.1:99999"])
def test_invalid_cdp_url_is_rejected_at_startup(tmp_path, value):
	"""非 http(s) / 无主机 / 端口非法的 CDP 地址要当成参数错误，
	不能让 doctor 把同一个坏地址写进 next_actions 反复执行。"""
	runner = CliRunner()
	result = runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", "--cdp-url", value, "status"])

	parsed = _assert_single_stdout_json_error(result, command="boss", code="INVALID_PARAM")
	assert "http(s) 调试地址" in parsed["error"]["message"]
	assert parsed["error"]["recovery_action"] == "用 boss --help 查看参数说明"
	assert parsed["hints"]["next_actions"] == ["boss --help"]


def test_invalid_config_cdp_url_does_not_block_config_repair(tmp_path):
	"""config.json 里的坏 cdp_url 不能把 `boss config reset cdp_url` 也挡在启动期。"""
	(tmp_path / "config.json").write_text(json.dumps({"cdp_url": ":::"}), encoding="utf-8")

	result = CliRunner().invoke(cli, ["--data-dir", str(tmp_path), "--json", "config", "reset", "cdp_url"])

	assert result.exit_code == 0, result.output
	parsed = json.loads(result.output)
	assert parsed["data"] == {"key": "cdp_url", "value": None, "restored": True}


@pytest.mark.parametrize("value", ["", "   "])
def test_blank_data_dir_is_param_error_not_current_directory(tmp_path, monkeypatch, value):
	"""`--data-dir ""` 只能报参数错误：`Path("")` 是当前目录，登录态与缓存会静默写进 cwd，
	下一个命令还会因为找不到 ~/.boss-agent 的登录态而回「未登录」。"""
	workdir = tmp_path / "work"
	workdir.mkdir()
	monkeypatch.chdir(workdir)

	result = CliRunner().invoke(cli, ["--data-dir", value, "--json", "status"])

	parsed = _assert_single_stdout_json_error(result, command="boss", code="INVALID_PARAM")
	assert "不能为空" in parsed["error"]["message"]
	assert parsed["hints"]["next_actions"] == ["boss --help"]
	assert list(workdir.iterdir()) == []


def test_data_dir_with_surrounding_whitespace_is_rejected(tmp_path):
	"""首尾空白会被当成目录名的一部分，静默建出一个几乎看不见的目录。"""
	target = tmp_path / "padded"

	result = CliRunner().invoke(cli, ["--data-dir", f" {target} ", "--json", "status"])

	parsed = _assert_single_stdout_json_error(result, command="boss", code="INVALID_PARAM")
	assert "首尾不能有空白" in parsed["error"]["message"]
	assert not target.exists()


def test_data_dir_pointing_at_a_file_is_param_error(tmp_path):
	"""目录位置被文件占住时，错误要指向 --data-dir 而不是报成命令的本地 IO 失败。"""
	target = tmp_path / "occupied"
	target.write_text("not a directory", encoding="utf-8")

	result = CliRunner().invoke(cli, ["--data-dir", str(target), "--json", "status"])

	parsed = _assert_single_stdout_json_error(result, command="boss", code="INVALID_PARAM")
	assert "--data-dir" in parsed["error"]["message"]
	assert "不是目录" in parsed["error"]["message"]
	assert parsed["hints"]["next_actions"] == ["boss --help"]


def test_data_dir_under_a_file_is_param_error(tmp_path):
	"""上级路径是文件时同理：不能把 NotADirectoryError 说成本地读写失败。"""
	target = tmp_path / "occupied"
	target.write_text("not a directory", encoding="utf-8")

	result = CliRunner().invoke(cli, ["--data-dir", str(target / "child"), "--json", "status"])

	parsed = _assert_single_stdout_json_error(result, command="boss", code="INVALID_PARAM")
	assert "上级路径不是目录" in parsed["error"]["message"]


@pytest.mark.skipif(sys.platform == "win32", reason="Windows 的 Path.expanduser 不解析 ~user")
def test_unknown_tilde_user_is_param_error_not_traceback():
	"""`~某用户` 查不到家目录时 expanduser 抛 RuntimeError：原来只有 traceback、没有信封。"""
	result = CliRunner().invoke(cli, ["--data-dir", "~boss_cli_no_such_user_zzz/data", "--json", "status"])

	parsed = _assert_single_stdout_json_error(result, command="boss", code="INVALID_PARAM")
	assert "无法展开家目录写法" in parsed["error"]["message"]
	assert parsed["hints"]["next_actions"] == ["boss --help"]


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
