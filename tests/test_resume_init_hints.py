"""验证 boss resume init 的 hint 引导用户使用 boss me 拉取真实简历。"""
import json
import shlex
from pathlib import Path

from click.testing import CliRunner

from boss_agent_cli.main import cli


def test_resume_init_hints_include_boss_me(tmp_path: Path) -> None:
	"""resume init 完成后应在 hints.next_actions 提示用户用 boss me 拉真实简历。"""
	runner = CliRunner()
	result = runner.invoke(
		cli,
		["--data-dir", str(tmp_path), "resume", "init", "--name", "test"],
	)
	assert result.exit_code == 0, result.output
	envelope = json.loads(result.output)
	assert envelope["ok"] is True
	next_actions = envelope.get("hints", {}).get("next_actions", [])
	assert any("boss me" in action for action in next_actions), (
		f"期望 hint 提示用户走 boss me 拉真实简历，实际 next_actions={next_actions}"
	)


def test_resume_hints_quote_name_with_apostrophe(tmp_path: Path) -> None:
	"""名字里的落单单引号会让提示变成非法 shell：`boss resume show it's` 引号没闭合。

	以前 shell_argument() 只在有空白时加引号，`it's` 原样拼进 hint，Agent
	或用户照抄到终端会直接报「引号未闭合」，而不是看到简历。
	"""
	runner = CliRunner()
	result = runner.invoke(
		cli, ["--data-dir", str(tmp_path), "--json", "resume", "init", "--name", "it's"],
	)
	assert result.exit_code == 0, result.output
	hint = next(
		a for a in json.loads(result.output)["hints"]["next_actions"] if a.startswith("boss resume show")
	)
	assert hint == "boss resume show 'it'\\''s'"

	parts = shlex.split(hint)
	assert parts[3] == "it's"
	shown = runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", *parts[1:]])
	assert shown.exit_code == 0, shown.output
	assert json.loads(shown.output)["data"]["name"] == "it's"


def test_resume_hints_quote_name_with_spaces(tmp_path: Path) -> None:
	"""简历名是自由文本：带空格时 hint 必须整体加引号，照抄才能执行。"""
	runner = CliRunner()
	result = runner.invoke(
		cli, ["--data-dir", str(tmp_path), "--json", "resume", "init", "--name", "我的 简历"],
	)
	assert result.exit_code == 0, result.output
	next_actions = json.loads(result.output)["hints"]["next_actions"]
	assert "boss resume show '我的 简历'" in next_actions

	hint = next(a for a in next_actions if a.startswith("boss resume show"))
	shown = runner.invoke(cli, ["--data-dir", str(tmp_path), "--json", *shlex.split(hint)[1:]])
	assert shown.exit_code == 0, shown.output
	assert json.loads(shown.output)["data"]["name"] == "我的 简历"
