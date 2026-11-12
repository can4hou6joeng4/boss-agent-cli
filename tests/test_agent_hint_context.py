"""招聘自动化命令的后继与恢复提示在产生它的数据目录中直接重放。"""

import json
import shlex

import pytest
from click.testing import CliRunner

from boss_agent_cli.main import cli


@pytest.fixture
def agent_dir(tmp_path, monkeypatch):
	"""数据目录含空格、单引号与 Rich 标签；默认目录指向一次性 HOME。"""
	monkeypatch.setenv("HOME", str(tmp_path / "home"))
	data_dir = tmp_path / "jobs [bold] team's"
	data_dir.mkdir()
	return data_dir


def _run(data_dir, *args):
	return CliRunner().invoke(cli, ["--data-dir", str(data_dir), "--json", *args])


def _prefix(action, data_dir):
	parts = shlex.split(action)
	assert parts[:3] == ["boss", "--data-dir", str(data_dir.resolve())], action
	return parts


def _recruiter_argv(action, data_dir):
	"""招聘者命令必须显式带 --role recruiter：agent 组不在 hr 组下。"""
	parts = _prefix(action, data_dir)
	assert parts[3:5] == ["--role", "recruiter"], action
	return parts[1:]


def _crawl_argv(action, data_dir):
	"""候选人侧的 agent crawl 沿用目录，但不应被强行加上招聘者角色。"""
	parts = _prefix(action, data_dir)
	assert "--role" not in parts, action
	return parts[1:]


def _replay(action, data_dir, parser=_recruiter_argv, **placeholders):
	values = {"<id>": "missing", **placeholders}
	argv = [values.get(part, part) for part in parser(action, data_dir)]
	result = CliRunner().invoke(cli, ["--json", *argv])
	assert result.exit_code == 0, result.output
	return json.loads(result.stdout)


def test_agent_stats_hints_replay_in_original_directory(agent_dir):
	result = _run(agent_dir, "agent", "stats")
	assert result.exit_code == 0, result.output
	actions = json.loads(result.stdout)["hints"]["next_actions"]
	assert [shlex.split(action)[-1] for action in actions] == ["stats", "list", "list"]
	assert [shlex.split(action)[5:] for action in actions] == [
		["agent", "stats"],
		["agent", "review", "list"],
		["agent", "pending", "list"],
	]
	# 统计写入同一个数据目录：提示丢了目录或角色就会落到默认目录的另一套状态。
	stats = _replay(actions[0], agent_dir)
	assert stats["data"]["events"] == 0


def test_agent_control_commands_keep_directory_and_role(agent_dir):
	result = _run(agent_dir, "agent", "control")
	assert result.exit_code == 0, result.output
	payload = json.loads(result.stdout)["data"]
	assert len(payload["commands"]) == 4
	for command in payload["commands"]:
		_recruiter_argv(command, agent_dir)
	assert payload["commands"][1].endswith("agent stats")


def test_agent_stop_followup_replays_in_original_directory(agent_dir):
	result = _run(agent_dir, "agent", "stop", "--reason", "测试停用")
	assert result.exit_code == 0, result.output
	actions = json.loads(result.stdout)["hints"]["next_actions"]
	for action in actions:
		_recruiter_argv(action, agent_dir)
	stats = _replay(actions[0], agent_dir)
	assert stats["data"]["events"] == 0


@pytest.mark.parametrize("subcommand", ["approve", "reject"])
def test_review_missing_recovery_replays_in_original_directory(agent_dir, subcommand):
	result = _run(agent_dir, "agent", "review", subcommand, "missing")
	assert result.exit_code == 1, result.output
	parsed = json.loads(result.stdout)
	assert parsed["error"]["code"] == "INVALID_PARAM"
	recovery_cmd = parsed["error"]["recovery_action"].split("用 ", 1)[1].split(" 核对", 1)[0]
	assert parsed["hints"]["next_actions"] == [recovery_cmd]
	listed = _replay(recovery_cmd, agent_dir)
	assert listed["data"]["items"] == []


def test_agent_run_invalid_config_recovery_replays_in_original_directory(agent_dir):
	(agent_dir / "config.json").write_text('{"automation": "foo"}', encoding="utf-8")
	result = _run(agent_dir, "agent", "run", "--dry-run")
	assert result.exit_code == 1, result.output
	parsed = json.loads(result.stdout)
	assert parsed["error"]["code"] == "INVALID_PARAM"
	parts = _prefix(parsed["error"]["recovery_action"], agent_dir)
	assert parts[3:] == ["config", "reset", "automation"]
	# 恢复命令只依赖原目录中的 config.json，直接重放即可修复。
	fixed = CliRunner().invoke(cli, ["--json", *parts[1:]])
	assert fixed.exit_code == 0, fixed.output
	assert "automation" not in json.loads((agent_dir / "config.json").read_text(encoding="utf-8"))


def test_agent_crawl_examples_keep_directory_without_recruiter_role(agent_dir):
	result = _run(agent_dir, "agent", "crawl", "--resume", "default")
	assert result.exit_code == 1, result.output
	parsed = json.loads(result.stdout)
	assert parsed["error"]["code"] == "INVALID_PARAM"
	actions = parsed["hints"]["next_actions"]
	assert [_crawl_argv(action, agent_dir)[2] for action in actions] == ["agent", "agent"]
	assert [shlex.split(action)[5] for action in actions] == ["--run-id", "--query"]


def test_agent_crawl_city_recovery_keeps_directory(agent_dir):
	result = _run(agent_dir, "agent", "crawl", "--query", "Python", "--resume", "default")
	assert result.exit_code == 1, result.output
	parsed = json.loads(result.stdout)
	assert parsed["error"]["code"] == "INVALID_PARAM"
	recovery_cmd = parsed["error"]["recovery_action"].split("例如 ", 1)[1]
	parts = _crawl_argv(recovery_cmd, agent_dir)
	assert parts[2:4] == ["agent", "crawl"]
	assert "--city" in parts
